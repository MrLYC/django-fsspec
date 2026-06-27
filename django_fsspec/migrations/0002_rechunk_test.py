"""Test migration for RechunkOperation. Used by test_migration.py."""

from django.db import migrations

from django_fsspec.migrations_ops import RechunkOperation


class Migration(migrations.Migration):

    dependencies = [
        ("django_fsspec", "0001_initial"),
    ]

    operations = [
        RechunkOperation(new_block_size=500),
    ]
