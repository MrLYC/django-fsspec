# Management Commands

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
python manage.py fsspec_fsck --namespace 0  # Check specific namespace
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

## fsspec_stats — Statistics

Display filesystem statistics:

```bash
python manage.py fsspec_stats
python manage.py fsspec_stats --namespace 0
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
