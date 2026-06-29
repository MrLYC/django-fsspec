"""Shared Django settings for tests, e2e runs, benchmarks, and demo commands."""

import os

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

SECRET_KEY = "demo-secret-key-do-not-use-in-production"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ROOT_URLCONF = "demo.urls"

BENCH_DB = os.environ.get("DJANGO_FSSPEC_BENCH_DB")

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
elif BENCH_DB == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get(
                "DJANGO_FSSPEC_BENCH_SQLITE_NAME",
                os.path.join(BASE_DIR, "benchmarks", "bench.sqlite3"),
            ),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django_fsspec",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django_fsspec.webdav.auth.BasicAuthMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
