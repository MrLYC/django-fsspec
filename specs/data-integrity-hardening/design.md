# Data Integrity Hardening Design

Status: implementation-backed spec. The core defensive reads, dirty-graph
guards, fsck reporting, and explicit path-conflict repair flow are implemented
in this branch; remaining open items are tracked in `tasks.md`.

## Overview

The current write path already uses transactions, unique constraints, checksums,
and optimistic version updates for normal file overwrite and append. The main
remaining risks are structural races:

- A path can become both a file and an ancestor of another file.
- Recursive directory move is currently copy-then-delete at the fsspec layer.
- Long-lived read handles fetch by path on each range read.
- Transaction state is stored on the filesystem instance.
- Unexpected database damage can make read, list, copy, or delete operations
  either silently trust bad rows or fail broader workflows than necessary.

This design favors portable correctness before throughput. If benchmark data
later shows excessive contention, introduce narrower path-prefix locks as a
second phase.

## Operation Modes

Add explicit integrity behavior levels:

| Mode | Behavior |
|------|----------|
| `strict` | Fail fast on integrity or structure problems. This is appropriate for verified reads, audited copies, and management workflows. |
| `degraded` | Keep healthy objects available while marking or skipping corrupt objects. This is appropriate for incident browsing, inventory, and partial export. |
| `repair` | Management-command-only mode that audits and mutates metadata with explicit flags. |

The default can preserve current compatibility, but the strict and degraded
paths must be available through configuration and/or explicit API options.

Introduce a dedicated exception type such as `DataIntegrityError` for corrupted
rows, mismatched checksums, inconsistent block graphs, and repair-required
conditions. Avoid using plain `ValueError` for integrity failures.

## Namespace-Scoped Write Lock

Add an internal helper such as `_lock_namespace_for_write(namespace)` that locks
the `Namespace` row with `select_for_update()` inside an existing
`transaction.atomic()` block.

Use it for all path-mutating operations:

- create and overwrite writes
- exclusive create
- append when it may create a new file
- mkdir and rmdir
- file delete and recursive delete
- file move and future directory move

The lock serializes path-tree mutations within one namespace and stays portable
across PostgreSQL, MySQL/InnoDB, Oracle, and Django's transaction API.

## Transactional Path Invariant Checks

Move ancestor and descendant checks inside the locked transaction. Add a single
invariant helper that enforces:

- no file path may have a file or directory descendant
- no file may be created under an existing file ancestor
- an explicit directory may have descendants, but must not have file blocks

Run the helper before creating or updating a `FileNode.path`. Keep validation
path-based and namespace-scoped.

## Atomic Directory Move

Replace recursive directory `mv` implemented as copy-then-delete with a dedicated
operation that rewrites `FileNode.path` values inside one transaction.

Algorithm:

1. Lock the namespace.
2. Validate source, destination, and destination conflicts.
3. Reject moving a directory into itself.
4. Select all `FileNode` rows matching the source prefix.
5. Rewrite paths using a temporary prefix if needed to avoid unique-key
   collisions.
6. Leave `StorageBlock` and `FileBlock` rows untouched.

This makes directory move a metadata operation rather than a copy/delete
workflow. File checksums and block mappings should not change.

## Read Version Guard

When opening a file for `rb`, capture:

- `FileNode.pk`
- `FileNode.version`
- `FileNode.checksum`
- `FileNode.size`
- `FileNode.block_size`

Range reads should use the captured primary key and expected version. If the row
is deleted, moved, or overwritten before the handle finishes, raise a clear
conflict instead of reading by path and returning mixed ranges.

Path-only one-shot reads can keep current behavior. Long-lived handles need the
stronger guard.

## Defensive Reads

Add a configurable read-integrity policy:

| Policy | Checks |
|--------|--------|
| `off` | Preserve current compatibility; do not verify content unless explicitly requested. |
| `metadata` | Verify block graph shape, sequence coverage, file size, and `is_free` flags without hashing every byte. |
| `checksum` | Verify metadata checks plus block and file checksums. |

Whole-file reads and range reads should share the same validation helpers. Range
reads can validate only the blocks needed for the requested range plus file-level
metadata that is cheap to check. Full file checksum requires reading all bytes
and should be tied to the `checksum` policy.

`copy_file()` should keep the default path low-overhead for normal application
workflows. Backup/export-like callers should pass at least `metadata` and prefer
`checksum` where practical. This prevents corrupted source bytes from being
copied into a new destination with fresh metadata when the caller has requested
that stronger contract.

## Tolerant Listing

Add tolerant listing helpers for incident mode. These helpers should continue
walking healthy entries even when some rows are corrupt.

Behavior:

- Default listing is metadata-only and avoids per-file validation overhead.
- Tolerant listing either skips corrupt entries or returns a structured marker:

  ```python
  {
      "name": "/path",
      "type": "corrupt",
      "error": "file path has descendants",
  }
  ```

- Tolerant listing must not repair rows or fabricate file contents.
- Management commands can use tolerant traversal to produce complete audit
  reports even when individual entries are damaged.

## Dirty-Graph Write Protection

Destructive operations such as overwrite, delete, move, and recursive delete
should avoid expensive validation of old content. They should use release logic
that is safe even when mappings are dirty:

- deleting mappings marks only now-ownerless storage blocks as free
- stale size/checksum metadata does not block deletion or overwrite
- target path is not both a file and an ancestor
- directory nodes with accidental file blocks can still be removed safely

If the path tree itself is dirty, fail with `DataIntegrityError` or a
repair-required subclass. The operation should not turn shared or mis-owned
blocks into free blocks while another file still references them.

Provide a management-only bypass only if a concrete recovery workflow needs it.

## Thread-Local Transaction State

Do not store active fsspec transaction state only on the shared filesystem
instance. Move `_intrans` and `_transaction` state behind thread-local or
`contextvars` storage, or document and enforce that one `DjangoFileSystem`
instance cannot be used concurrently across threads.

Prefer implementation over documentation if the fsspec instance cache makes
shared instances likely in real applications.

## fsck Design

Extend `fsspec_fsck` with structural checks:

- file path has descendants
- directory node has `FileBlock` mappings
- invalid `FileNode.node_type`
- invalid persisted paths under current validation rules
- file node has missing or duplicate sequence coverage
- file node checksum/size does not match current mapped bytes
- referenced block is marked free
- used block has no owner
- a `StorageBlock` is referenced by more than one file unless explicit sharing is
  introduced in the future

The existing checksum and free-block checks stay unchanged.

fsck output should support a machine-readable JSON mode with severity levels:

| Severity | Meaning |
|----------|---------|
| `warning` | Suspicious but not known to corrupt current reads |
| `recoverable` | Safe repair exists and can be previewed |
| `unresolved` | Requires explicit recovery policy or backup restore |
| `critical` | Normal writes should stop until repaired |

## Repair Design

Keep the current safe repairs:

- recompute block metadata from current bytes
- recompute file metadata from currently mapped blocks
- mark referenced blocks as used
- mark unreferenced used blocks as free in global repair
- renumber non-contiguous file block sequences
- remove impossible directory mappings

Add planned structural recovery:

| Damage | Default repair behavior | Explicit recovery option |
|--------|-------------------------|--------------------------|
| File path also has descendants | Report as unresolved path conflict and return non-zero after other safe repairs | `--recover-path-conflicts --path-conflict-policy=move-descendants` moves descendants to `/__django_fsspec_recovered__/conflicts/<namespace>/<timestamp>/...` |
| Directory move was interrupted after copy but before delete | Detect duplicate path/content patterns when possible, but do not delete automatically | Future `--dedupe-recovered-copies` only after an audit report |
| Long-read mixed output was copied into another file | Cannot infer original bytes | Report only; restore from backup if original byte order matters |
| Deleted `FileBlock` ownership | Preserve current behavior: recompute file from remaining mappings and free orphaned blocks | No automatic path inference without an audit source |
| Shared block graph | Report as unresolved by default | Future explicit policy may duplicate blocks per owner only when checksums and expected file bytes can be proven |
| Invalid persisted path | Report as unresolved by default | Future explicit policy may move to a validated recovery prefix |

The recovery path must be valid under existing path validation rules and remain
inside the same namespace.

`fsspec_repair --dry-run` should become the primary incident entry point. It
should summarize safe repairs, unresolved damage, and mode recommendations
without mutating data.

## Documentation Impact

After implementation, update:

- management command docs for new `fsck` and `repair` checks
- architecture docs for namespace write locking and directory move semantics
- usage docs to state recursive copy is not a snapshot unless a snapshot mode is added
- transaction docs to clarify thread-safety behavior
- incident docs for strict/degraded/repair modes and `fsspec_repair --dry-run`
  runbooks
