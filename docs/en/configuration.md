# Configuration

All settings are read from Django's `settings.py` via `getattr` with defaults.

## DJANGO_FSSPEC_BLOCK_SIZE

- **Default**: `262144` (256KB)
- **Description**: Block size for file chunking
- **Impact**: Only affects newly written files. Each `FileNode` records the `block_size` used at write time, so files with different block sizes coexist
- **Recommendation**: For small-file workloads, try `64 * 1024` (64KB)

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
state. `copy_file()` uses checksum integrity by default even when the global
read policy is `"off"`.

## Changing block size

After changing `DJANGO_FSSPEC_BLOCK_SIZE`:

1. **Existing files are unaffected** — each file records its own block_size
2. **New files use the new setting**
3. **Both coexist** — reads use the file's stored `block_size` for block arithmetic

To unify all files to the new block size, use `RechunkOperation`. See [Migration Guide](migration-guide.md).
