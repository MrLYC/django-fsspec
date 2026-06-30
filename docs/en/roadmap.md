# Roadmap

This roadmap describes the intended evolution of `django-fsspec` after the
0.2.x line. It is organized by target effect rather than feature labels, so
each item can be judged by the operational result it should produce.

Migration-history compatibility is not a roadmap item. The current direction is
to keep the reset migration history, remove the old `RechunkOperation` API, and
use `fsspec_rechunk` as the explicit block-size rewrite tool.

## Review Lens

The roadmap is based on adversarial review from several common project roles:

- Maintainer: release artifacts, tags, docs, and CI should not drift.
- Operator: dirty database state should not block healthy flows, and repair
  commands should be predictable under incident pressure.
- DBA: block size, binary-field storage, transaction behavior, and cleanup cost
  should be visible before deployment.
- Application developer: common fsspec workflows should either work consistently
  or fail with clear unsupported-operation behavior.
- Security reviewer: namespace and auth boundaries should be documented without
  implying guarantees the storage layer does not enforce.

Items are prioritized when more than one role exposes the same operational risk.

## Target Outcomes

| Area | Target effect | Evidence of completion |
|------|---------------|------------------------|
| Release discipline | A tag can be cut without manual version, package, or changelog drift | Release checklist and CI guards pass before every tag |
| Operations | Operators can inspect, repair, rechunk, and clean up data with predictable commands | JSON output, stable exit codes, and documented runbooks |
| Database fit | Users can choose block size and database settings from measured data | Block-size matrix and database-specific guidance are published |
| Integrity | Damaged data is detected early and never silently turned into healthy-looking data | Fsck/repair/rechunk scenarios cover common destructive cases |
| Ecosystem | fsspec and Django users understand supported behavior and limits | Compatibility tests and integration docs cover expected workflows |

## P0: Release Discipline

Goal: make releases reproducible and hard to publish in an inconsistent state.

Planned work:

- Add a release checklist covering version tag, changelog section, wheel
  contents, migration checks, e2e tests, benchmark smoke, and publish workflow.
- Add CI checks that verify tag version equals wheel version and that built
  wheels exclude `demo/`, top-level `tests/`, and generated-only local files.
- Require docs and changelog updates for user-visible changes before tagging.
- Keep publish on `v*` tags, with `main` CI green before a tag is created.

Target effect:

- A maintainer can tag a release and know the generated package, docs, and CI
  state describe the same version.

## P1: Operational Closure

Goal: make `fsck`, `repair`, `rechunk`, `gc`, and `stats` usable as a production
operations toolkit.

Planned work:

- Add `--json` output to `fsspec_rechunk` and align summary shape with
  `fsspec_fsck` / `fsspec_repair`.
- Define stable exit codes for operational commands:
  - `0`: completed without findings or skipped files
  - `1`: completed with findings, skipped files, or unresolved damage
  - `2`: invalid arguments or unrecoverable command failure
- Document the incident flow:
  `fsspec_fsck` -> `fsspec_repair --dry-run` -> explicit repair flags ->
  `fsspec_rechunk --dry-run` -> `fsspec_rechunk` -> `fsspec_fsck` ->
  `fsspec_gc --dry-run` -> `fsspec_gc`.
- Keep repair and rechunk separate: repair handles damaged metadata; rechunk
  rewrites healthy files to a target block size.
- Improve command output so batch runs can be audited after completion.

Target effect:

- An operator can recover from common metadata damage, rewrite block sizes, and
  clean free blocks without guessing which command is safe to run next.

## P2: Performance And Database Fit

Goal: replace block-size and database recommendations with measured,
database-specific guidance.

Planned work:

- Run the block-size matrix across SQLite, MySQL, PostgreSQL, Oracle, and any
  target domestic database environment available to maintainers.
- Compare `32KB`, `64KB`, `128KB`, and `256KB` blocks across small files, large
  files, range reads, overwrite, seeded directory operations, rechunk, and gc.
- Measure binary-field degradation risks where a database stores Django
  `BinaryField` as text/CLOB-like payloads.
- Publish recommended defaults by workload:
  - compatibility and small files: keep `32KB`
  - balanced server database use: benchmark `64KB` and `128KB`
  - large-file throughput: benchmark `128KB` and `256KB`
- Expand large benchmark reporting so ordinary read/write scenarios are always
  visible alongside seeded metadata scenarios.

Target effect:

- Users can estimate operation latency and choose a block size before deploying,
  instead of relying on a one-size-fits-all default.

## P3: Ecosystem And Product Surface

Goal: make the project easier to adopt in existing Django and fsspec workflows
without broadening the core storage contract prematurely.

Planned work:

- Expand fsspec compatibility tests for common file and directory operations,
  transaction behavior, append behavior, and explicit non-supported APIs.
- Document namespace boundaries clearly: namespaces partition paths, but direct
  fsspec API callers are still trusted application code.
- Improve WebDAV/Auth integration docs without presenting namespace as an
  authorization boundary.
- Evaluate a Django `Storage` backend only after operational and benchmark
  behavior is stable.
- Design backup/export verification commands that can produce a manifest,
  verify checksums, and support restore drills.

Target effect:

- Application developers can integrate `django-fsspec` with fewer hidden
  assumptions, and operators have a path toward backup verification beyond
  best-effort repair.

## Non-Goals

- Do not restore migration compatibility for the removed test-only rechunk
  migration.
- Do not automatically rewrite user data during package migrations.
- Do not make `fsspec_repair` invent or recover bytes that no longer have a
  trustworthy owner.
- Do not treat namespace as a standalone security boundary for direct API
  callers.
