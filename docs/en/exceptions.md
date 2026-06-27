# Exceptions

## Custom Exceptions

### DjangoFsspecError

Base class for all django-fsspec exceptions.

```python
from django_fsspec.exceptions import DjangoFsspecError
```

### FileConflictError

Optimistic lock conflict. Raised when another process modified the file between your read and write.

```python
from django_fsspec.exceptions import FileConflictError

try:
    write_file(0, "/config.json", new_data)
except FileConflictError:
    # File was modified by another process — re-read and retry
    pass
```

### PathValidationError

Path validation failure. The path contains illegal characters, `..` traversal, consecutive slashes, etc.

```python
from django_fsspec.exceptions import PathValidationError

try:
    write_file(0, "../etc/passwd", b"data")
except PathValidationError:
    # Invalid path
    pass
```

### FileTooLargeError

File size exceeds `DJANGO_FSSPEC_MAX_FILE_SIZE`.

## Built-in Exceptions

| Exception | Trigger |
|-----------|---------|
| `FileNotFoundError` | Reading or deleting a non-existent file |
| `FileExistsError` | Writing in `xb` mode when file exists; `mv` target exists |
| `ValueError` | Unsupported file mode (e.g., `r+b`); checksum mismatch with `verify_checksum=True` |
| `IsADirectoryError` | `rm` on a directory without `recursive=True` |
