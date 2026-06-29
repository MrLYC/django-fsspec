# Benchmarks

`benchmarks/run.py` measures django-fsspec behavior across supported database backends. The benchmark runner is intentionally split into a small CI scale and larger manually-triggered scales so normal pull request feedback stays fast while large-table behavior remains measurable.

## Running locally

```bash
# Default CI-scale benchmark against the configured SQLite backend
DJANGO_FSSPEC_BENCH_DB=sqlite python benchmarks/run.py --db sqlite --scale ci --seed 1

# Run one scenario
DJANGO_FSSPEC_BENCH_DB=sqlite python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario write_small

# Save JSON output
DJANGO_FSSPEC_BENCH_DB=sqlite python benchmarks/run.py --db sqlite --scale ci --seed 1 --json /tmp/bench.json
```

`--db` is a result label. The actual Django database backend is selected before startup with `DJANGO_FSSPEC_BENCH_DB`.

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
| fsspec interoperability | `pipe`, `cat`, `ls`, `find`, `mv`, `copy`, `rm`, and mixed use with lower-level operations APIs |
| Transactions | commit, rollback, rollback after conflicting tree workflow, unclosed write handles, and block cleanup |
| Concurrency | different-file writes, same-file overwrites, same-file appends, read/write interleaving, delete/list races, block-pool integrity |

## Full local validation

Run these before publishing or when changing storage semantics:

```bash
python -m pytest tests/ -q --cov=django_fsspec --cov-report=term-missing
DJANGO_SETTINGS_MODULE=demo.settings python -m django makemigrations --check --dry-run
python demo/manage.py check
python benchmarks/e2e_test.py
python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario write_small --json /tmp/django-fsspec-benchmark-smoke.json
python -m build --wheel --outdir /tmp/django-fsspec-build-check
```

After building a wheel, verify that `demo/`, top-level `tests/`, and `django_fsspec/tests/` are not present in the wheel contents. The generated `django_fsspec/_version.py` should appear in the wheel, but it is ignored in the repository because it is produced by `hatch-vcs`.

## Scales

| Scale | Purpose | Seeded files | Seeded directories | Seeded operation repeats | Seeded `find` repeats |
|-------|---------|--------------|--------------------|--------------------------|-----------------------|
| `ci` | Fast push/PR benchmark and smoke testing | 100 | 10 | 25 | 1 |
| `medium` | Manual moderate large-table benchmark | 10,000 | 100 | 250 | 5 |
| `large` | Manual large-table benchmark | 50,000 | 500 | 500 | 3 |

All scales keep the original fixed operation counts for write/read/delete/list/concurrent scenarios. Push/PR CI runs `--scale ci --seed 1` only.

## Latest GitHub results

The data below was collected on GitHub Actions on 2026-06-29. CI-scale and medium-scale seeded results come from commit `eb31d73`. Large-scale seeded results come from commit `205aee6`; between `205aee6` and `eb31d73`, only benchmark documentation changed, not benchmark code, runtime code, demo settings, tests, or workflows.

| Artifact | Run | Commit | Scope |
|----------|-----|--------|-------|
| `benchmark-sqlite` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | `eb31d73` | CI scale, SQLite |
| `benchmark-mysql-8.0-django-4.2` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | `eb31d73` | CI scale, MySQL 8.0 with Django 4.2 |
| `benchmark-mysql-8.0-django-5.2` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | `eb31d73` | CI scale, MySQL 8.0 with Django 5.2 |
| `benchmark-postgres-16-django-4.2` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | `eb31d73` | CI scale, PostgreSQL 16 with Django 4.2 |
| `benchmark-postgres-16-django-5.2` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | `eb31d73` | CI scale, PostgreSQL 16 with Django 5.2 |
| `benchmark-oracle` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | `eb31d73` | CI scale, Oracle 23 |
| `large-benchmark-sqlite-medium-seed-1` | [28381604379](https://github.com/MrLYC/django-fsspec/actions/runs/28381604379) | `eb31d73` | Medium scale, SQLite |
| `large-benchmark-mysql-medium-seed-1` | [28381612421](https://github.com/MrLYC/django-fsspec/actions/runs/28381612421) | `eb31d73` | Medium scale, MySQL 8.0 |
| `large-benchmark-postgres-medium-seed-1` | [28381595934](https://github.com/MrLYC/django-fsspec/actions/runs/28381595934) | `eb31d73` | Medium scale, PostgreSQL 16 |
| `large-benchmark-oracle-medium-seed-1` | [28381618404](https://github.com/MrLYC/django-fsspec/actions/runs/28381618404) | `eb31d73` | Medium scale, Oracle 23 |
| `large-benchmark-sqlite-large-seed-1` | [28373589555](https://github.com/MrLYC/django-fsspec/actions/runs/28373589555) | `205aee6` | Large scale, SQLite |
| `large-benchmark-mysql-large-seed-1` | [28373568411](https://github.com/MrLYC/django-fsspec/actions/runs/28373568411) | `205aee6` | Large scale, MySQL 8.0 |
| `large-benchmark-postgres-large-seed-1` | [28373362314](https://github.com/MrLYC/django-fsspec/actions/runs/28373362314) | `205aee6` | Large scale, PostgreSQL 16 |
| `large-benchmark-oracle-large-seed-1` | [28373585625](https://github.com/MrLYC/django-fsspec/actions/runs/28373585625) | `205aee6` | Large scale, Oracle 23 |

Format: average latency / throughput. SQLite `concurrent_write` and `concurrent_mixed` report `database is locked`; that is expected for SQLite's serialized write model and is recorded as a benchmark outcome instead of being hidden.

### CI-scale results

| Scenario | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PostgreSQL 16 / Django 4.2 | PostgreSQL 16 / Django 5.2 | Oracle 23 |
|----------|--------|------------------------|------------------------|----------------------------|----------------------------|-----------|
| `write_small` | 4.23ms / 236 ops/s | 8.04ms / 124 ops/s | 7.13ms / 140 ops/s | 6.05ms / 165 ops/s | 5.98ms / 167 ops/s | 6.53ms / 153 ops/s |
| `write_medium` | 4.47ms / 223 ops/s | 8.39ms / 119 ops/s | 7.51ms / 133 ops/s | 6.08ms / 164 ops/s | 5.97ms / 168 ops/s | 6.91ms / 145 ops/s |
| `write_large` | 8.21ms / 122 ops/s | 31.28ms / 32 ops/s | 29.34ms / 34 ops/s | 27.14ms / 37 ops/s | 27.13ms / 37 ops/s | 15.94ms / 63 ops/s |
| `read_small` | 1.42ms / 705 ops/s | 2.58ms / 387 ops/s | 2.40ms / 416 ops/s | 2.50ms / 400 ops/s | 2.45ms / 408 ops/s | 2.68ms / 373 ops/s |
| `read_large` | 1.82ms / 549 ops/s | 4.48ms / 223 ops/s | 4.12ms / 243 ops/s | 8.18ms / 122 ops/s | 8.23ms / 121 ops/s | 5.73ms / 174 ops/s |
| `overwrite` | 4.86ms / 206 ops/s | 10.18ms / 98 ops/s | 9.15ms / 109 ops/s | 7.47ms / 134 ops/s | 7.50ms / 133 ops/s | 8.06ms / 124 ops/s |
| `ls_flat_1000` | 4.21ms / 237 ops/s | 7.02ms / 142 ops/s | 6.78ms / 148 ops/s | 6.35ms / 157 ops/s | 6.30ms / 159 ops/s | 8.21ms / 122 ops/s |
| `ls_nested_100dirs` | 3.95ms / 253 ops/s | 6.10ms / 164 ops/s | 5.84ms / 171 ops/s | 6.28ms / 159 ops/s | 6.21ms / 161 ops/s | 5.60ms / 179 ops/s |
| `delete` | 2.67ms / 375 ops/s | 5.77ms / 173 ops/s | 5.18ms / 193 ops/s | 3.81ms / 263 ops/s | 3.67ms / 273 ops/s | 3.98ms / 251 ops/s |
| `seek_read` | 1.56ms / 642 ops/s | 3.34ms / 299 ops/s | 3.05ms / 328 ops/s | 4.83ms / 207 ops/s | 4.63ms / 216 ops/s | 3.85ms / 260 ops/s |
| `concurrent_write` | ERROR: database is locked | 6.03ms / 166 ops/s | 5.47ms / 183 ops/s | 4.86ms / 206 ops/s | 4.80ms / 208 ops/s | 5.77ms / 173 ops/s |
| `concurrent_read` | 2.08ms / 481 ops/s | 3.11ms / 322 ops/s | 2.88ms / 348 ops/s | 2.32ms / 432 ops/s | 2.35ms / 426 ops/s | 3.17ms / 316 ops/s |
| `concurrent_mixed` | ERROR: database is locked | 3.94ms / 254 ops/s | 3.65ms / 274 ops/s | 3.48ms / 288 ops/s | 3.34ms / 299 ops/s | 4.09ms / 244 ops/s |

### Medium seeded results

These runs use `--scale medium --seed 1`, which seeds 10,000 files across 100 directories. Dataset creation is excluded from the measured timings.

| Scenario | SQLite / Django 5.2.15 | MySQL 8.0 / Django 5.2.15 | PostgreSQL 16 / Django 5.2.15 | Oracle 23 / Django 5.2.15 |
|----------|------------------------|----------------------------|--------------------------------|---------------------------|
| `seeded_ls_root` | 13.21ms / 76 ops/s | 21.62ms / 46 ops/s | 26.86ms / 37 ops/s | 13.14ms / 76 ops/s |
| `seeded_ls_deep` | 4.01ms / 250 ops/s | 7.90ms / 127 ops/s | 6.04ms / 165 ops/s | 4.21ms / 237 ops/s |
| `seeded_exists` | 0.99ms / 1014 ops/s | 2.07ms / 483 ops/s | 1.53ms / 652 ops/s | 0.85ms / 1171 ops/s |
| `seeded_info` | 0.33ms / 3076 ops/s | 0.72ms / 1381 ops/s | 0.69ms / 1448 ops/s | 0.64ms / 1568 ops/s |
| `seeded_find` | 122.00ms / 8 ops/s | 144.07ms / 7 ops/s | 100.83ms / 10 ops/s | 140.27ms / 7 ops/s |

### Large seeded results

These runs use `--scale large --seed 1`, which seeds 50,000 files across 500 directories. Dataset creation is excluded from the measured timings. The manual Large Benchmark workflow does not run a Django-version matrix; it installs the project's normal `django>=4.2` dependency set at run time.

| Scenario | SQLite | MySQL 8.0 | PostgreSQL 16 | Oracle 23 |
|----------|--------|-----------|---------------|-----------|
| `seeded_ls_root` | 60.14ms / 17 ops/s | 86.93ms / 12 ops/s | 124.37ms / 8 ops/s | 51.53ms / 19 ops/s |
| `seeded_ls_deep` | 8.97ms / 111 ops/s | 18.61ms / 54 ops/s | 10.81ms / 93 ops/s | 4.99ms / 200 ops/s |
| `seeded_exists` | 3.39ms / 295 ops/s | 6.54ms / 153 ops/s | 3.99ms / 251 ops/s | 1.04ms / 961 ops/s |
| `seeded_info` | 0.38ms / 2626 ops/s | 0.73ms / 1370 ops/s | 0.65ms / 1542 ops/s | 0.74ms / 1356 ops/s |
| `seeded_find` | 644.41ms / 2 ops/s | 736.55ms / 1 ops/s | 494.83ms / 2 ops/s | 753.99ms / 1 ops/s |

### Objective analysis

These numbers are useful as a directional comparison between backends under the same benchmark code, not as absolute production capacity limits. GitHub Actions runners, database container startup state, and host load can introduce run-to-run variance. The CI table is the most directly comparable set because all rows come from the same CI run. The manual medium and large seeded runs are comparable by scale and scenario, but the Large Benchmark workflow does not run a Django-version matrix.

- **CI-scale single-operation latency**: SQLite has the lowest latency for single-threaded write, read, list, delete, and seek scenarios because it runs without a networked database service. Among networked databases, PostgreSQL has the lowest small and medium write latency in the Django 5.2 row, Oracle has the lowest large-write latency, and MySQL has the slowest large-write latency in this run.
- **Read behavior**: SQLite is fastest for `read_small`, `read_large`, and `seek_read`. Among networked databases, MySQL is fastest for `read_large`, Oracle is in the middle, and PostgreSQL is slowest for large reads in this CI environment.
- **Django version impact**: MySQL 8.0 improves under Django 5.2 compared with Django 4.2 in every CI scenario measured here, with average latency about 8% lower across the table. PostgreSQL 16 is mostly flat across Django 4.2 and 5.2, with average latency about 1.4% lower under Django 5.2 and individual scenarios ranging from a small regression to a small improvement.
- **Concurrency**: SQLite reports `database is locked` for write-heavy concurrent scenarios, which matches SQLite's serialized write model. Among networked databases, PostgreSQL has the lowest concurrent write and mixed workload latency, MySQL is close but slower, and Oracle is similar for concurrent writes but slower for mixed read/write in this run.
- **Seeded scale behavior**: Moving from medium to large increases the seeded dataset from 10,000 files and 100 directories to 50,000 files and 500 directories. `seeded_find` scales close to linearly with file count, increasing by about 4.9x to 5.4x. `seeded_ls_root` increases by about 3.9x to 4.6x, which suggests root listing cost is strongly influenced by total indexed path volume. `seeded_info` remains nearly flat at about 0.9x to 1.2x, indicating direct metadata lookup remains index-friendly as the dataset grows.
- **Backend fit**: SQLite is appropriate for local development, tests, and low-concurrency deployments. For production workloads with concurrent writes, use a server database. PostgreSQL is the most balanced option in these results for concurrent and recursive `find` workloads. Oracle performs well on large seeded root listing and `exists`. MySQL remains viable, but large writes and recursive `find` are comparatively slower in these runs.

## Default CI scenarios

These scenarios run by default for `--scale ci` and keep stable operation names for CI artifacts:

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

`--scale medium` and `--scale large` include these scenarios by default. They can also be selected explicitly with `--scenario` at any scale.

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

Large seeded runs use the manual GitHub Actions workflow **Large Benchmark**. Inputs:

| Input | Values |
|-------|--------|
| `database` | `sqlite`, `mysql`, `postgres`, `oracle` |
| `scale` | `medium`, `large` |
| `seed` | Integer seed, default `1` |
| `scenario` | `all` or any benchmark scenario name |

The manual workflow runs one database at a time and uploads JSON artifacts named with database, scale, and seed.
