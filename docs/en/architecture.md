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

Benchmarked on GitHub Actions (ubuntu-latest) with default 256KB block size. Format: avg latency / throughput. Updated automatically by CI.

### Write Operations

| Operation | SQLite | MySQL 5.7 | MySQL 8.0 | PG 9.6 | PG 16 | Oracle 23 |
|-----------|--------|-----------|-----------|--------|-------|-----------|
| write_small (100B) | 2.22ms / 450 ops/s | 3.53ms / 283 ops/s | 4.43ms / 226 ops/s | 2.89ms / 346 ops/s | 3.00ms / 333 ops/s | 3.20ms / 313 ops/s |
| write_medium (10KB) | 2.31ms / 433 ops/s | 3.77ms / 265 ops/s | 4.90ms / 204 ops/s | 3.00ms / 334 ops/s | 3.11ms / 321 ops/s | 3.61ms / 277 ops/s |
| write_large (1MB) | 6.63ms / 151 ops/s | 22.64ms / 44 ops/s | 28.03ms / 36 ops/s | 28.26ms / 35 ops/s | 24.00ms / 42 ops/s | 11.30ms / 88 ops/s |
| overwrite | 3.76ms / 266 ops/s | 7.30ms / 137 ops/s | 9.14ms / 109 ops/s | 6.32ms / 158 ops/s | 6.27ms / 159 ops/s | 7.17ms / 139 ops/s |

### Read Operations

| Operation | SQLite | MySQL 5.7 | MySQL 8.0 | PG 9.6 | PG 16 | Oracle 23 |
|-----------|--------|-----------|-----------|--------|-------|-----------|
| read_small (100B) | 1.19ms / 841 ops/s | 2.35ms / 426 ops/s | 2.43ms / 411 ops/s | 2.17ms / 460 ops/s | 2.32ms / 431 ops/s | 2.56ms / 390 ops/s |
| read_large (1MB) | 1.67ms / 598 ops/s | 4.04ms / 248 ops/s | 4.48ms / 223 ops/s | 10.88ms / 92 ops/s | 7.77ms / 129 ops/s | 5.48ms / 183 ops/s |
| seek_read | 1.36ms / 738 ops/s | 2.99ms / 334 ops/s | 3.20ms / 312 ops/s | 4.70ms / 213 ops/s | 4.59ms / 218 ops/s | 3.69ms / 271 ops/s |

### Directory & Delete Operations

| Operation | SQLite | MySQL 5.7 | MySQL 8.0 | PG 9.6 | PG 16 | Oracle 23 |
|-----------|--------|-----------|-----------|--------|-------|-----------|
| ls_flat (1000 files) | 2.54ms / 394 ops/s | 8.04ms / 124 ops/s | 5.94ms / 168 ops/s | 4.06ms / 246 ops/s | 3.94ms / 254 ops/s | 5.97ms / 167 ops/s |
| ls_nested (100 dirs) | 2.17ms / 460 ops/s | 5.46ms / 183 ops/s | 4.04ms / 247 ops/s | 3.90ms / 256 ops/s | 3.55ms / 282 ops/s | 3.56ms / 281 ops/s |
| delete | 2.42ms / 413 ops/s | 4.55ms / 220 ops/s | 5.33ms / 188 ops/s | 3.59ms / 278 ops/s | 3.52ms / 284 ops/s | 3.73ms / 268 ops/s |

### Key Observations

- **SQLite** is fastest across all operations (no network overhead)
- **MySQL 5.7 vs 8.0**: 5.7 is faster on writes/reads; 8.0 is ~50% faster on directory listing (query optimizer improvements)
- **PG 9.6 vs 16**: PG 16 is faster on large reads (~30% improvement); other operations are similar
- **PostgreSQL** excels at small writes but is slower on large reads (TOAST overhead)
- **Oracle** has consistent low-latency performance
- All databases handle the target workload (30K small files) well — even the slowest write (MySQL 8.0 large file) at 36 ops/s can write 30K files in ~14 minutes

## Development Setup

```bash
git clone https://github.com/MrLYC/django-fsspec.git
cd django-fsspec
pip install -e ".[dev]"
python -m pytest django_fsspec/tests/ -v --cov=django_fsspec
```
