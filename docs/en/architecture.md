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
| `management/commands/` | Operational tooling: `fsspec_stats`, `fsspec_fsck`, `fsspec_repair`, `fsspec_rechunk`, and `fsspec_gc` |

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

| Command / Hook | Purpose |
|----------------|---------|
| `fsspec_stats` | Reports namespace count, file count/size, used/free blocks, and mapping count |
| `fsspec_fsck` | Verifies block/file metadata, path-tree conflicts, invalid persisted paths and node types, directory block mappings, shared blocks, and mappings to free blocks; supports JSON findings with severity |
| `fsspec_repair` | Best-effort repair for derived metadata, live/free block flags, sequence gaps, impossible directory mappings, unreferenced used blocks, and explicit path-conflict recovery |
| `fsspec_rechunk` | Rewrites healthy files to a target block size with dry-run, filters, per-file transactions, and skip/abort error handling |
| `fsspec_gc` | Deletes free `StorageBlock` rows, optionally retaining recent free rows for inspection |
| `check_block_size_consistency` | Emits a Django warning when stored block sizes differ from the configured block size |

## Performance Baseline

Benchmarked on GitHub Actions (ubuntu-latest) with the historical 256KB block size. Format: average latency / throughput. Source: CI run [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170), commit `eb31d73`, `--scale ci --seed 1`.

### Write Operations

| Operation | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PG 16 / Django 4.2 | PG 16 / Django 5.2 | Oracle 23 |
|-----------|--------|------------------------|------------------------|--------------------|--------------------|-----------|
| write_small (100B) | 4.23ms / 236 ops/s | 8.04ms / 124 ops/s | 7.13ms / 140 ops/s | 6.05ms / 165 ops/s | 5.98ms / 167 ops/s | 6.53ms / 153 ops/s |
| write_medium (10KB) | 4.47ms / 223 ops/s | 8.39ms / 119 ops/s | 7.51ms / 133 ops/s | 6.08ms / 164 ops/s | 5.97ms / 168 ops/s | 6.91ms / 145 ops/s |
| write_large (1MB) | 8.21ms / 122 ops/s | 31.28ms / 32 ops/s | 29.34ms / 34 ops/s | 27.14ms / 37 ops/s | 27.13ms / 37 ops/s | 15.94ms / 63 ops/s |
| overwrite | 4.86ms / 206 ops/s | 10.18ms / 98 ops/s | 9.15ms / 109 ops/s | 7.47ms / 134 ops/s | 7.50ms / 133 ops/s | 8.06ms / 124 ops/s |

### Read Operations

| Operation | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PG 16 / Django 4.2 | PG 16 / Django 5.2 | Oracle 23 |
|-----------|--------|------------------------|------------------------|--------------------|--------------------|-----------|
| read_small (100B) | 1.42ms / 705 ops/s | 2.58ms / 387 ops/s | 2.40ms / 416 ops/s | 2.50ms / 400 ops/s | 2.45ms / 408 ops/s | 2.68ms / 373 ops/s |
| read_large (1MB) | 1.82ms / 549 ops/s | 4.48ms / 223 ops/s | 4.12ms / 243 ops/s | 8.18ms / 122 ops/s | 8.23ms / 121 ops/s | 5.73ms / 174 ops/s |
| seek_read | 1.56ms / 642 ops/s | 3.34ms / 299 ops/s | 3.05ms / 328 ops/s | 4.83ms / 207 ops/s | 4.63ms / 216 ops/s | 3.85ms / 260 ops/s |

### Directory & Delete Operations

| Operation | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PG 16 / Django 4.2 | PG 16 / Django 5.2 | Oracle 23 |
|-----------|--------|------------------------|------------------------|--------------------|--------------------|-----------|
| ls_flat (1000 files) | 4.21ms / 237 ops/s | 7.02ms / 142 ops/s | 6.78ms / 148 ops/s | 6.35ms / 157 ops/s | 6.30ms / 159 ops/s | 8.21ms / 122 ops/s |
| ls_nested (100 dirs) | 3.95ms / 253 ops/s | 6.10ms / 164 ops/s | 5.84ms / 171 ops/s | 6.28ms / 159 ops/s | 6.21ms / 161 ops/s | 5.60ms / 179 ops/s |
| delete | 2.67ms / 375 ops/s | 5.77ms / 173 ops/s | 5.18ms / 193 ops/s | 3.81ms / 263 ops/s | 3.67ms / 273 ops/s | 3.98ms / 251 ops/s |

### Key Observations

- **SQLite** remains fastest for local reads and seek reads, but concurrent writes and mixed read/write workloads surface SQLite's expected `database is locked` behavior.
- **MySQL 8.0** improves under Django 5.2 for most measured CI operations compared with Django 4.2, with large writes still the slowest path.
- **PostgreSQL 16** is stable across Django 4.2 and 5.2; large reads remain slower than SQLite and MySQL in this CI environment.
- **Oracle 23** has the fastest large-write result among the networked databases in this run and consistent read latency.
- Full CI scenario data and manually triggered seeded-table results are in [Benchmarks](benchmarks.md).

## Development Setup

```bash
git clone https://github.com/MrLYC/django-fsspec.git
cd django-fsspec
pip install -e ".[dev]"
python -m pytest tests/ -v --cov=django_fsspec
```
