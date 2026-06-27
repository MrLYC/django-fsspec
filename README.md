# django-fsspec

A Django app that provides a file system interface via [fsspec](https://filesystem-spec.readthedocs.io/), backed by Django ORM.

## Features

- **fsspec compatible** — use standard `fsspec.filesystem("django")` API
- **Multi-database** — MySQL, PostgreSQL, Oracle, SQLite, and domestic databases
- **Configurable block size** — tune storage granularity per deployment
- **Optimistic locking** — safe concurrent writes with conflict detection
- **Block pool reuse** — efficient storage with free block recycling
- **Namespace isolation** — multi-tenant support via integer namespace
- **Path validation** — blacklist rules + Unicode NFC normalization
- **Implicit directories** — no directory records, derived from file paths
- **Management commands** — `fsspec_gc`, `fsspec_fsck`, `fsspec_stats`

## Quick Start

```bash
pip install django-fsspec
```

Add to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    ...
    "django_fsspec",
]
```

Run migrations:

```bash
python manage.py migrate
```

Use it:

```python
import fsspec

fs = fsspec.filesystem("django", namespace=0)

# Write
with fs.open("/hello.txt", "wb") as f:
    f.write(b"Hello World")

# Read
data = fs.cat("/hello.txt")  # b"Hello World"

# List
fs.ls("/")  # ["/hello.txt"]

# Delete
fs.rm("/hello.txt")
```

## Configuration

Add to your Django `settings.py`:

```python
# Block size in bytes (default: 256KB)
DJANGO_FSSPEC_BLOCK_SIZE = 64 * 1024

# Maximum file size in bytes (default: 2MB)
DJANGO_FSSPEC_MAX_FILE_SIZE = 2 * 1024 * 1024
```

## Supported File Modes

| Mode | Description |
|------|-------------|
| `rb` | Read (file must exist) |
| `wb` | Write (create or overwrite) |
| `ab` | Append (create or append) |
| `xb` | Exclusive create (file must not exist) |

## Documentation

- [Getting Started](docs/en/getting-started.md)
- [Configuration](docs/en/configuration.md)
- [Usage Guide](docs/en/usage.md)
- [Architecture](docs/en/architecture.md)
- [Management Commands](docs/en/management-commands.md)
- [Block Size Migration](docs/en/migration-guide.md)
- [Exceptions](docs/en/exceptions.md)

[中文文档](README_zh.md) | [Chinese Documentation](README_zh.md)

## License

MIT — see [LICENSE](LICENSE).
