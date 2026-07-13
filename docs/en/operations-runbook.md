# Operations Runbook

This page is scenario-oriented. For complete option lists, see
[Management Commands](management-commands.md).

## First Response Flow

Use this sequence when database rows may have been changed or deleted outside
the normal `django-fsspec` APIs:

```bash
python manage.py fsspec_fsck --json
python manage.py fsspec_repair --dry-run --json
python manage.py fsspec_repair
python manage.py fsspec_fsck
```

If the incident also involved a block-size policy change or a backend with slow
large binary fields, batch healthy files into the current target block size:

```bash
python manage.py fsspec_rechunk --block-size 32768 --dry-run --json
python manage.py fsspec_rechunk --block-size 32768 --namespace 1 --prefix /uploads/ --limit 1000
python manage.py fsspec_fsck
python manage.py fsspec_gc --dry-run
python manage.py fsspec_gc
```

Safety rules:

- Take a database backup, or rehearse against a restored copy, before commands
  that mutate rows.
- Prefer `--dry-run --json` first in automation. Exit code `1` means the command
  completed but needs operator attention; exit code `2` means invalid input or a
  hard failure.
- Run `fsspec_fsck` before and after repair, rechunk, or large migrations.
- Use `--namespace`, `--prefix`, and `--limit` to keep operational batches small
  enough for your database lock and maintenance windows.

## Command Choice

| Situation | Command |
|-----------|---------|
| Check whether rows are still internally consistent | `fsspec_fsck` |
| Repair derived metadata, free/used flags, sequence gaps, and safe directory metadata issues | `fsspec_repair` |
| Move descendants away from a file path conflict after explicit approval | `fsspec_repair --recover-path-conflicts` |
| Rewrite existing files to the configured block size | `fsspec_rechunk --block-size 32768` |
| Delete free blocks after verification and retention | `fsspec_gc` |
| Copy data between local, Django, and other fsspec filesystems | `fsspec_migrate` |
| Inspect high-level storage counts | `fsspec_stats` |

## Cross-Filesystem Migration

Use `fsspec_migrate` when importing from a local directory, exporting to a local
backup path, or copying between namespaces:

```bash
python manage.py fsspec_migrate file:///mnt/import/ django://1/imports/ --dry-run --json
python manage.py fsspec_migrate file:///mnt/import/ django://1/imports/ --manifest /var/log/django-fsspec/import.jsonl
python manage.py fsspec_migrate django://1/uploads/ file:///mnt/export/uploads/ --manifest /var/log/django-fsspec/export.jsonl
python manage.py fsspec_migrate django://1/uploads/ django://2/uploads-copy/ --limit 1000
```

Limits and notes:

- The command is copy-only. It never deletes source files.
- The default conflict policy is `skip`; existing targets make the command exit
  with code `1` so scripts can flag operator review.
- `django://<namespace_id>/<path>` selects a namespace. It is not an
  authentication identity; the caller must already be allowed to run the command.
- Pass backend credentials through environment variables or
  `--source-options` / `--target-options` JSON. These options are not written to
  the manifest or JSON summary.
- Optional fsspec backends such as S3 are not bundled by `django-fsspec`; install
  them in the application environment.

## Repairing Damaged Rows

Use `fsspec_repair` when `fsspec_fsck` reports recoverable findings:

```bash
python manage.py fsspec_repair --dry-run --json
python manage.py fsspec_repair
python manage.py fsspec_fsck --json
```

The command can repair metadata that can be recomputed from current rows:
`StorageBlock.size`, `StorageBlock.checksum`, `FileNode.size`,
`FileNode.checksum`, live blocks incorrectly marked free, sequence gaps,
impossible directory block mappings, and unreferenced used blocks.

Limits:

- It cannot recreate bytes deleted from `StorageBlock.data`.
- If `FileBlock` rows were deleted, orphaned block bytes no longer have a
  trustworthy path owner. Repair makes the database consistent from remaining
  mappings and marks orphaned blocks free.
- Shared blocks, invalid persisted paths, and path conflicts require explicit
  operator handling or backup restore.
- Use `--recover-path-conflicts` only after reviewing a dry run and accepting the
  recovery prefix move.

## Rechunk And Garbage Collection

Use `fsspec_rechunk` after changing the target block size or when a database
backend performs poorly with large binary/text fields:

```bash
python manage.py fsspec_rechunk --block-size 32768 --dry-run --json
python manage.py fsspec_rechunk --block-size 32768 --namespace 1 --prefix /media/ --limit 1000
python manage.py fsspec_fsck
python manage.py fsspec_gc --dry-run
python manage.py fsspec_gc
```

Limits:

- Rechunking is not a Django migration and can be rerun. Files already at the
  target block size are skipped.
- Each file is processed in its own transaction. Damaged files are skipped by
  default so healthy files can continue to progress.
- Old blocks are marked free, not deleted. `fsspec_gc` permanently deletes only
  free blocks, so run it after verification and after any retention window.

## Validated E2E Coverage

`benchmarks/e2e_test.py` includes runbook-level coverage for:

- `ops_runbook_migrate_roundtrip`: local directory to `django://`, export back
  to local files, then namespace-to-namespace copy.
- `ops_runbook_repair_flow`: `fsck` detects repairable metadata damage,
  `repair --dry-run` does not mutate rows, `repair` fixes rows, and `fsck`
  becomes clean.
- `ops_runbook_rechunk_gc_flow`: old-block-size files are dry-run inspected,
  rechunked to 32KB, verified, then free blocks are removed with `gc`.
- `ops_runbook_json_exit_codes`: JSON output parses successfully, `fsck`
  findings and migrate conflicts return attention exit code `1`, and dry-run
  repair remains non-mutating.
