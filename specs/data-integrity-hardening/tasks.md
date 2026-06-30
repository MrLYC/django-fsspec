# Data Integrity Hardening Tasks

Status: implementation in progress. Checked items are implemented in code and
covered by local tests in this branch. Unchecked concurrency items still need a
database-backed e2e matrix that can exercise real row-lock behavior.

## Phase 1: Adversarial Tests

- [ ] Add a `TransactionTestCase` helper for multi-role scenarios with barriers
      and separate database connections.
- [ ] A1: test concurrent create of `/a` as a file and `/a/b.txt` as a child.
- [ ] A2: test concurrent overwrite of `/a` while another role creates
      `/a/b.txt`.
- [ ] A3: test recursive `fs.mv("/dir", "/archive", recursive=True)` while a
      writer adds `/dir/new.txt` between enumeration and source removal.
- [ ] A4: test recursive `fs.copy()` while source files are overwritten, and
      assert the chosen snapshot or non-snapshot contract.
- [x] A5: test an open read handle across a concurrent overwrite.
- [x] A6: test shared `DjangoFileSystem` transaction state across threads.
- [x] A7: keep or strengthen concurrent append handle tests.
- [ ] A8: test recursive delete racing with append/overwrite on a child path.
- [x] D1: test strict read behavior after block bytes or block checksum are
      corrupted.
- [x] D2: test tolerant listing when one file in a directory has corrupted
      metadata or structure.
- [x] D3: test copy/backup behavior refuses to propagate corrupted source
      content by default.
- [x] D4: test overwrite/delete fails on a dirty shared-block graph instead of
      releasing blocks incorrectly.
- [x] D5: test `fsspec_repair --dry-run` reports path-tree conflicts without
      moving data.

## Phase 2: Core Fixes

- [x] Add namespace-scoped write locking inside path-mutating transactions.
- [x] Move path ancestor and descendant invariant checks into the locked
      transaction.
- [x] Add a shared path invariant helper.
- [x] Replace recursive directory move copy/delete with an atomic metadata move.
- [x] Add read-handle version guards for `rb` handles.
- [x] Move fsspec transaction state to thread-local or `contextvars`, or enforce
      non-concurrent filesystem instance usage.
- [x] Add `DataIntegrityError` or equivalent dedicated exception class for
      corrupted database state.
- [x] Add read-integrity policy support: `off`, `metadata`, and `checksum`.
- [x] Share validation helpers between whole-file reads and range reads.
- [x] Make copy/export-like flows use integrity checks by default.
- [x] Add tolerant listing support that can skip or mark corrupt entries.
- [x] Add dirty-graph preflight checks before overwrite, delete, move, and
      recursive delete.

## Phase 3: fsck and Repair

- [x] Extend `fsspec_fsck` to report file paths that have descendants.
- [x] Extend `fsspec_fsck` to report directory nodes with `FileBlock` mappings.
- [x] Extend `fsspec_fsck` to report missing or duplicate sequence coverage.
- [x] Extend `fsspec_fsck` to report invalid `FileNode.node_type` values.
- [x] Extend `fsspec_fsck` to report invalid persisted paths under current path
      validation rules.
- [x] Extend `fsspec_fsck` to report blocks referenced by more than one file.
- [x] Add severity levels to fsck findings: warning, recoverable, unresolved,
      and critical.
- [x] Add machine-readable JSON output to `fsspec_fsck`.
- [x] Add `fsspec_repair --dry-run` reporting for `path_conflicts`.
- [x] Add `fsspec_repair --dry-run` summary buckets for safe repairs,
      unresolved damage, and critical stop-write conditions.
- [x] Add explicit path-conflict recovery options:
      `--recover-path-conflicts` and
      `--path-conflict-policy=move-descendants`.
- [x] Keep path-conflict recovery in the same namespace and under a validated
      recovery prefix.
- [x] Add unresolved reporting for shared block graphs and invalid persisted
      paths.
- [x] Add repair test R1: `fsspec_fsck` reports `/a` file plus `/a/b.txt`
      descendant.
- [x] Add repair test R2: `fsspec_repair --dry-run` reports path conflicts
      without data movement.
- [x] Add repair test R3: explicit recovery moves descendants to the recovery
      prefix and `fsspec_fsck` passes.
- [x] Add repair test R4: namespace-scoped repair reports or repairs only the
      selected namespace.
- [x] Keep existing metadata, free-block, sequence-gap, directory-mapping, and
      deleted-mapping repair tests passing.

## Phase 4: Documentation and Validation

- [x] Update management command docs after fsck and repair behavior changes.
- [x] Update architecture docs after namespace locking and directory move changes.
- [x] Update usage docs with the recursive copy snapshot contract.
- [x] Update transaction docs with the thread-safety contract.
- [x] Add incident-mode docs for strict, degraded, and repair operation.
- [x] Document recommended runbook: stop writes if fsck reports critical issues,
      run `fsspec_repair --dry-run`, apply explicit repair flags only after
      backup, then rerun fsck.
- [x] Run `python -m pytest tests/ -q --cov=django_fsspec --cov-report=term-missing`.
- [ ] Run GitHub CI and e2e database matrix.
