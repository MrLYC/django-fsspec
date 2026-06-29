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
| `migrations_ops.py` | `RechunkOperation` for rewriting existing files to a new block size |
| `management/commands/` | Operational tooling: `fsspec_stats`, `fsspec_fsck`, and `fsspec_gc` |

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
   `DjangoFileSystem(namespace=...)`.
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
5. `_allocate_blocks()` claims free `StorageBlock` rows when possible, rewrites
   their data/checksum, and creates new blocks for any shortfall.
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

## Block Size Changes

Each `FileNode` stores the block size used when it was written, so files with
different block sizes can coexist and range reads still work. Changing
`DJANGO_FSSPEC_BLOCK_SIZE` only affects new writes. To rewrite existing files,
add a migration with `RechunkOperation(new_block_size=...)`; the Django system
check `django_fsspec.W001` warns when persisted files differ from the current
setting.

## Operational Tooling

| Command / Hook | Purpose |
|----------------|---------|
| `fsspec_stats` | Reports namespace count, file count/size, used/free blocks, and mapping count |
| `fsspec_fsck` | Verifies block checksums, block sizes, file checksums, file sizes, sequence continuity, and mappings to free blocks |
| `fsspec_gc` | Deletes free `StorageBlock` rows, optionally retaining recent free rows for inspection |
| `check_block_size_consistency` | Emits a Django warning when stored block sizes differ from the configured block size |

## Performance Baseline

Benchmarked on GitHub Actions (ubuntu-latest) with default 256KB block size. Format: avg latency / throughput. Updated automatically by CI.

### Write Operations

| Operation | SQLite | MySQL 8.0 | PG 16 | Oracle 23 |
|-----------|--------|-----------|-------|-----------|
| write_small (100B) | 2.22ms / 450 ops/s | 4.43ms / 226 ops/s | 3.00ms / 333 ops/s | 3.20ms / 313 ops/s |
| write_medium (10KB) | 2.31ms / 433 ops/s | 4.90ms / 204 ops/s | 3.11ms / 321 ops/s | 3.61ms / 277 ops/s |
| write_large (1MB) | 6.63ms / 151 ops/s | 28.03ms / 36 ops/s | 24.00ms / 42 ops/s | 11.30ms / 88 ops/s |
| overwrite | 3.76ms / 266 ops/s | 9.14ms / 109 ops/s | 6.27ms / 159 ops/s | 7.17ms / 139 ops/s |

### Read Operations

| Operation | SQLite | MySQL 8.0 | PG 16 | Oracle 23 |
|-----------|--------|-----------|-------|-----------|
| read_small (100B) | 1.19ms / 841 ops/s | 2.43ms / 411 ops/s | 2.32ms / 431 ops/s | 2.56ms / 390 ops/s |
| read_large (1MB) | 1.67ms / 598 ops/s | 4.48ms / 223 ops/s | 7.77ms / 129 ops/s | 5.48ms / 183 ops/s |
| seek_read | 1.36ms / 738 ops/s | 3.20ms / 312 ops/s | 4.59ms / 218 ops/s | 3.69ms / 271 ops/s |

### Directory & Delete Operations

| Operation | SQLite | MySQL 8.0 | PG 16 | Oracle 23 |
|-----------|--------|-----------|-------|-----------|
| ls_flat (1000 files) | 2.54ms / 394 ops/s | 5.94ms / 168 ops/s | 3.94ms / 254 ops/s | 5.97ms / 167 ops/s |
| ls_nested (100 dirs) | 2.17ms / 460 ops/s | 4.04ms / 247 ops/s | 3.55ms / 282 ops/s | 3.56ms / 281 ops/s |
| delete | 2.42ms / 413 ops/s | 5.33ms / 188 ops/s | 3.52ms / 284 ops/s | 3.73ms / 268 ops/s |

### Key Observations

- **SQLite** is fastest across all operations (no network overhead)
- **MySQL 8.0** is slower on large writes but remains within the target workload envelope
- **PostgreSQL 16** excels at small writes but is slower on large reads (TOAST overhead)
- **Oracle** has consistent low-latency performance
- All databases handle the target workload (30K small files) well — even the slowest write (MySQL 8.0 large file) at 36 ops/s can write 30K files in ~14 minutes

## Development Setup

```bash
git clone https://github.com/MrLYC/django-fsspec.git
cd django-fsspec
pip install -e ".[dev]"
python -m pytest django_fsspec/tests/ -v --cov=django_fsspec
```
