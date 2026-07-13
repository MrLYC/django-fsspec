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
- **Management commands** — `fsspec_migrate`, `fsspec_gc`, `fsspec_fsck`, `fsspec_repair`, `fsspec_rechunk`, `fsspec_stats`

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

When using this outside a running Django process (for example, in a standalone
script, worker, or notebook), configure Django first:

```python
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "your_project.settings")
django.setup()
```

## Configuration

Add to your Django `settings.py`:

```python
# Block size in bytes (default: 32KB)
DJANGO_FSSPEC_BLOCK_SIZE = 32 * 1024

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

Benchmarked on GitHub Actions (ubuntu-latest), using the historical 256KB block size. The table below uses CI run [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) on commit `2236341` with `--scale ci --seed 1`. Format: average latency (throughput).

| Operation | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PostgreSQL 16 / Django 4.2 | PostgreSQL 16 / Django 5.2 | Oracle 23 |
|-----------|--------|------------------------|------------------------|----------------------------|----------------------------|-----------|
| **Write** small (100B) | 4.2ms (237/s) | 7.7ms (129/s) | 7.2ms (138/s) | 6.6ms (152/s) | 6.4ms (157/s) | 6.3ms (159/s) |
| **Write** medium (10KB) | 4.4ms (228/s) | 8.1ms (124/s) | 7.7ms (130/s) | 6.6ms (153/s) | 6.2ms (160/s) | 6.5ms (153/s) |
| **Write** large (1MB) | 8.4ms (118/s) | 30.2ms (33/s) | 30.0ms (33/s) | 33.4ms (30/s) | 29.9ms (33/s) | 14.0ms (71/s) |
| **Read** small (100B) | 1.4ms (693/s) | 2.6ms (381/s) | 2.4ms (409/s) | 3.0ms (338/s) | 2.5ms (393/s) | 2.8ms (355/s) |
| **Read** large (1MB) | 1.9ms (536/s) | 4.6ms (219/s) | 4.3ms (235/s) | 9.5ms (106/s) | 9.5ms (105/s) | 5.3ms (188/s) |
| **List** 1000 files | 4.3ms (234/s) | 7.1ms (141/s) | 6.8ms (147/s) | 7.0ms (144/s) | 6.5ms (153/s) | 7.6ms (132/s) |
| **Delete** | 3.4ms (294/s) | 7.0ms (143/s) | 6.4ms (156/s) | 5.2ms (193/s) | 5.0ms (199/s) | 5.1ms (196/s) |

Full benchmark results, including concurrency and manually triggered seeded runs, are documented in [Benchmarks](docs/en/benchmarks.md) and available as [GitHub Actions artifacts](https://github.com/MrLYC/django-fsspec/actions).

## Documentation

- [Getting Started](docs/en/getting-started.md)
- [Configuration](docs/en/configuration.md)
- [Usage Guide](docs/en/usage.md)
- [Architecture](docs/en/architecture.md)
- [Management Commands](docs/en/management-commands.md)
- [Operations Runbook](docs/en/operations-runbook.md)
- [Benchmarks](docs/en/benchmarks.md)
- [Block Size Operations](docs/en/block-size.md)
- [Local Cache Guide](docs/en/local-cache.md)
- [Roadmap](docs/en/roadmap.md)
- [Release Checklist](docs/en/release-checklist.md)
- [Exceptions](docs/en/exceptions.md)

[中文文档](README_zh.md) | [Chinese Documentation](README_zh.md)

## License

MIT — see [LICENSE](LICENSE).
