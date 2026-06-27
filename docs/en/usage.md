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

## Thread Safety

Each `fs.open()` returns an independent `DjangoFile` instance. Safe for multi-threaded use.

## Database Routing

Ensure all three django_fsspec tables reside on the same database. Transactions cannot span multiple databases.
