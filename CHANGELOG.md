# Changelog

## [Unreleased]

### Added
- Add `fsspec_rechunk` as the repeatable operational command for rewriting existing files to a target block size with dry-run, filters, checksum verification, and skip/abort error handling.
- Add JSON output for `fsspec_repair` and `fsspec_rechunk`.
- Add a release checklist and CI package checks for wheel contents and tag version consistency.
- Add `uv.lock` and use uv for development, CI, benchmark, and release dependency management.

### Changed
- Change the default block size from 256KB to 32KB and document 32KB as the conservative default for small files and broad database compatibility.
- Rework block-size guidance so changing `DJANGO_FSSPEC_BLOCK_SIZE` does not imply a Django migration; old and new block sizes coexist by design.
- Define stable operational exit codes for `fsspec_fsck`, `fsspec_repair`, and `fsspec_rechunk`.
- Constrain the supported Django dependency range to `>=4.2,<6.0`, matching the tested Django 4.2 and 5.2 matrix.

### Removed
- Remove the `RechunkOperation` migration API. Rechunking user data is now an explicit management command, not a package migration operation.

## [0.2.1] - 2026-06-29

### Added
- Add scale-based benchmark runs with deterministic seeds and seeded large-table scenarios.
- Document benchmark scenario design and manual large benchmark workflow.
- Add a manual Large Benchmark GitHub Actions workflow for medium and large benchmark datasets.
- Add a demo Django project settings module and `demo/manage.py` for local checks, tests, e2e, and benchmark runs.

### Changed
- Mark package metadata as Production/Stable and align supported Django versions with Python 3.11+ by requiring Django 4.2 or newer.
- Disable free block reuse on the write path; new writes now always allocate fresh storage blocks, and `fsspec_gc` is responsible for permanently deleting free blocks.
- Clarify that namespaces provide path partitioning, not an authorization boundary for direct fsspec API callers.
- Reject `.` path segments in addition to `..` to avoid canonicalization ambiguity.
- Move tests to the top-level `tests/` directory so test modules are not shipped inside the installable package.
- Consolidate benchmark and unit-test Django settings into `demo.settings`.

### Fixed
- Route fsspec append mode through `append_file()` instead of rewriting stale preloaded file contents.
- Make `fsspec_fsck` fail with `CommandError` when corruption is detected and scope block checks to `--namespace` when provided.
- Convert the test-only rechunk migration into a no-op compatibility migration so new installs do not rechunk data and existing migration graphs keep the `0002` node.
- Validate `RechunkOperation(new_block_size=...)` and use the active migration database alias.

## [0.2.0] - 2026-06-28

### Fixed
- **Transaction rollback broken in autocommit mode** — `DjangoTransaction` now uses `atomic()` instead of raw `savepoint()`, which was a no-op in Django's default autocommit mode. Rollbacks now work correctly in all modes.
- **`append_file` not atomic** — read + append + write now execute in a single transaction with optimistic locking. Concurrent appends raise `FileConflictError` instead of silently losing data.
- **MySQL `bulk_create` compatibility** — replaced `bulk_create` for new block allocation with individual creates, fixing "no primary key" errors on MySQL.
- **Oracle `ORA-01408` duplicate index** — removed redundant `(namespace, path)` index that conflicted with `unique_together` on Oracle.
- **`oracledb` 4.0 encoding crash** — pinned `oracledb<4` in CI to avoid known driver bug (oracle/python-oracledb#595).

### Added
- **fsspec API completeness** — implemented `_rm`, `rm_file`, `touch`, `checksum` (returns stored SHA-256), `ukey` (checksum:version), `sign` (raises NotImplementedError), and `find` (direct database query with `maxdepth` and `withdirs` support).
- **fsspec transaction integration** — `DjangoTransaction` maps `fs.transaction` onto Django database transactions. Supports commit, rollback on exception, and interop with existing Django transactions as savepoints.
- **Block size drift detection** — Django system check `django_fsspec.W001` warns when files exist with a different `block_size` than the current setting. Also logs a warning via `post_migrate` signal.
- **Migration testing** — 17 tests using `django-test-migrations` to verify initial migration schema and `RechunkOperation` end-to-end through the Django migration framework.
- **Transaction E2E tests** — 9 scenarios covering atomicity, exclusive create race, delete-while-reading, overwrite consistency, block pool integrity, concurrent move, concurrent append, and namespace isolation under contention.
- **Concurrency benchmarks** — `concurrent_write`, `concurrent_read`, `concurrent_mixed` (8 threads each).
- **CI multi-database matrix** — MySQL 5.7 + 8.0, PostgreSQL 9.6 + 16, Oracle 23 Free, SQLite. E2E (36 tests per DB) + benchmarks (13 scenarios) + performance baseline artifacts.
- **Architecture diagram** in README and docs.
- **Performance tables** in README (en + zh) with latest CI benchmark data.
- **Transaction pitfalls documentation** covering isolation level differences, non-grouped operations, and commit/discard semantics.
- **PyPI metadata** — project URLs (homepage, repository, docs, changelog, issues), classifiers, and keywords.
- **Tag-triggered PyPI publish** in CI with version verification.

### Changed
- `DjangoFile` moved from `fs.py` to `buffer.py` per original architecture plan.
- `verify_checksum` parameter added to `read_file()`.
- Nested `fs.transaction` now raises `RuntimeError` instead of undefined behavior.

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
