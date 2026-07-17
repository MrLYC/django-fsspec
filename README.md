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

### WebDAV large uploads

The WebDAV `PUT` handler streams the request body directly from Django. To allow
uploads larger than Django's default in-memory limit, set:

```python
# Allow unlimited request bodies for streamed WebDAV uploads.
# Tune this to a value appropriate for your deployment.
DATA_UPLOAD_MAX_MEMORY_SIZE = None
```

If this setting is left at the Django default (2.5MB), `PUT` requests larger
than that limit will be rejected before django-fsspec can process them.

## Supported File Modes

| Mode | Description |
|------|-------------|
| `rb` | Read (file must exist) |
| `wb` | Write (create or overwrite) |
| `ab` | Append (create or append) |
| `xb` | Exclusive create (file must not exist) |

## Performance

Benchmarked on GitHub Actions (ubuntu-latest), using the current default 32KB block size. The table below uses CI run [29259244795](https://github.com/MrLYC/django-fsspec/actions/runs/29259244795) on commit `eb8fbc2` with `--scale ci --seed 1`. Format: average latency (throughput).

| Operation | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PostgreSQL 16 / Django 4.2 | PostgreSQL 16 / Django 5.2 | Oracle 23 |
|-----------|--------|------------------------|------------------------|----------------------------|----------------------------|-----------|
| **Write** small (100B) | 3.8ms (260/s) | 7.1ms (140/s) | 9.9ms (101/s) | 5.9ms (170/s) | 5.8ms (171/s) | 6.9ms (146/s) |
| **Write** medium (10KB) | 3.9ms (256/s) | 7.5ms (133/s) | 11.2ms (89/s) | 5.9ms (170/s) | 5.8ms (171/s) | 7.6ms (132/s) |
| **Write** large (1MB) | 11.6ms (86/s) | 45.3ms (22/s) | 71.0ms (14/s) | 36.2ms (28/s) | 37.1ms (27/s) | 37.2ms (27/s) |
| **Read** small (100B) | 1.3ms (796/s) | 2.5ms (405/s) | 2.3ms (430/s) | 2.4ms (415/s) | 2.6ms (387/s) | 3.0ms (331/s) |
| **Read** large (1MB) | 2.3ms (444/s) | 5.2ms (191/s) | 4.2ms (239/s) | 9.3ms (108/s) | 8.8ms (114/s) | 12.8ms (78/s) |
| **List** 1000 files | 4.0ms (252/s) | 7.0ms (143/s) | 6.5ms (155/s) | 5.9ms (169/s) | 5.9ms (168/s) | 8.5ms (118/s) |
| **Delete** | 3.1ms (328/s) | 6.4ms (157/s) | 8.6ms (116/s) | 4.8ms (209/s) | 4.8ms (209/s) | 5.7ms (177/s) |

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
