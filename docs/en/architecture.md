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

## Development Setup

```bash
git clone https://github.com/MrLYC/django-fsspec.git
cd django-fsspec
pip install -e ".[dev]"
python -m pytest django_fsspec/tests/ -v --cov=django_fsspec
```
