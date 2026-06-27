# Block Size Migration

## Coexistence

After changing `DJANGO_FSSPEC_BLOCK_SIZE`, files with different block sizes coexist automatically:

- Each `FileNode` stores the `block_size` used at write time
- Reads use the file's own `block_size` for block arithmetic
- No migration required for correct operation

## When to Migrate

Use `RechunkOperation` when you want to **globally unify** block sizes — for example, to simplify operations or optimize storage efficiency.

## Using RechunkOperation

### 1. Create an empty migration

```bash
python manage.py makemigrations django_fsspec --empty -n rechunk_to_64k
```

### 2. Edit the migration

```python
from django.db import migrations
from django_fsspec.migrations_ops import RechunkOperation

class Migration(migrations.Migration):
    dependencies = [
        ("django_fsspec", "0001_initial"),
    ]

    operations = [
        RechunkOperation(new_block_size=64 * 1024),
    ]
```

### 3. Run the migration

```bash
python manage.py migrate
```

## What It Does

For each `FileNode` where `block_size != new_block_size`:

1. Reads all block data in sequence order
2. Joins into complete file content
3. Re-chunks with the new block size
4. In a single transaction: marks old blocks as free, deletes old mappings, creates new blocks and mappings
5. Updates `FileNode.block_size`

## Performance

For ~30K small files (< 256KB each), migration typically completes in a few minutes. Run `fsspec_gc` afterward to clean up freed blocks.
