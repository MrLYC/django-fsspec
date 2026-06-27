"""Django settings for benchmark and E2E test runs.

Supports four database backends via DJANGO_FSSPEC_BENCH_DB env var:
  - sqlite (default)
  - mysql
  - postgres
  - oracle
"""

import os

SECRET_KEY = "benchmark-secret-key"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

BENCH_DB = os.environ.get("DJANGO_FSSPEC_BENCH_DB", "sqlite")

if BENCH_DB == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("MYSQL_DATABASE", "fsspec_test"),
            "USER": os.environ.get("MYSQL_USER", "fsspec"),
            "PASSWORD": os.environ.get("MYSQL_PASSWORD", "fsspec_test"),
            "HOST": os.environ.get("MYSQL_HOST", "127.0.0.1"),
            "PORT": os.environ.get("MYSQL_PORT", "13306"),
            "OPTIONS": {
                "charset": "utf8mb4",
            },
        }
    }
elif BENCH_DB == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "fsspec_test"),
            "USER": os.environ.get("POSTGRES_USER", "fsspec"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "fsspec_test"),
            "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.environ.get("POSTGRES_PORT", "15432"),
        }
    }
elif BENCH_DB == "oracle":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.oracle",
            "NAME": os.environ.get("ORACLE_DSN", "127.0.0.1:1521/FREEPDB1"),
            "USER": os.environ.get("ORACLE_USER", "fsspec"),
            "PASSWORD": os.environ.get("ORACLE_PASSWORD", "fsspec_test"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.path.join(
                os.path.dirname(__file__), "bench.sqlite3"
            ),
        }
    }

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django_fsspec",
]
