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

## Performance

Benchmarked on GitHub Actions (ubuntu-latest), default 256KB block size.

| Operation | SQLite | MySQL 8.0 | PostgreSQL 16 | Oracle 23 |
|-----------|--------|-----------|---------------|-----------|
| **Write** small (100B) | 2.2ms (455/s) | 4.3ms (230/s) | 3.2ms (316/s) | 3.2ms (314/s) |
| **Write** medium (10KB) | 2.3ms (438/s) | 4.8ms (207/s) | 3.2ms (312/s) | 3.7ms (270/s) |
| **Write** large (1MB) | 6.2ms (161/s) | 26.9ms (37/s) | 26.2ms (38/s) | 11.0ms (91/s) |
| **Read** small (100B) | 1.2ms (851/s) | 2.4ms (417/s) | 2.3ms (434/s) | 2.5ms (399/s) |
| **Read** large (1MB) | 1.7ms (597/s) | 4.7ms (212/s) | 10.8ms (93/s) | 5.4ms (186/s) |
| **List** 1000 files | 2.5ms (395/s) | 5.1ms (197/s) | 4.4ms (228/s) | 6.0ms (165/s) |
| **Delete** | 2.3ms (432/s) | 5.2ms (192/s) | 4.0ms (252/s) | 4.0ms (253/s) |

Full benchmark results (including MySQL 5.7, PG 9.6, concurrency tests) are collected by CI on every push and available as [GitHub Actions artifacts](https://github.com/MrLYC/django-fsspec/actions).

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
