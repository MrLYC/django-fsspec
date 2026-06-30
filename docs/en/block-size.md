# Block Size Operations

## Coexistence

After changing `DJANGO_FSSPEC_BLOCK_SIZE`, files with different block sizes coexist automatically:

- Each `FileNode` stores the `block_size` used at write time
- Reads use the file's own `block_size` for block arithmetic
- No migration required for correct operation

## When to Rechunk

Use `fsspec_rechunk` when you want to **globally unify** block sizes — for example, to reduce per-block payload size on databases where large binary fields are expensive, or to standardize old files after changing the setting.

Changing the default from 256KB to 32KB does not require a migration. Existing files keep their recorded `FileNode.block_size`; new writes use the current setting. Run rechunk only when you intentionally want to rewrite old files.

## Using fsspec_rechunk

Preview first:

```bash
python manage.py fsspec_rechunk --block-size 32768 --dry-run
```

Run in batches when the table is large:

```bash
python manage.py fsspec_rechunk --block-size 32768 --namespace 1 --prefix /uploads/ --limit 1000
```

Use checksum verification for backup-grade rewrites:

```bash
python manage.py fsspec_rechunk --block-size 32768 --verify checksum
```

## What It Does

For each selected file where `block_size != --block-size`:

1. Verifies metadata by default; `--verify checksum` also verifies SHA-256 checksums
2. Reads all block data in sequence order
3. Joins into complete file content
4. Re-chunks with the new block size
5. In a per-file transaction: creates new blocks and mappings, removes old mappings, and marks ownerless old blocks as free
6. Updates `FileNode.block_size`, size, checksum, and version

If a file changes while it is being rewritten, or if damaged metadata is found, the command skips that file by default. Use `--on-error abort` when a batch should stop on the first problem.

After rechunking, run:

```bash
python manage.py fsspec_fsck
python manage.py fsspec_gc --dry-run
```

## Performance

For ~30K small files (< 256KB each), rechunk may still take minutes because every selected file is read and rewritten. Use `--limit` to split large datasets into repeatable batches. Run `fsspec_gc` afterward to clean up freed blocks.
