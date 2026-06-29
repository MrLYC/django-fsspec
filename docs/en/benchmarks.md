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

## Scales

| Scale | Purpose | Seeded files | Seeded directories | Seeded operation repeats | Seeded `find` repeats |
|-------|---------|--------------|--------------------|--------------------------|-----------------------|
| `ci` | Fast push/PR benchmark and smoke testing | 100 | 10 | 25 | 1 |
| `medium` | Manual moderate large-table benchmark | 10,000 | 100 | 250 | 5 |
| `large` | Manual large-table benchmark | 50,000 | 500 | 500 | 3 |

All scales keep the original fixed operation counts for write/read/delete/list/concurrent scenarios. Push/PR CI runs `--scale ci --seed 1` only.

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
