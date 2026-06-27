# Usage Guide

## File Modes

| Mode | Description |
|------|-------------|
| `rb` | Read only (file must exist) |
| `wb` | Write (create or overwrite) |
| `ab` | Append (create or append to existing) |
| `xb` | Exclusive create (raises `FileExistsError` if file exists) |

## Directory Operations

Directories are implicit — no directory records are stored:

```python
fs.mkdir("/any/path")    # no-op
fs.makedirs("/a/b/c")   # no-op
fs.exists("/dir")        # True if any file starts with /dir/
fs.info("/dir")          # {"type": "directory", ...}
```

## Listing

```python
fs.ls("/", detail=False)  # ["/file.txt", "/subdir"]
fs.ls("/", detail=True)   # [{"name": "/file.txt", "size": 100, "type": "file"}, ...]
```

## Deletion

```python
fs.rm("/file.txt")                   # Delete a single file
fs.rm("/dir", recursive=True)        # Recursively delete directory
fs.rm("/dir")                        # Raises IsADirectoryError
```

## Copy and Move

```python
fs.cp_file("/src.txt", "/dst.txt")   # Copy (no block reuse)
fs.mv("/src.txt", "/dst.txt")        # Move (updates path field)
```

## Path Rules

- Must start with `/`
- No null bytes or control characters (`\x00`-`\x1f`)
- No `..` path segments
- No consecutive slashes (`//`)
- No trailing slash (except `/` for ls)
- Unicode NFC normalization applied automatically

## Checksum Verification

```python
from django_fsspec.operations import read_file

data = read_file(namespace=0, path="/test.txt", verify_checksum=True)
# Raises ValueError on checksum mismatch
```

## Transactions

Use `fs.transaction` to batch multiple operations atomically:

```python
# All-or-nothing: both files committed together, or both rolled back
with fs.transaction:
    fs.pipe("/config/a.json", b'{"key": "value"}')
    fs.pipe("/config/b.json", b'{"other": "data"}')

# Exception triggers rollback — no partial writes
try:
    with fs.transaction:
        fs.pipe("/tmp/will_rollback.txt", b"data")
        raise ValueError("oops")
except ValueError:
    pass
# /tmp/will_rollback.txt does not exist
```

Works with Django's `transaction.atomic()` too:

```python
from django.db import transaction

with transaction.atomic():
    MyModel.objects.create(name="test")
    fs.pipe("/related.txt", b"data")
    # Both the model and the file are committed or rolled back together
```

### Transaction Pitfalls

**Operations outside `fs.transaction` are not grouped.** Each `pipe`, `rm`, `mv` etc. commits independently. If the second operation fails, the first is already persisted:

```python
# NOT atomic — if pipe to b.txt fails, a.txt is already written
fs.pipe("/a.txt", b"aaa")
fs.pipe("/b.txt", b"bbb")

# Atomic — use fs.transaction or Django's transaction.atomic()
with fs.transaction:
    fs.pipe("/a.txt", b"aaa")
    fs.pipe("/b.txt", b"bbb")
```

**`commit()` and `discard()` on `DjangoFile` are no-ops.** Transaction rollback relies on the database (Django's `atomic()`), not on fsspec's file-level commit/discard pattern. This means `fs.transaction` only works with a database that supports transactions — which all supported databases do.

**Transaction isolation depends on your database.** Within `fs.transaction`, reads (`ls`, `cat`, `exists`) may or may not see concurrent writes from other connections, depending on the database's isolation level:

| Database | Default Isolation | Behavior in `fs.transaction` |
|----------|------------------|------------------------------|
| PostgreSQL | READ COMMITTED | Each query sees latest committed data |
| MySQL | REPEATABLE READ | Queries see a snapshot from transaction start |
| SQLite | SERIALIZABLE | Full isolation (single-writer) |

If you need consistent reads within a transaction, be aware that PostgreSQL may show changes committed by other connections between two queries within the same `fs.transaction`.

## Thread Safety

Each `fs.open()` returns an independent `DjangoFile` instance. Safe for multi-threaded use.

## Database Routing

Ensure all three django_fsspec tables reside on the same database. Transactions cannot span multiple databases.
