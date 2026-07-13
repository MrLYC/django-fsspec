# Architecture

![django-fsspec architecture diagram](../assets/django-fsspec-architecture.png)

`django-fsspec` implements an fsspec filesystem by translating file operations
into Django ORM queries and transactions. The public API is fsspec-compatible;
the storage backend is a small relational schema made of file metadata, reusable
binary blocks, and file-to-block mappings.

## Module Map

| Module | Responsibility |
|--------|----------------|
| `fs.py` | `DjangoFileSystem` fsspec adapter, namespace partitioning, directory/file API, and fsspec transaction integration |
| `buffer.py` | `DjangoFile` bridge to `AbstractBufferedFile`, buffered uploads, append bootstrap, and range reads |
| `operations.py` | Transactional file primitives: validate, chunk, allocate/release blocks, read, list, copy, move, and delete |
| `models.py` | ORM schema and storage settings helpers |
| `validators.py` | Path validation and Unicode NFC normalization |
| `checks.py` | Django system/startup checks for block-size drift |
| `management/commands/` | Operational tooling: `fsspec_migrate`, `fsspec_stats`, `fsspec_fsck`, `fsspec_repair`, `fsspec_rechunk`, and `fsspec_gc` |

## Three-Table Model

| Table | Purpose |
|-------|---------|
| `FileNode` | Per-file metadata: `namespace`, `path`, `size`, `block_size`, checksum, content type, version, timestamps |
| `StorageBlock` | Binary chunk storage: data, size, checksum, and `is_free` pool flag |
| `FileBlock` | Ordered mapping from a file to storage blocks via `sequence` |

`FileNode` is unique on `(namespace, path)`, so the same path can exist in
different tenant namespaces. `FileBlock` is unique on `(file, sequence)` and
ordered by `sequence`, which makes block reconstruction deterministic.

## Request Path

1. fsspec loads the `django` protocol entry point and instantiates
   `DjangoFileSystem(namespace_id=...)`.
2. `DjangoFileSystem` strips the protocol, applies namespace scope, and delegates
   to `operations.py` for metadata, listing, copy, move, and delete operations.
3. `open()` returns a `DjangoFile`, which uses fsspec's buffered-file contract.
4. `DjangoFile` calls `operations.read_file_range()` for reads and
   `operations.write_file()` or `operations.create_file_exclusive()` on final
   upload.
5. `operations.py` validates paths, computes checksums, chunks bytes, and writes
   the ORM rows inside Django transactions.

## Implicit Directories

No directory table. Directories are derived from file path prefixes using database-side pushdown:

```python
FileNode.objects.filter(path__startswith=prefix).annotate(
    relative=Substr("path", prefix_len + 1),
    slash_pos=StrIndex("relative", Value("/")),
    next_part=Case(
        When(slash_pos=0, then="relative"),
        default=Substr("relative", 1, F("slash_pos") - 1),
    ),
).values_list("next_part", flat=True).distinct()
```

The database returns only deduplicated next-level entries — O(children) not O(total files).

`find()` uses the same prefix idea to fetch files below a path. When `maxdepth`
is provided, it filters depth in Python after the prefix query; `ls()` keeps the
next-child projection in the database.

## Write Flow

1. `open(path, "wb")`, `pipe()`, or `touch()` creates a `DjangoFile`.
2. `DjangoFile` buffers bytes through `AbstractBufferedFile`; append mode first
   reads the existing file into the upload buffer.
3. On final upload, `operations.write_file()` validates the path, enforces
   `DJANGO_FSSPEC_MAX_FILE_SIZE`, computes a SHA-256 file checksum, and splits
   data by the current `DJANGO_FSSPEC_BLOCK_SIZE`.
4. Inside `transaction.atomic()`, an existing file releases its old blocks to the
   free pool and updates `FileNode` with an optimistic `version` predicate; a new
   file creates a fresh `FileNode`.
5. `_allocate_blocks()` creates fresh `StorageBlock` rows. Free rows are kept
   for inspection or later removal by `fsspec_gc`.
6. `FileBlock.bulk_create()` stores the ordered file-to-block mapping.

Exclusive create mode (`"xb"`) routes through `create_file_exclusive()` and
raises `FileExistsError` if the `(namespace, path)` already exists.

## Read Flow

1. `open(path, "rb")` resolves file metadata and file size.
2. `read()` or `seek()` calls `DjangoFile._fetch_range(start, end)`.
3. `operations.read_file_range()` uses the file's stored `block_size` to compute
   `start_block` and `end_block`, selects only those `FileBlock` rows, joins the
   related `StorageBlock` rows, and trims the concatenated bytes to the requested
   range.
4. `operations.read_file(..., verify_checksum=True)` can verify both block-level
   and file-level SHA-256 checksums.

## Free Blocks

On delete, overwrite, recursive directory removal, or rechunking, old blocks are
marked `is_free=True` and their `FileBlock` mappings are removed. New writes
always create fresh `StorageBlock` rows; this avoids cross-database locking
assumptions and keeps the write path correctness-first. Use `fsspec_gc` to
permanently delete free blocks after inspection or retention windows.

## Optimistic Locking

`FileNode.version` implements optimistic locking:

```sql
UPDATE file_node SET ... WHERE pk=X AND version=old_version
```

If zero rows affected, another process modified the file — raises `FileConflictError`.

## Transactions

`DjangoTransaction` maps fsspec transactions onto `django.db.transaction.atomic()`.
In Django autocommit mode it opens a database transaction; inside an existing
transaction it creates a savepoint. On discard, it marks the atomic block for
rollback. Nested fsspec transactions are rejected with `RuntimeError`.

Transaction state is stored in thread-local state on the filesystem instance, so
sharing a `DjangoFileSystem` object across threads does not make one thread's
transaction capture another thread's normal writes.

## Block Size Changes

Each `FileNode` stores the block size used when it was written, so files with
different block sizes can coexist and range reads still work. The default is
32KB. Changing `DJANGO_FSSPEC_BLOCK_SIZE` only affects new writes and does not
require a migration for correctness. To rewrite existing files, run
`fsspec_rechunk`; the Django system check `django_fsspec.W001` warns when
persisted files differ from the current setting.

## Operational Tooling

For incident and maintenance command sequences, see the
[Operations Runbook](operations-runbook.md).

| Command / Hook | Purpose |
|----------------|---------|
| `fsspec_migrate` | Copies files between fsspec-compatible filesystems with dry-run, checksum verification, conflict policies, and manifest resume |
| `fsspec_stats` | Reports namespace count, file count/size, used/free blocks, and mapping count |
| `fsspec_fsck` | Verifies block/file metadata, path-tree conflicts, invalid persisted paths and node types, directory block mappings, shared blocks, and mappings to free blocks; supports JSON findings with severity |
| `fsspec_repair` | Best-effort repair for derived metadata, live/free block flags, sequence gaps, impossible directory mappings, unreferenced used blocks, and explicit path-conflict recovery |
| `fsspec_rechunk` | Rewrites healthy files to a target block size with dry-run, filters, per-file transactions, and skip/abort error handling |
| `fsspec_gc` | Deletes free `StorageBlock` rows, optionally retaining recent free rows for inspection |
| `check_block_size_consistency` | Emits a Django warning when stored block sizes differ from the configured block size |

## Performance Baseline

Benchmarked on GitHub Actions (ubuntu-latest) with the current default 32KB block size. Format: average latency / throughput. Source: CI run [29259244795](https://github.com/MrLYC/django-fsspec/actions/runs/29259244795), commit `eb8fbc2`, `--scale ci --seed 1`.

### Write Operations

| Operation | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PG 16 / Django 4.2 | PG 16 / Django 5.2 | Oracle 23 |
|-----------|--------|------------------------|------------------------|--------------------|--------------------|-----------|
| write_small (100B) | 3.85ms / 260 ops/s | 7.13ms / 140 ops/s | 9.93ms / 101 ops/s | 5.89ms / 170 ops/s | 5.84ms / 171 ops/s | 6.87ms / 146 ops/s |
| write_medium (10KB) | 3.90ms / 256 ops/s | 7.52ms / 133 ops/s | 11.20ms / 89 ops/s | 5.89ms / 170 ops/s | 5.84ms / 171 ops/s | 7.55ms / 132 ops/s |
| write_large (1MB) | 11.65ms / 86 ops/s | 45.30ms / 22 ops/s | 71.00ms / 14 ops/s | 36.16ms / 28 ops/s | 37.15ms / 27 ops/s | 37.18ms / 27 ops/s |
| overwrite | 5.17ms / 193 ops/s | 10.75ms / 93 ops/s | 13.67ms / 73 ops/s | 8.60ms / 116 ops/s | 8.78ms / 114 ops/s | 9.99ms / 100 ops/s |

### Read Operations

| Operation | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PG 16 / Django 4.2 | PG 16 / Django 5.2 | Oracle 23 |
|-----------|--------|------------------------|------------------------|--------------------|--------------------|-----------|
| read_small (100B) | 1.26ms / 796 ops/s | 2.47ms / 405 ops/s | 2.33ms / 430 ops/s | 2.41ms / 415 ops/s | 2.58ms / 387 ops/s | 3.02ms / 331 ops/s |
| read_large (1MB) | 2.25ms / 444 ops/s | 5.24ms / 191 ops/s | 4.19ms / 239 ops/s | 9.29ms / 108 ops/s | 8.80ms / 114 ops/s | 12.81ms / 78 ops/s |
| seek_read | 1.32ms / 756 ops/s | 2.57ms / 390 ops/s | 2.45ms / 408 ops/s | 2.75ms / 363 ops/s | 2.73ms / 367 ops/s | 3.27ms / 306 ops/s |

### Directory & Delete Operations

| Operation | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PG 16 / Django 4.2 | PG 16 / Django 5.2 | Oracle 23 |
|-----------|--------|------------------------|------------------------|--------------------|--------------------|-----------|
| ls_flat (1000 files) | 3.97ms / 252 ops/s | 7.01ms / 143 ops/s | 6.45ms / 155 ops/s | 5.92ms / 169 ops/s | 5.95ms / 168 ops/s | 8.50ms / 118 ops/s |
| ls_nested (100 dirs) | 3.71ms / 270 ops/s | 5.82ms / 172 ops/s | 5.35ms / 187 ops/s | 6.39ms / 156 ops/s | 6.02ms / 166 ops/s | 5.84ms / 171 ops/s |
| delete | 3.05ms / 328 ops/s | 6.39ms / 157 ops/s | 8.61ms / 116 ops/s | 4.78ms / 209 ops/s | 4.78ms / 209 ops/s | 5.66ms / 177 ops/s |

### Key Observations

- **SQLite** remains fastest for local small reads, seek reads, and bounded listings, but concurrent writes and mixed read/write workloads surface SQLite's expected `database is locked` behavior.
- **MySQL 8.0** is viable for concurrent writes, but Django 5.2 was slower than Django 4.2 on write-heavy CI scenarios in this run.
- **PostgreSQL 16** is the most stable server backend across Django 4.2 and 5.2 in this CI run, with small reads and writes clustered closely.
- **Oracle 23** is stable on small writes and directory operations, but its 1MB reads and writes were slower than the previous documented run.
- Full CI scenario data and manually triggered seeded-table results are in [Benchmarks](benchmarks.md).

## Development Setup

```bash
git clone https://github.com/MrLYC/django-fsspec.git
cd django-fsspec
uv sync --extra dev --frozen
uv run python -m pytest tests/ -v --cov=django_fsspec
```
