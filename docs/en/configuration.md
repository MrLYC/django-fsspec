# Configuration

All settings are read from Django's `settings.py` via `getattr` with defaults.

## DJANGO_FSSPEC_BLOCK_SIZE

- **Default**: `32768` (32KB)
- **Description**: Block size for file chunking
- **Impact**: Only affects newly written files. Each `FileNode` records the `block_size` used at write time, so files with different block sizes coexist
- **Recommendation**: Keep the 32KB default for small-file-heavy workloads and databases where large binary fields may behave like text/CLOB fields. For large-file throughput on databases with efficient binary storage, benchmark `64 * 1024`, `128 * 1024`, and `256 * 1024` before overriding.

## DJANGO_FSSPEC_MAX_FILE_SIZE

- **Default**: `2097152` (2MB)
- **Description**: Maximum allowed file size
- **Impact**: Writes exceeding this limit raise `FileTooLargeError`
- **Relationship to block_size**: Independent — these two settings don't affect each other

## DJANGO_FSSPEC_READ_INTEGRITY

- **Default**: `"off"`
- **Allowed values**: `"off"`, `"metadata"`, `"checksum"`
- **Description**: Default integrity policy for `read_file()` when no explicit
  policy is provided
- **Impact**:
  - `"off"` preserves compatibility and reads mapped bytes without extra checks
  - `"metadata"` verifies file/block shape, sizes, sequence continuity, and live block flags
  - `"checksum"` includes metadata checks plus block and file SHA-256 checks

Use `"metadata"` or `"checksum"` for jobs that must fail fast on damaged database
state. `copy_file()` keeps its default copy path low-overhead; pass
`integrity="checksum"` when a copy must be backup-grade.

## DJANGO_FSSPEC_MIGRATE_MANIFEST_DIR

- **Default**: `<BASE_DIR>/.django-fsspec-migrate`
- **Description**: Directory for automatically generated `fsspec_migrate`
  JSONL manifests
- **Impact**: Only affects real `fsspec_migrate` runs. Dry runs do not create a
  manifest unless a future command explicitly writes one.

Use `--manifest` on `fsspec_migrate` to override the destination for one run.

## Changing block size

After changing `DJANGO_FSSPEC_BLOCK_SIZE`:

1. **Existing files are unaffected** — each file records its own block_size
2. **New files use the new setting**
3. **Both coexist** — reads use the file's stored `block_size` for block arithmetic

No migration is required for correctness. To unify existing files to one block size, use `fsspec_rechunk`. See [Block Size Operations](block-size.md).
