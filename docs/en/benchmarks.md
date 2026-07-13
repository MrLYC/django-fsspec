# Benchmarks

`benchmarks/run.py` measures django-fsspec behavior across supported database backends. The benchmark runner is intentionally split into a small CI scale and larger manually-triggered scales so normal pull request feedback stays fast while large-table behavior remains measurable.

## Running locally

```bash
# Default CI-scale benchmark against the configured SQLite backend
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1

# Run one scenario
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario write_small

# Run one scenario with a specific block size
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario write_large --block-size 32768

# Save JSON output
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --json /tmp/bench.json
```

`--db` is a result label. The actual Django database backend is selected before startup with `DJANGO_FSSPEC_BENCH_DB`. `--block-size` overrides `DJANGO_FSSPEC_BLOCK_SIZE` for the benchmark process and is recorded in each JSON result as `block_size`.

## Test and E2E configuration

Unit tests, E2E tests, benchmarks, and manual management commands all use the shared demo Django project in `demo.settings`. The demo project is kept outside the installable `django_fsspec` package, and tests live in the top-level `tests/` directory so neither `demo/` nor tests are shipped in the wheel.

The settings module selects the database backend from `DJANGO_FSSPEC_BENCH_DB` for E2E and benchmark runs:

| Value | Backend |
|-------|---------|
| unset | In-memory SQLite for unit tests |
| `sqlite` | File-backed SQLite benchmark database |
| `mysql` | MySQL using `MYSQL_*` environment variables |
| `postgres` | PostgreSQL using `POSTGRES_*` environment variables |
| `oracle` | Oracle using `ORACLE_*` environment variables |

`benchmarks/e2e_test.py` validates behavior against the selected real database backend. SQLite intentionally skips concurrent-write scenarios because SQLite serializes writes, while MySQL, PostgreSQL, and Oracle run the full concurrency set in CI.

The E2E suite covers these user-facing workflows:

| Area | Coverage |
|------|----------|
| Core file API | write, read, overwrite, empty files, multi-block files, range reads, checksum verification |
| Directory semantics | listing, implicit directories, durable empty directories, recursive delete, recursive copy/move, `find`/`tree` views |
| Conflict handling | file-vs-directory path conflicts, implicit directory targets, existing move destinations, root/delete safety |
| Namespace behavior | same paths isolated across namespaces and mixed file/tree namespace conflicts |
| fsspec interoperability | `pipe`, `cat`, `ls`, `find`, `mv`, `copy`, `rm`, mixed use with lower-level operations APIs, and local cache wrappers (`filecache`, `simplecache`, `blockcache`, `cached`) |
| Operational runbooks | `fsspec_migrate` roundtrip, `fsck`/`repair`, `rechunk`/`gc`, JSON output, and attention exit codes |
| Transactions | commit, rollback, rollback after conflicting tree workflow, unclosed write handles, and block cleanup |
| Concurrency | different-file writes, same-file overwrites, same-file appends, read/write interleaving, delete/list races, block-pool integrity |

## Full local validation

Run these before publishing or when changing storage semantics:

```bash
uv sync --extra dev --frozen
uv run python -m pytest tests/ -q --cov=django_fsspec --cov-report=term-missing
DJANGO_SETTINGS_MODULE=demo.settings uv run python -m django makemigrations --check --dry-run
uv run python demo/manage.py check
uv run python benchmarks/e2e_test.py
uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario write_small --json /tmp/django-fsspec-benchmark-smoke.json
uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario cache_filecache_read_large --json /tmp/django-fsspec-cache-smoke.json
uv run python -m build --wheel --outdir /tmp/django-fsspec-build-check
```

After building a wheel, verify that `demo/`, top-level `tests/`, and `django_fsspec/tests/` are not present in the wheel contents. The generated `django_fsspec/_version.py` should appear in the wheel, but it is ignored in the repository because it is produced by `hatch-vcs`.

## Scales

| Scale | Purpose | Seeded files | Seeded directories | Seeded operation repeats | Seeded `find` repeats |
|-------|---------|--------------|--------------------|--------------------------|-----------------------|
| `ci` | Fast push/PR benchmark and smoke testing | 100 | 10 | 25 | 1 |
| `small` | Manual bridge scale between CI and larger seeded runs | 1,000 | 50 | 100 | 5 |
| `medium` | Manual moderate large-table benchmark | 10,000 | 100 | 250 | 5 |
| `large` | Manual large-table benchmark | 50,000 | 500 | 500 | 3 |

All scales keep the original fixed operation counts for write/read/delete/list/concurrent scenarios. Push/PR CI runs `--scale ci --seed 1` only. The `small` manual scale exists to avoid jumping directly from CI's 100 seeded files to medium's 10,000 seeded files.

Cache scenarios are included in the default CI-scale benchmark set:

| Scenario | What it measures | CI repeats | Small | Medium | Large |
|----------|------------------|------------|-------|--------|-------|
| `cache_filecache_read_large` | Hot whole-file reads after the first `filecache` copy of a 1MB file | 50 | 100 | 250 | 500 |
| `cache_simplecache_read_large` | Hot whole-file reads after the first `simplecache` copy of a 1MB file | 50 | 100 | 250 | 500 |
| `cache_blockcache_seek_read` | Repeated 4KB seek reads from a 1MB file through `blockcache` with 64KB cache blocks | 100 | 200 | 500 | 1000 |

The `cached` alias is covered by E2E tests and shares the same implementation as
`blockcache`, so it is not duplicated in the benchmark matrix.

## Block-size comparisons

Some database backends expose Django `BinaryField` through text/CLOB-like storage. For those implementations, 256KB rows can be slower than smaller chunks because encoding, memory copies, redo/undo logs, and out-of-row LOB handling become more visible. `django-fsspec` defaults to 32KB as a conservative small-file and broad-database baseline. Use the same database and scale with multiple block sizes before overriding it in production:

```bash
for bs in 32768 65536 131072 262144; do
  DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py \
    --db "sqlite-bs-${bs}" \
    --scale small \
    --seed 1 \
    --scenario write_large \
    --block-size "$bs" \
    --json "/tmp/django-fsspec-write-large-bs-${bs}.json"
done
```

For broad coverage, run at least `write_large`, `read_large`, `seek_read`, `overwrite`, `concurrent_write`, and one seeded scenario such as `seeded_find`. The manual GitHub Actions workflow defaults to `block_size_kb=32` and can also run a full block-size matrix by setting `block_size_kb` to `all`.

## Performance expectations

The ranges below are derived from the real GitHub Actions data in [Detailed GitHub data](#detailed-github-data). Treat them as directional expectations for the same benchmark scenarios, not as production capacity limits. GitHub Actions runner placement, database container warm-up, and host load can move individual numbers between runs.

### CI-scale operation ranges

CI scale is the best apples-to-apples comparison across all supported database backends because each row comes from the same successful CI run. MySQL and PostgreSQL ranges include both Django 4.2 and Django 5.2 jobs.

| Operation family | SQLite | MySQL 8.0 | PostgreSQL 16 | Oracle 23 | Practical expectation |
|------------------|--------|-----------|---------------|-----------|-----------------------|
| Small/medium writes | 4.21-4.38ms | 7.23-8.07ms | 6.24-6.59ms | 6.29-6.54ms | Small files should stay in single-digit milliseconds; server databases add roughly a few milliseconds over SQLite. |
| Large writes, 1 MB | 8.44ms | 29.95-30.17ms | 29.93-33.39ms | 14.04ms | Large writes are dominated by block persistence; expect low tens of milliseconds on networked databases. |
| Reads and seeks | 1.44-1.87ms | 2.44-4.57ms | 2.54-9.48ms | 2.81-5.32ms | Reads are usually single-digit milliseconds. PostgreSQL large reads were the slowest networked read path in this CI run. |
| Overwrite/delete | 3.40-5.80ms | 6.41-11.56ms | 5.03-9.50ms | 5.11-9.32ms | Mutating existing paths usually stays in single-digit to low double-digit milliseconds. |
| Directory listing | 4.03-4.28ms | 5.87-7.09ms | 6.54-7.17ms | 5.44-7.58ms | Bounded flat and shallow nested listings remain close across server databases. |
| 8-thread concurrency | read-only 2.14ms; write-heavy locked | 2.84-7.83ms | 2.37-6.74ms | 3.16-7.19ms | Use a server database for concurrent writes. SQLite is fine for local or read-heavy work, but write-heavy concurrent scenarios hit `database is locked`. |

### Seeded scale expectations

The published manual seeded runs below show what changes when the metadata table already contains 10,000 or 50,000 files. Dataset creation is excluded from timing, so these numbers measure operations on an existing tree. New manual runs can also use `--scale small` for a 1,000-file bridge between CI and medium.

| Operation | Medium scale, 10,000 files | Large scale, 50,000 files | Practical expectation |
|-----------|----------------------------|---------------------------|-----------------------|
| Direct `info` | 0.33-0.72ms | 0.38-0.74ms | Direct metadata lookup remains effectively flat because it is index-friendly. |
| `exists` | 0.85-2.07ms | 1.04-6.54ms | Existence checks remain cheap, but missing/present mix and backend query planning can become visible at larger sizes. |
| Deep directory `ls` | 4.01-7.90ms | 4.99-18.61ms | Listing one populated deep directory grows moderately with scale. |
| Root `ls` | 13.14-26.86ms | 51.53-124.37ms | Root listing scales with the amount of indexed path volume under the root and should not be a hot-path primitive for large trees. |
| Recursive `find` | 100.83-144.07ms | 494.83-753.99ms | Recursive tree scans scale close to linearly with file count; avoid frequent full-tree `find` in request paths. |

Backend fit in these results is straightforward: SQLite is appropriate for unit tests, local development, and low-concurrency deployments; use MySQL, PostgreSQL, or Oracle for concurrent writes. PostgreSQL is the most balanced server backend for the measured concurrent and recursive `find` workloads. Oracle performs especially well on large writes, large root listing, and large `exists` in these runs. MySQL remains viable, but large writes and recursive `find` are comparatively slower in the captured data.

Django version changes are measurable but not the main factor. In the latest CI run, MySQL 8.0 under Django 5.2 is about 6.1% lower latency on average than Django 4.2 across the measured table. PostgreSQL 16 under Django 5.2 is about 3.8% lower latency on average, with individual scenarios ranging from a small regression to a larger improvement.

One interpretation detail matters for `small`, `medium`, and `large`: the common read/write/delete/list/concurrency scenarios keep the same fixed operation counts as CI and reset the database before each scenario. The larger scale changes the seeded dataset size and seeded scenario repeat counts. That means the common read/write rows in the manual seeded artifacts are useful for confirming normal operation behavior in the same workflow, while the seeded rows are the scale-sensitive large-table measurements.

## Detailed GitHub data

CI-scale data was collected on GitHub Actions on 2026-06-30 from successful CI run [`28412676243`](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) at commit `2236341`. Medium and large seeded data was collected from manually triggered Large Benchmark runs on 2026-06-29. Those seeded runs are retained as the latest available large-table reference artifacts; compare them by scale and scenario rather than treating them as the same commit as the latest CI-scale table.

| Artifact | Run | Commit | Scope |
|----------|-----|--------|-------|
| `benchmark-sqlite` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI scale, SQLite |
| `benchmark-mysql-8.0-django-4.2` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI scale, MySQL 8.0 with Django 4.2 |
| `benchmark-mysql-8.0-django-5.2` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI scale, MySQL 8.0 with Django 5.2 |
| `benchmark-postgres-16-django-4.2` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI scale, PostgreSQL 16 with Django 4.2 |
| `benchmark-postgres-16-django-5.2` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI scale, PostgreSQL 16 with Django 5.2 |
| `benchmark-oracle` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI scale, Oracle 23 |
| `large-benchmark-sqlite-medium-seed-1` | [28381604379](https://github.com/MrLYC/django-fsspec/actions/runs/28381604379) | `eb31d73` | Medium scale, SQLite |
| `large-benchmark-mysql-medium-seed-1` | [28381612421](https://github.com/MrLYC/django-fsspec/actions/runs/28381612421) | `eb31d73` | Medium scale, MySQL 8.0 |
| `large-benchmark-postgres-medium-seed-1` | [28381595934](https://github.com/MrLYC/django-fsspec/actions/runs/28381595934) | `eb31d73` | Medium scale, PostgreSQL 16 |
| `large-benchmark-oracle-medium-seed-1` | [28381618404](https://github.com/MrLYC/django-fsspec/actions/runs/28381618404) | `eb31d73` | Medium scale, Oracle 23 |
| `large-benchmark-sqlite-large-seed-1` | [28373589555](https://github.com/MrLYC/django-fsspec/actions/runs/28373589555) | `205aee6` | Large scale, SQLite |
| `large-benchmark-mysql-large-seed-1` | [28373568411](https://github.com/MrLYC/django-fsspec/actions/runs/28373568411) | `205aee6` | Large scale, MySQL 8.0 |
| `large-benchmark-postgres-large-seed-1` | [28373362314](https://github.com/MrLYC/django-fsspec/actions/runs/28373362314) | `205aee6` | Large scale, PostgreSQL 16 |
| `large-benchmark-oracle-large-seed-1` | [28373585625](https://github.com/MrLYC/django-fsspec/actions/runs/28373585625) | `205aee6` | Large scale, Oracle 23 |

Format: average latency / throughput. Successful concurrent results include the configured `8t` thread-count suffix in the `op` name. SQLite `concurrent_write` and `concurrent_mixed` report `database is locked`; that is expected for SQLite's serialized write model and is recorded as a benchmark outcome instead of being hidden.

### CI-scale results

| Scenario | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PostgreSQL 16 / Django 4.2 | PostgreSQL 16 / Django 5.2 | Oracle 23 |
|----------|--------|------------------------|------------------------|----------------------------|----------------------------|-----------|
| `write_small` | 4.21ms / 237 ops/s | 7.74ms / 129 ops/s | 7.23ms / 138 ops/s | 6.59ms / 152 ops/s | 6.36ms / 157 ops/s | 6.29ms / 159 ops/s |
| `write_medium` | 4.38ms / 228 ops/s | 8.07ms / 124 ops/s | 7.70ms / 130 ops/s | 6.55ms / 153 ops/s | 6.24ms / 160 ops/s | 6.54ms / 153 ops/s |
| `write_large` | 8.44ms / 118 ops/s | 30.17ms / 33 ops/s | 29.95ms / 33 ops/s | 33.39ms / 30 ops/s | 29.93ms / 33 ops/s | 14.04ms / 71 ops/s |
| `read_small` | 1.44ms / 693 ops/s | 2.62ms / 381 ops/s | 2.44ms / 409 ops/s | 2.96ms / 338 ops/s | 2.54ms / 393 ops/s | 2.81ms / 355 ops/s |
| `read_large` | 1.87ms / 536 ops/s | 4.57ms / 219 ops/s | 4.26ms / 235 ops/s | 9.45ms / 106 ops/s | 9.48ms / 105 ops/s | 5.32ms / 188 ops/s |
| `overwrite` | 5.80ms / 172 ops/s | 11.56ms / 86 ops/s | 10.75ms / 93 ops/s | 9.50ms / 105 ops/s | 9.05ms / 110 ops/s | 9.32ms / 107 ops/s |
| `ls_flat_1000` | 4.28ms / 234 ops/s | 7.09ms / 141 ops/s | 6.79ms / 147 ops/s | 6.95ms / 144 ops/s | 6.54ms / 153 ops/s | 7.58ms / 132 ops/s |
| `ls_nested_100dirs` | 4.03ms / 248 ops/s | 6.15ms / 163 ops/s | 5.87ms / 170 ops/s | 7.17ms / 139 ops/s | 6.64ms / 151 ops/s | 5.44ms / 184 ops/s |
| `delete` | 3.40ms / 294 ops/s | 7.01ms / 143 ops/s | 6.41ms / 156 ops/s | 5.19ms / 193 ops/s | 5.03ms / 199 ops/s | 5.11ms / 196 ops/s |
| `seek_read` | 1.59ms / 627 ops/s | 3.35ms / 299 ops/s | 3.14ms / 319 ops/s | 5.34ms / 187 ops/s | 6.25ms / 160 ops/s | 3.59ms / 279 ops/s |
| `concurrent_write_8t` | ERROR: database is locked | 7.83ms / 128 ops/s | 7.62ms / 131 ops/s | 6.56ms / 152 ops/s | 6.74ms / 148 ops/s | 7.19ms / 139 ops/s |
| `concurrent_read_8t` | 2.14ms / 467 ops/s | 3.19ms / 313 ops/s | 2.84ms / 353 ops/s | 2.73ms / 366 ops/s | 2.37ms / 423 ops/s | 3.16ms / 316 ops/s |
| `concurrent_mixed_8t` | ERROR: database is locked | 5.11ms / 196 ops/s | 4.62ms / 216 ops/s | 4.30ms / 233 ops/s | 4.21ms / 237 ops/s | 4.58ms / 218 ops/s |

### Medium common-operation results

These rows come from the same `--scale medium --seed 1` artifacts as the seeded results. Common scenarios use the fixed operation counts from [Default CI scenarios](#default-ci-scenarios) and reset the database before each scenario, so they are not measured against the 10,000-file seeded tree.

| Scenario | SQLite / Django 5.2.15 | MySQL 8.0 / Django 5.2.15 | PostgreSQL 16 / Django 5.2.15 | Oracle 23 / Django 5.2.15 |
|----------|------------------------|----------------------------|--------------------------------|---------------------------|
| `write_small` | 4.32ms / 232 ops/s | 7.98ms / 125 ops/s | 6.20ms / 161 ops/s | 6.41ms / 156 ops/s |
| `write_medium` | 4.06ms / 246 ops/s | 8.44ms / 118 ops/s | 6.12ms / 164 ops/s | 6.91ms / 145 ops/s |
| `write_large` | 8.09ms / 124 ops/s | 32.49ms / 31 ops/s | 28.17ms / 35 ops/s | 15.71ms / 64 ops/s |
| `read_small` | 1.25ms / 800 ops/s | 2.53ms / 396 ops/s | 2.48ms / 402 ops/s | 2.54ms / 393 ops/s |
| `read_large` | 1.76ms / 569 ops/s | 4.31ms / 232 ops/s | 8.58ms / 117 ops/s | 5.09ms / 196 ops/s |
| `overwrite` | 4.57ms / 219 ops/s | 9.85ms / 102 ops/s | 7.77ms / 129 ops/s | 8.16ms / 123 ops/s |
| `ls_flat_1000` | 3.97ms / 252 ops/s | 6.91ms / 145 ops/s | 6.51ms / 154 ops/s | 7.72ms / 130 ops/s |
| `ls_nested_100dirs` | 3.79ms / 264 ops/s | 6.05ms / 165 ops/s | 6.33ms / 158 ops/s | 5.43ms / 184 ops/s |
| `delete` | 2.51ms / 398 ops/s | 5.51ms / 181 ops/s | 3.65ms / 274 ops/s | 3.79ms / 264 ops/s |
| `seek_read` | 1.42ms / 706 ops/s | 3.40ms / 295 ops/s | 4.98ms / 201 ops/s | 3.59ms / 278 ops/s |
| `concurrent_write_8t` | ERROR: database is locked | 6.35ms / 157 ops/s | 4.90ms / 204 ops/s | 6.02ms / 166 ops/s |
| `concurrent_read_8t` | 2.02ms / 495 ops/s | 2.95ms / 339 ops/s | 2.32ms / 430 ops/s | 3.20ms / 312 ops/s |
| `concurrent_mixed_8t` | ERROR: database is locked | 4.12ms / 243 ops/s | 3.48ms / 288 ops/s | 3.93ms / 254 ops/s |

### Medium seeded results

These runs use `--scale medium --seed 1`, which seeds 10,000 files across 100 directories. Dataset creation is excluded from the measured timings.

| Scenario | SQLite / Django 5.2.15 | MySQL 8.0 / Django 5.2.15 | PostgreSQL 16 / Django 5.2.15 | Oracle 23 / Django 5.2.15 |
|----------|------------------------|----------------------------|--------------------------------|---------------------------|
| `seeded_ls_root` | 13.21ms / 76 ops/s | 21.62ms / 46 ops/s | 26.86ms / 37 ops/s | 13.14ms / 76 ops/s |
| `seeded_ls_deep` | 4.01ms / 250 ops/s | 7.90ms / 127 ops/s | 6.04ms / 165 ops/s | 4.21ms / 237 ops/s |
| `seeded_exists` | 0.99ms / 1014 ops/s | 2.07ms / 483 ops/s | 1.53ms / 652 ops/s | 0.85ms / 1171 ops/s |
| `seeded_info` | 0.33ms / 3076 ops/s | 0.72ms / 1381 ops/s | 0.69ms / 1448 ops/s | 0.64ms / 1568 ops/s |
| `seeded_find` | 122.00ms / 8 ops/s | 144.07ms / 7 ops/s | 100.83ms / 10 ops/s | 140.27ms / 7 ops/s |

### Large common-operation results

These rows come from the same `--scale large --seed 1` artifacts as the seeded results. Common scenarios again use fixed operation counts and reset the database before each scenario; they are included here so the manual large benchmark also shows normal read/write behavior.

| Scenario | SQLite | MySQL 8.0 | PostgreSQL 16 | Oracle 23 |
|----------|--------|-----------|---------------|-----------|
| `write_small` | 4.29ms / 233 ops/s | 7.83ms / 128 ops/s | 6.24ms / 160 ops/s | 6.82ms / 147 ops/s |
| `write_medium` | 4.41ms / 227 ops/s | 8.23ms / 121 ops/s | 6.05ms / 165 ops/s | 7.11ms / 141 ops/s |
| `write_large` | 8.26ms / 121 ops/s | 31.83ms / 31 ops/s | 29.08ms / 34 ops/s | 15.84ms / 63 ops/s |
| `read_small` | 1.43ms / 700 ops/s | 2.53ms / 396 ops/s | 2.49ms / 402 ops/s | 2.79ms / 359 ops/s |
| `read_large` | 1.84ms / 545 ops/s | 4.48ms / 223 ops/s | 8.56ms / 117 ops/s | 5.92ms / 169 ops/s |
| `overwrite` | 4.92ms / 203 ops/s | 10.07ms / 99 ops/s | 7.89ms / 127 ops/s | 8.59ms / 116 ops/s |
| `ls_flat_1000` | 4.28ms / 234 ops/s | 7.02ms / 142 ops/s | 6.29ms / 159 ops/s | 8.43ms / 119 ops/s |
| `ls_nested_100dirs` | 4.03ms / 248 ops/s | 6.11ms / 164 ops/s | 6.31ms / 158 ops/s | 5.78ms / 173 ops/s |
| `delete` | 2.71ms / 369 ops/s | 5.47ms / 183 ops/s | 3.79ms / 264 ops/s | 4.07ms / 246 ops/s |
| `seek_read` | 1.58ms / 633 ops/s | 3.29ms / 304 ops/s | 4.81ms / 208 ops/s | 3.93ms / 254 ops/s |
| `concurrent_write_8t` | ERROR: database is locked | 5.90ms / 170 ops/s | 5.10ms / 196 ops/s | 6.28ms / 159 ops/s |
| `concurrent_read_8t` | 2.33ms / 430 ops/s | 2.88ms / 347 ops/s | 2.29ms / 437 ops/s | 3.38ms / 296 ops/s |
| `concurrent_mixed_8t` | ERROR: database is locked | 4.07ms / 246 ops/s | 3.51ms / 285 ops/s | 4.31ms / 232 ops/s |

### Large seeded results

These runs use `--scale large --seed 1`, which seeds 50,000 files across 500 directories. Dataset creation is excluded from the measured timings. The manual Large Benchmark workflow does not run a Django-version matrix; it installs the project's normal `django>=4.2,<6.0` dependency set at run time.

| Scenario | SQLite | MySQL 8.0 | PostgreSQL 16 | Oracle 23 |
|----------|--------|-----------|---------------|-----------|
| `seeded_ls_root` | 60.14ms / 17 ops/s | 86.93ms / 12 ops/s | 124.37ms / 8 ops/s | 51.53ms / 19 ops/s |
| `seeded_ls_deep` | 8.97ms / 111 ops/s | 18.61ms / 54 ops/s | 10.81ms / 93 ops/s | 4.99ms / 200 ops/s |
| `seeded_exists` | 3.39ms / 295 ops/s | 6.54ms / 153 ops/s | 3.99ms / 251 ops/s | 1.04ms / 961 ops/s |
| `seeded_info` | 0.38ms / 2626 ops/s | 0.73ms / 1370 ops/s | 0.65ms / 1542 ops/s | 0.74ms / 1356 ops/s |
| `seeded_find` | 644.41ms / 2 ops/s | 736.55ms / 1 ops/s | 494.83ms / 2 ops/s | 753.99ms / 1 ops/s |

## Default CI scenarios

These scenarios run by default for `--scale ci`. Result `op` names are stable for a given scale, and some include configured sizes or thread counts such as `ls_flat_1000` and `concurrent_write_8t`:

| Scenario | Design |
|----------|--------|
| `write_small` | Write 1,000 files of 100 B each. |
| `write_medium` | Write 200 files of 10 KB each. |
| `write_large` | Write 50 files of 1 MB each. |
| `read_small` | Pre-create 1,000 files of 100 B each, then time reads. |
| `read_large` | Pre-create 50 files of 1 MB each, then time reads. |
| `overwrite` | Overwrite the same file 500 times. |
| `ls_flat` | Create 1,000 files in one directory, then list it 100 times. |
| `ls_nested` | Create 100 directories with 10 files each, then list the parent 100 times. |
| `delete` | Pre-create 500 files, then time deletes. |
| `seek_read` | Create a 1 MB file, then perform 100 deterministic random seek/read operations. |
| `concurrent_write` | 8 threads write 100 files, preserving all operations across thread batches. |
| `concurrent_read` | 8 threads read 100 pre-created files. |
| `concurrent_mixed` | 8 threads perform 200 mixed read/write operations. |

## Seeded large-table scenarios

Seeded scenarios build a deterministic dataset under `/bench/seeded` before timing. Dataset creation is not included in operation timings. The `--seed` value controls path distribution so repeated runs are comparable while alternate seeds can vary directory placement.

`--scale small`, `--scale medium`, and `--scale large` include these scenarios by default. They can also be selected explicitly with `--scenario` at any scale.

| Scenario | Design |
|----------|--------|
| `seeded_ls_root` | Repeatedly list `/bench/seeded` after pre-seeding the configured dataset. |
| `seeded_ls_deep` | Repeatedly list one deterministic populated deep directory. |
| `seeded_exists` | Repeatedly check a 50/50 mix of existing and missing paths. |
| `seeded_info` | Repeatedly fetch metadata for deterministic existing paths. |
| `seeded_find` | Repeatedly run recursive `find` over `/bench/seeded`; repeat count is intentionally lower because this scans the whole seeded tree. |

## GitHub Actions

Normal CI runs bounded benchmarks on every push and pull request, and uploads JSON artifacts with these metadata fields on every result:

- `db`: display label passed with `--db`
- `backend`: `DJANGO_FSSPEC_BENCH_DB`
- `scale`
- `seed`
- `block_size`: effective `DJANGO_FSSPEC_BLOCK_SIZE` in bytes

Manual seeded runs use the GitHub Actions workflow **Large Benchmark**. Inputs:

| Input | Values |
|-------|--------|
| `database` | `sqlite`, `mysql`, `postgres`, `oracle` |
| `scale` | `small`, `medium`, `large` |
| `seed` | Integer seed, default `1` |
| `scenario` | `all` or any benchmark scenario name |
| `block_size_kb` | `32` (default), `64`, `128`, `256`, or `all` |

The manual workflow runs one database at a time and uploads JSON artifacts named with database, scale, seed, and block size.
