# Management Commands

## fsspec_namespace — Namespace Management

Manage namespaces:

```bash
python manage.py fsspec_namespace list
python manage.py fsspec_namespace show default
python manage.py fsspec_namespace show --id 1
python manage.py fsspec_namespace create media --description "Media files"
python manage.py fsspec_namespace create media --read-group readers --write-group writers
python manage.py fsspec_namespace update media --description "Updated description"
python manage.py fsspec_namespace update media --clear-read-groups
python manage.py fsspec_namespace delete media
```

The default namespace is created by migrations as `id=1`, `name=default`, and cannot be deleted by this command.

## fsspec_gc — Garbage Collection

Clean up free storage blocks:

```bash
python manage.py fsspec_gc              # Delete all free blocks
python manage.py fsspec_gc --keep 100   # Keep 100 free blocks for inspection
python manage.py fsspec_gc --dry-run    # Preview without deleting
```

Example output:
```
Deleted 42 free blocks (kept 0)
```

## fsspec_fsck — Filesystem Check

Verify data integrity:

```bash
python manage.py fsspec_fsck               # Check all
python manage.py fsspec_fsck --namespace 1  # Check specific namespace
```

Checks performed:
- Block checksum matches stored data
- Block size matches actual data length
- File checksum matches reassembled content
- File size matches reassembled content length
- Block sequence numbers are contiguous
- No file blocks point to free storage blocks

Example output:
```
Checking block checksums...
  Checked 150 blocks
Checking file checksums...
  Checked 50 files
Checking for orphaned blocks...

Filesystem check passed. No errors found.
```

## fsspec_repair — Best-Effort Repair

Repair recoverable database damage with one command:

```bash
python manage.py fsspec_repair --dry-run
python manage.py fsspec_repair
python manage.py fsspec_repair --namespace 1
```

Recommended incident flow:

1. Back up the database, or run against a restored copy first.
2. Run `python manage.py fsspec_repair --dry-run` to see the planned changes.
3. Run `python manage.py fsspec_repair` to apply repairs.
4. Run `python manage.py fsspec_fsck` to verify the result.

What it can repair:

| Damage scenario | Repair behavior |
|-----------------|-----------------|
| `StorageBlock.size` or `StorageBlock.checksum` was changed unexpectedly | Recomputes both fields from the current block bytes |
| `FileNode.size` or `FileNode.checksum` was changed unexpectedly | Reassembles current mapped blocks and recomputes file metadata |
| A live `StorageBlock` was incorrectly marked `is_free=True` | Marks referenced blocks as used again |
| `FileBlock.sequence` has gaps or starts at the wrong number | Renumbers existing mappings to contiguous `0..N-1` order |
| A directory row has block mappings or payload metadata | Removes impossible mappings and resets directory size/checksum |
| A used block has no remaining `FileBlock` owner | Marks it free during a global repair |

Limits:

- The command cannot recreate bytes that were deleted or overwritten in `StorageBlock.data`.
- If `FileBlock` rows were deleted, the remaining orphaned block bytes no longer have a trustworthy path owner. The repair keeps the database consistent by recomputing the file from still-mapped blocks and freeing orphaned blocks.
- If block mappings were reordered while still keeping contiguous sequence numbers, there is no authoritative database signal for the original order. Restore from backup when original byte order matters.
- Run with `--namespace` to limit file and mapping repairs to one namespace. Global orphan cleanup only runs when no namespace filter is supplied.

Example output:

```
Repairing filesystem metadata...

block_metadata: 1
free_referenced_blocks: 1
unreferenced_used_blocks: 1
directory_mappings: 0
directory_metadata: 0
file_sequences: 1
file_metadata: 2

Applied 6 repair actions. Run fsspec_fsck to verify.
```

## fsspec_stats — Statistics

Display filesystem statistics:

```bash
python manage.py fsspec_stats
python manage.py fsspec_stats --namespace 1
```

Example output:
```
Django-fsspec Statistics
========================================
Namespaces:       3
Files:            1250
Total file size:  15.2 MB
Storage blocks:   1300
  Used:           1250
  Free:           50
Block data size:  15.4 MB
File-block maps:  1250
```
