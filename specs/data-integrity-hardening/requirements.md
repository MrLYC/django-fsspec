# Data Integrity Hardening Requirements

Status: implementation-backed spec. Core defensive behavior is implemented in
this branch; remaining validation and documentation work is tracked in
`tasks.md`.

## Goal

Use multi-role adversarial scenarios that only call the public fsspec interface
to harden path consistency, recursive operations, streaming reads, and the
`fsspec_repair` incident path.

The target is not to make every race impossible at zero cost. The target is:

- No valid fsspec operation should leave a namespace with an impossible path tree.
- Recursive moves must not silently lose files written during the move.
- Long-lived reads must not return mixed bytes from two file versions.
- Unexpected database damage should not automatically make every healthy file
  unavailable.
- `fsspec_fsck` and `fsspec_repair` should detect and handle structural damage
  that cannot be prevented in older deployments.

## Adversarial Roles

| Role | Behavior to simulate |
|------|----------------------|
| Writer | Creates or overwrites files through `fs.pipe()` or `fs.open(..., "wb")` |
| Appender | Uses `fs.open(..., "ab")` with multiple handles and delayed close |
| Deleter | Calls `fs.rm()` on files and recursive directories |
| Mover | Calls `fs.mv()` for files and directories |
| Copier | Calls recursive `fs.copy()` while source files are changing |
| Reader | Holds an open `rb` handle while another role mutates the same path |
| Transaction user | Uses `with fs.transaction:` and intentionally rolls back |
| Damager | Represents external SQL, migration bugs, old-version bugs, or operator mistakes that already corrupted rows |
| Operator | Runs `fsspec_fsck`, `fsspec_repair`, backup, and audit commands during an incident |

## Requirements

### R1. Path Tree Consistency

The filesystem must prevent impossible path trees.

Acceptance criteria:

- Creating `/a` as a file while `/a/b.txt` is concurrently created must leave at
  most one of those conflicting outcomes.
- Overwriting `/a` must not turn an existing implicit directory into a file when
  descendants exist.
- After any public fsspec write, move, delete, or mkdir operation, no file path
  may also be an ancestor of another node.

### R2. Recursive Move Safety

Recursive directory move must not be implemented as a lossy copy/delete workflow.

Acceptance criteria:

- If a role writes `/dir/new.txt` while another role moves `/dir` to `/archive`,
  the new file is moved, remains at source after serialized execution, or the
  writer gets a clear error.
- The new file must not be silently deleted.
- Moving a directory into itself is rejected.

### R3. Read Handle Consistency

Long-lived read handles must not return a mixed version.

Acceptance criteria:

- If a read handle reads one range, another role overwrites the file, and the
  original handle reads another range, the handle returns a stable snapshot or
  raises a conflict.
- It must not return old bytes for one range and new bytes for another range.

### R4. Transaction Isolation for Shared Filesystem Instances

Transaction state must not leak across threads or concurrent tasks using the same
`DjangoFileSystem` instance.

Acceptance criteria:

- A rollback in one role must not discard another role's independent write.
- A transaction in one thread must not capture normal writes from another thread
  unless the caller explicitly shares that transaction context.

### R5. Recursive Copy Contract

Recursive copy behavior during source mutation must be explicit.

Acceptance criteria:

- If recursive copy is not a snapshot operation, documentation must say so.
- If snapshot semantics are implemented, the operation must detect source changes
  or lock the source namespace while copying.
- A non-snapshot recursive copy result must not be presented as a backup-quality
  point-in-time copy.

### R6. fsck and Repair Coverage

`fsspec_fsck` and `fsspec_repair` must cover structural damage that can exist in
older databases or after external tampering.

Acceptance criteria:

- `fsspec_fsck` reports file paths that have descendants.
- `fsspec_repair --dry-run` reports structural damage without moving data.
- Explicit repair flags are required for path-conflict recovery.
- Existing safe repairs for metadata, free-block flags, sequence gaps, directory
  mappings, and orphaned used blocks continue to work.

### R7. Defensive Reads and Copy Safety

The read path must support configurable integrity checks so damaged bytes are
not silently treated as trustworthy data.

Acceptance criteria:

- Add an integrity policy such as `off`, `metadata`, and `checksum`.
- Whole-file reads and range reads should fail with a dedicated integrity error
  when required checks fail.
- Backup-like operations can pass an explicit integrity policy so corrupted
  content is not copied into a new healthy-looking file.
- The default application mode must remain compatible and low-overhead, but
  strict mode must be available and documented.

### R8. Tolerant Listing and Degraded Operation

The filesystem must provide an operational mode that lets healthy objects remain
available while damaged objects are isolated.

Acceptance criteria:

- Listing APIs can run in a tolerant mode that skips or marks corrupt entries
  instead of aborting the entire directory traversal.
- Corrupt entries should be representable as structured metadata such as
  `{"type": "corrupt", "error": "..."}` for management tools.
- Tolerant mode must be opt-in or configuration-controlled; normal application
  mode can remain fail-fast.
- Degraded mode must never invent file bytes or silently repair content.

### R9. Dirty-Graph Write Protection

Write, delete, and move operations must not expand existing database damage.

Acceptance criteria:

- Before overwriting, deleting, or moving a file, perform a lightweight
  integrity check on the target's block graph.
- If the target has shared blocks, non-contiguous mappings, references to free
  blocks, or directory/file type conflicts, fail with a repair-required error.
- The error should point operators to `fsspec_repair --dry-run`.
- A bypass, if added, must be explicit and management-only.

### R10. Incident Modes

The system should clearly separate normal operation, degraded operation, and
repair operation.

Acceptance criteria:

- `strict` mode fails fast on integrity problems.
- `degraded` mode keeps healthy files listable/readable while surfacing corrupt
  entries.
- `repair` mode is limited to management commands that audit and mutate damaged
  metadata with explicit flags.
- Mode behavior must be covered by tests and documented after implementation.

## Adversarial Test Matrix

| ID | Scenario | Expected behavior after hardening |
|----|----------|-----------------------------------|
| A1 | Concurrent create of `/a` as a file and `/a/b.txt` as a child file | At most one role succeeds. After both finish, no file path is also an ancestor of another file. |
| A2 | Concurrent overwrite of `/a` while another role creates `/a/b.txt` | Same invariant as A1; overwrite must not turn an existing implicit directory into a file. |
| A3 | Recursive `fs.mv("/dir", "/archive", recursive=True)` while another role writes `/dir/new.txt` after enumeration but before source removal | The new file is either moved, preserved at source after serialization, or the writer receives a clear error. It must not be silently deleted. |
| A4 | Recursive `fs.copy("/src", "/dst", recursive=True)` while source files are overwritten | The copy either uses a documented non-snapshot contract or detects the change and fails. It must not be presented as a snapshot backup. |
| A5 | Open read handle reads first range, another role overwrites the file, then the read handle reads the next range | The handle returns a stable snapshot or raises a conflict. It must not return mixed old/new bytes. |
| A6 | Same `DjangoFileSystem` instance is shared by two threads, one inside `with fs.transaction:` and another doing normal writes | Transaction state must not leak across threads. A rollback in one role must not discard another role's independent write. |
| A7 | Concurrent append handles close in different orders | Existing append behavior remains valid: both appends persist or one role gets a conflict; data must not be interleaved incorrectly. |
| A8 | Recursive delete races with append/overwrite on a child path | The outcome may be last-writer-wins or clear failure, but `fsspec_fsck` must pass and no orphaned live mappings remain. |
| D1 | Damager corrupts block bytes or block checksum, then Reader reads with strict integrity | Reader gets a dedicated integrity error and no corrupted bytes are returned. |
| D2 | Damager corrupts one file in a directory, then Operator lists in tolerant mode | Healthy entries are returned; corrupt entry is skipped or marked with structured error metadata. |
| D3 | Damager corrupts source content, then Copier runs copy/backup operation with explicit integrity checks | Copy refuses to create a healthy-looking destination. Default copy remains compatible and low-overhead. |
| D4 | Damager creates a shared block graph, then Writer overwrites one affected file | Write fails with repair-required error instead of freeing or mutating a block still referenced elsewhere. |
| D5 | Damager creates path-tree conflict, then Operator runs `fsspec_repair --dry-run` | Repair reports structural damage without moving data. |

Use `TransactionTestCase` for real transaction boundaries. Prefer barriers and
separate database connections over mock-only conflict simulation. SQLite may
surface coarse `database is locked` behavior; the same tests should still run in
the GitHub e2e matrix for PostgreSQL, MySQL, and Oracle.

Database-damage tests may directly mutate ORM rows to create states that the
public fsspec API should never create. Those tests should verify that defensive
code contains the damage, preserves healthy objects, and avoids silent
propagation.

## Acceptance Criteria

- The new adversarial tests fail on the current vulnerable behavior and pass
  after the fixes.
- `python -m pytest tests/ -q --cov=django_fsspec --cov-report=term-missing`
  remains above the 95% coverage threshold.
- GitHub CI passes on supported Python/Django combinations and e2e databases.
- `fsspec_repair --dry-run` gives a non-destructive audit path for every new
  structural repair.
