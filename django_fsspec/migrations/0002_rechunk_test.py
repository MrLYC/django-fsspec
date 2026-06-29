# Preserve this migration node for installations that already saw 0002.
# The original test-only RechunkOperation was removed before GA because app
# migrations must not rewrite user data unexpectedly.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("django_fsspec", "0001_initial"),
    ]

    operations = []
