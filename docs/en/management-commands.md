# Management Commands

This page is the command reference. For scenario-driven operation flows,
incident handling order, and limits, see [Operations Runbook](operations-runbook.md).

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

## Operational Exit Codes

`migrate`, `fsck`, `repair`, and `rechunk` use stable exit codes for scripts:

| Exit code | Meaning |
|-----------|---------|
| `0` | Command completed without findings that require operator attention |
| `1` | Command completed, but `migrate` skipped/conflicted, `fsck` found damage, `rechunk` skipped files, or `repair` still has unresolved damage |
| `2` | Invalid arguments or an unrecoverable command failure, such as invalid migrate options or `--on-error abort` |

`fsspec_repair` exits `0` after applying safe repairs when no unresolved damage remains.

Recommended incident flow:

```bash
python manage.py fsspec_fsck
python manage.py fsspec_repair --dry-run
python manage.py fsspec_repair
python manage.py fsspec_rechunk --block-size 32768 --dry-run
python manage.py fsspec_rechunk --block-size 32768
python manage.py fsspec_fsck
python manage.py fsspec_gc --dry-run
python manage.py fsspec_gc
```

## fsspec_migrate — Cross-Filesystem Copy

Copy files between fsspec-compatible filesystems:

```bash
python manage.py fsspec_migrate file:///mnt/import/ django://1/imports/
python manage.py fsspec_migrate django://1/uploads/ file:///mnt/export/uploads/
python manage.py fsspec_migrate django://1/a/ django://2/a-copy/ --limit 1000
python manage.py fsspec_migrate django://1/a/ file:///tmp/a/ --dry-run --json
python manage.py fsspec_migrate django://1/a/ file:///tmp/a/ --resume .django-fsspec-migrate/run.jsonl
```

`django://<namespace_id>/<path>` is supported anywhere fsspec URLs are accepted
by this package. The namespace id must be an integer. The URL host selects the
namespace; it is not an authentication identity.

Important options:

```bash
python manage.py fsspec_migrate SOURCE_URI TARGET_URI \
  --source-options '{"anon": false}' \
  --target-options '{}' \
  --conflict skip \
  --verify checksum
```

- `--source-options` and `--target-options` are JSON objects passed to
  `fsspec.core.url_to_fs()`. They are not written to manifest or JSON output, so
  credentials do not leak through command artifacts.
- Default behavior is copy-only: source files are never deleted.
- Default conflict policy is `skip`; existing targets are reported and the
  command exits `1`.
- `--conflict checksum` skips existing targets only when source and target bytes
  match.
- `--conflict overwrite` replaces existing target files.
- Default verification is `checksum`; use `--verify size` or `--verify off` only
  when the extra read is too expensive.
- Real runs generate a JSONL manifest by default. Use
  `DJANGO_FSSPEC_MIGRATE_MANIFEST_DIR` or `--manifest` to choose the location.
- Use `--resume <manifest>` to skip successful entries from a previous run and
  retry failed or conflicted entries.

The command prefers a temporary target path followed by `mv`, and falls back to
direct target writes when a filesystem does not support move/rename. It does not
install optional fsspec backends; install packages such as `s3fs` separately
when migrating to those protocols.

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
python manage.py fsspec_fsck --json         # Machine-readable findings
```

Checks performed:
- Block checksum matches stored data
- Block size matches actual data length
- File checksum matches reassembled content
- File size matches reassembled content length
- Block sequence numbers are contiguous
- No file blocks point to free storage blocks
- No file path also has descendants
- Directory nodes have no block mappings or payload metadata
- Persisted paths and node types are valid
- Storage blocks are not referenced by more than one file

Example output:
```
Checking block checksums...
  Checked 150 blocks
Checking file checksums...
  Checked 50 files
Checking for orphaned blocks...

Filesystem check passed. No errors found.
```

JSON output returns `{"ok": false, "findings": [...]}` and each finding includes
`severity`, `code`, `message`, and relevant ids. Severities are:

| Severity | Meaning |
|----------|---------|
| `warning` | Suspicious but not known to corrupt current reads |
| `recoverable` | Safe repair exists and can be previewed |
| `unresolved` | Requires an explicit recovery policy or backup restore |
| `critical` | Stop normal writes until investigated |

## fsspec_repair — Best-Effort Repair

Repair recoverable database damage with one command:

```bash
python manage.py fsspec_repair --dry-run
python manage.py fsspec_repair
python manage.py fsspec_repair --namespace 1
python manage.py fsspec_repair --recover-path-conflicts
python manage.py fsspec_repair --dry-run --json
```

Recommended incident flow:

1. Back up the database, or run against a restored copy first.
2. Run `python manage.py fsspec_repair --dry-run` to see the planned changes.
3. If `path_conflicts` is reported, inspect the output and decide whether moving
   descendants to the recovery prefix is acceptable.
4. Run `python manage.py fsspec_repair` to apply safe repairs.
5. Run `python manage.py fsspec_repair --recover-path-conflicts` only after a
   backup when you explicitly want descendants moved under the recovery prefix.
6. Run `python manage.py fsspec_fsck` to verify the result.

What it can repair:

| Damage scenario | Repair behavior |
|-----------------|-----------------|
| `StorageBlock.size` or `StorageBlock.checksum` was changed unexpectedly | Recomputes both fields from the current block bytes |
| `FileNode.size` or `FileNode.checksum` was changed unexpectedly | Reassembles current mapped blocks and recomputes file metadata |
| A live `StorageBlock` was incorrectly marked `is_free=True` | Marks referenced blocks as used again |
| `FileBlock.sequence` has gaps or starts at the wrong number | Renumbers existing mappings to contiguous `0..N-1` order |
| A directory row has block mappings or payload metadata | Removes impossible mappings and resets directory size/checksum |
| A used block has no remaining `FileBlock` owner | Marks it free during a global repair |
| A file path also has descendants | Reports `path_conflicts`; with `--recover-path-conflicts`, moves descendants to `/__django_fsspec_recovered__/conflicts/<namespace>/<timestamp>/...` |

Limits:

- The command cannot recreate bytes that were deleted or overwritten in `StorageBlock.data`.
- If `FileBlock` rows were deleted, the remaining orphaned block bytes no longer have a trustworthy path owner. The repair keeps the database consistent by recomputing the file from still-mapped blocks and freeing orphaned blocks.
- If block mappings were reordered while still keeping contiguous sequence numbers, there is no authoritative database signal for the original order. Restore from backup when original byte order matters.
- Shared storage blocks and invalid persisted paths are reported as unresolved
  damage. They are not automatically mutated because ownership or a valid target
  path cannot be proven from the damaged rows alone.
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
path_conflicts: 0
moved_descendants: 0
shared_blocks: 0
invalid_paths: 0

Applied 6 repair actions. Run fsspec_fsck to verify.
```

JSON output returns `{"ok": true, "dry_run": false, "summary": {...}, "unresolved": false}`.
Safe repairs are reflected in `summary`; unresolved structural damage sets
`unresolved` to `true` and exits with code `1`.

## fsspec_rechunk — Block Size Rewrite

Rewrite existing files to a target block size. This is an operational command,
not a Django migration; it can be run repeatedly and in batches.

```bash
python manage.py fsspec_rechunk --block-size 32768 --dry-run
python manage.py fsspec_rechunk --block-size 32768 --namespace 1 --prefix /uploads/ --limit 1000
python manage.py fsspec_rechunk --block-size 32768 --verify checksum
python manage.py fsspec_rechunk --block-size 32768 --json
```

Important behavior:

- Only files whose stored `FileNode.block_size` differs from `--block-size` are rewritten.
- Each file is processed in its own transaction.
- Existing files continue to work without rechunking; run this command only when you want to standardize old data.
- Damaged files and concurrent version conflicts are skipped by default. Use `--on-error abort` to stop on the first problem.
- Old blocks are marked free, not deleted. Run `fsspec_gc` after verification.

JSON output returns `{"ok": true, "dry_run": false, "summary": {...}, "skipped": []}`.
When files are skipped, `ok` is `false`, each skipped file is listed with a
reason, and the command exits with code `1`.

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
