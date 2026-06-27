# Architecture

## Three-Table Design

| Table | Purpose |
|-------|---------|
| `FileNode` | File metadata (path, size, checksum, version) |
| `StorageBlock` | Storage chunks (binary data, size, checksum, free flag) |
| `FileBlock` | File-to-block mapping (file ID, block ID, sequence) |

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

## Block Pool

On delete/overwrite, old blocks are marked `is_free=True`. New writes reuse free blocks via batch `UPDATE`, falling back to `bulk_create` for shortfalls.

## Optimistic Locking

`FileNode.version` implements optimistic locking:

```sql
UPDATE file_node SET ... WHERE pk=X AND version=old_version
```

If zero rows affected, another process modified the file — raises `FileConflictError`.

## Write Flow

1. `open(path, "wb")` → creates `DjangoFile`
2. `write(data)` → buffered by `AbstractBufferedFile`
3. `close()` → single transaction: chunk → allocate blocks → create mappings → update FileNode

## Read Flow

1. `open(path, "rb")` → reads `FileNode.block_size`
2. `read()`/`seek()` → triggers `_fetch_range(start, end)`
3. Arithmetic block positioning: `start_block = start // block_size`

## Performance Baseline

Benchmarked on GitHub Actions (ubuntu-latest) with default 256KB block size. Results from CI run 2026-06-27.

### Write Operations

| Operation | SQLite | MySQL 5.7 | MySQL 8.0 | PG 9.6 | PG 16 | Oracle 23 |
|-----------|--------|-----------|-----------|--------|-------|-----------|
| write_small (100B) | 2.61ms / 383 ops/s | — | 4.47ms / 224 ops/s | — | 3.08ms / 325 ops/s | 4.33ms / 231 ops/s |
| write_medium (10KB) | 2.88ms / 347 ops/s | — | 4.86ms / 206 ops/s | — | 3.17ms / 315 ops/s | 3.73ms / 268 ops/s |
| write_large (1MB) | 6.81ms / 147 ops/s | — | 27.29ms / 37 ops/s | — | 26.07ms / 38 ops/s | 11.29ms / 89 ops/s |
| overwrite | 4.10ms / 244 ops/s | — | 8.88ms / 113 ops/s | — | 6.77ms / 148 ops/s | 7.29ms / 137 ops/s |

### Read Operations

| Operation | SQLite | MySQL 8.0 | PG 16 | Oracle 23 |
|-----------|--------|-----------|-------|-----------|
| read_small (100B) | 1.32ms / 755 ops/s | 2.46ms / 406 ops/s | 2.58ms / 387 ops/s | 2.65ms / 378 ops/s |
| read_large (1MB) | 1.78ms / 561 ops/s | 4.89ms / 204 ops/s | 10.55ms / 95 ops/s | 5.66ms / 177 ops/s |
| seek_read | 1.48ms / 675 ops/s | 3.19ms / 314 ops/s | 4.93ms / 203 ops/s | 3.88ms / 258 ops/s |

### Directory & Delete Operations

| Operation | SQLite | MySQL 8.0 | PG 16 | Oracle 23 |
|-----------|--------|-----------|-------|-----------|
| ls_flat (1000 files) | 2.72ms / 367 ops/s | 5.14ms / 195 ops/s | 4.15ms / 241 ops/s | 6.31ms / 158 ops/s |
| ls_nested (100 dirs) | 2.29ms / 436 ops/s | 4.05ms / 247 ops/s | 3.87ms / 258 ops/s | 3.43ms / 292 ops/s |
| delete | 3.10ms / 323 ops/s | 5.54ms / 181 ops/s | 3.86ms / 259 ops/s | 3.90ms / 256 ops/s |

### Key Observations

- **SQLite** is fastest across all operations (no network overhead)
- **PostgreSQL** excels at writes but is slower on large reads (TOAST overhead)
- **Oracle** has consistent low-latency performance with occasional P99 spikes
- **MySQL** is the slowest for writes but solid for reads
- All databases handle the target workload (30K small files) well — even the slowest write (MySQL large file) at 37 ops/s can write 30K files in ~13 minutes

## Development Setup

```bash
git clone https://github.com/MrLYC/django-fsspec.git
cd django-fsspec
pip install -e ".[dev]"
python -m pytest django_fsspec/tests/ -v --cov=django_fsspec
```
