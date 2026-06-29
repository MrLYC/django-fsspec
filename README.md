# django-fsspec

A Django app that provides a file system interface via [fsspec](https://filesystem-spec.readthedocs.io/), backed by Django ORM.

![django-fsspec architecture](docs/assets/django-fsspec-architecture.png)

## Features

- **fsspec compatible** — use standard `fsspec.filesystem("django")` API
- **Multi-database** — works through Django ORM on supported relational databases
- **Configurable block size** — tune storage granularity per deployment
- **Optimistic locking** — safe concurrent writes with conflict detection
- **Append-safe API** — append mode uses the same database-backed append operation as the public API
- **Namespace partitioning** — separate path spaces via integer namespace; authorization remains the host app's responsibility
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

fs = fsspec.filesystem("django", namespace_id=1)

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

Benchmarked on GitHub Actions (ubuntu-latest), default 256KB block size. The table below uses CI run [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) on commit `eb31d73` with `--scale ci --seed 1`. Format: average latency (throughput).

| Operation | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PostgreSQL 16 / Django 4.2 | PostgreSQL 16 / Django 5.2 | Oracle 23 |
|-----------|--------|------------------------|------------------------|----------------------------|----------------------------|-----------|
| **Write** small (100B) | 4.2ms (236/s) | 8.0ms (124/s) | 7.1ms (140/s) | 6.0ms (165/s) | 6.0ms (167/s) | 6.5ms (153/s) |
| **Write** medium (10KB) | 4.5ms (223/s) | 8.4ms (119/s) | 7.5ms (133/s) | 6.1ms (164/s) | 6.0ms (168/s) | 6.9ms (145/s) |
| **Write** large (1MB) | 8.2ms (122/s) | 31.3ms (32/s) | 29.3ms (34/s) | 27.1ms (37/s) | 27.1ms (37/s) | 15.9ms (63/s) |
| **Read** small (100B) | 1.4ms (705/s) | 2.6ms (387/s) | 2.4ms (416/s) | 2.5ms (400/s) | 2.5ms (408/s) | 2.7ms (373/s) |
| **Read** large (1MB) | 1.8ms (549/s) | 4.5ms (223/s) | 4.1ms (243/s) | 8.2ms (122/s) | 8.2ms (121/s) | 5.7ms (174/s) |
| **List** 1000 files | 4.2ms (237/s) | 7.0ms (142/s) | 6.8ms (148/s) | 6.4ms (157/s) | 6.3ms (159/s) | 8.2ms (122/s) |
| **Delete** | 2.7ms (375/s) | 5.8ms (173/s) | 5.2ms (193/s) | 3.8ms (263/s) | 3.7ms (273/s) | 4.0ms (251/s) |

Full benchmark results, including concurrency and manually triggered medium seeded runs, are documented in [Benchmarks](docs/en/benchmarks.md) and available as [GitHub Actions artifacts](https://github.com/MrLYC/django-fsspec/actions).

## Documentation

- [Getting Started](docs/en/getting-started.md)
- [Configuration](docs/en/configuration.md)
- [Usage Guide](docs/en/usage.md)
- [Architecture](docs/en/architecture.md)
- [Management Commands](docs/en/management-commands.md)
- [Benchmarks](docs/en/benchmarks.md)
- [Block Size Migration](docs/en/migration-guide.md)
- [Exceptions](docs/en/exceptions.md)

[中文文档](README_zh.md) | [Chinese Documentation](README_zh.md)

## License

MIT — see [LICENSE](LICENSE).
