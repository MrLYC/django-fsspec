"""Register django-fsspec with DuckDB and query a Parquet file.

The script creates a temporary SQLite database, writes one Parquet file through
``DjangoFileSystem``, and reads it back with DuckDB. DuckDB calls the
filesystem from worker threads, so the database is a file. A SQLite
``:memory:`` database is private to the connection that created it.

Run from the repository root::

    uv run --extra duckdb python examples/duckdb_parquet.py

Parquet bytes must fit within ``DJANGO_FSSPEC_MAX_FILE_SIZE`` (default 2MB).
"""

import os
import tempfile

import duckdb


PARQUET_GLOB = "django:///warehouse/*.parquet"
PARQUET_PATH = "django:///warehouse/sales.parquet"


def configure_django(database_path):
    """Point Django at a file-backed SQLite database and migrate."""
    os.environ.pop("DJANGO_SETTINGS_MODULE", None)

    import django
    from django.conf import settings
    from django.core.management import call_command

    if not settings.configured:
        settings.configure(
            SECRET_KEY="django-fsspec-duckdb-example",
            DEBUG=True,
            USE_TZ=True,
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django_fsspec",
            ],
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": database_path,
                }
            },
        )
    django.setup()
    call_command("migrate", verbosity=0, interactive=False)


def main():
    import fsspec
    from django.db import connections

    with tempfile.TemporaryDirectory() as tmp:
        configure_django(os.path.join(tmp, "example.sqlite3"))
        try:
            filesystem = fsspec.filesystem("django", namespace_id=1)
            # Three slashes keep the stored path absolute. django://warehouse/...
            # strips the leading slash and fails django-fsspec path validation.
            duckdb.register_filesystem(filesystem)
            duckdb.sql(
                f"""
                COPY (
                    SELECT * FROM (VALUES
                        ('Beijing', 12.5::DOUBLE),
                        ('Shanghai', 9.0::DOUBLE),
                        ('Shenzhen', 15.75::DOUBLE)
                    ) sales(city, amount)
                ) TO '{PARQUET_PATH}' (FORMAT PARQUET)
                """
            )
            duckdb.sql(
                f"""
                SELECT city, amount
                FROM read_parquet('{PARQUET_GLOB}')
                ORDER BY city
                """
            ).show()
            total = duckdb.sql(
                f"SELECT sum(amount) FROM read_parquet('{PARQUET_GLOB}')"
            ).fetchone()[0]
            print(f"total={total:.2f}")
        finally:
            connections.close_all()


if __name__ == "__main__":
    main()
