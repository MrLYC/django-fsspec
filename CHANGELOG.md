# Changelog

## [0.1.0] - 2026-06-27

### Added
- Initial release
- FileNode, StorageBlock, FileBlock models with configurable block size
- fsspec integration via `DjangoFileSystem` (protocol: `django`)
- File modes: `rb`, `wb`, `ab`, `xb`
- Optimistic locking with version field for concurrent write detection
- Batch block allocation with free block pool reuse
- Path validation (blacklist + Unicode NFC normalization)
- Implicit directory support with database-side pushdown queries
- `RechunkOperation` for block size migration
- Management commands: `fsspec_gc`, `fsspec_fsck`, `fsspec_stats`
- Django Admin integration (FileNode read-only)
- Multi-database support (MySQL, PostgreSQL, Oracle, domestic databases)
- Namespace-based multi-tenancy
