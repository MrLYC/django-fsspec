"""Django settings for benchmark runs.

Supports three database backends via DJANGO_FSSPEC_BENCH_DB env var:
  - sqlite (default)
  - mysql
  - postgres
"""

import os

SECRET_KEY = "benchmark-secret-key"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

BENCH_DB = os.environ.get("DJANGO_FSSPEC_BENCH_DB", "sqlite")

if BENCH_DB == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": "fsspec_test",
            "USER": "fsspec",
            "PASSWORD": "fsspec_test",
            "HOST": "127.0.0.1",
            "PORT": "13306",
            "OPTIONS": {
                "charset": "utf8mb4",
            },
        }
    }
elif BENCH_DB == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "fsspec_test",
            "USER": "fsspec",
            "PASSWORD": "fsspec_test",
            "HOST": "127.0.0.1",
            "PORT": "15432",
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
