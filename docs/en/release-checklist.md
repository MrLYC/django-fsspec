# Release Checklist

Use this checklist before creating a `v*` tag. Publishing is tag-triggered, so
the tag should only be created after `main` is green.

## Required Checks

```bash
uv sync --extra dev --frozen
uv run python -m pytest tests/ -q --cov=django_fsspec --cov-report=term-missing
DJANGO_SETTINGS_MODULE=demo.settings uv run python -m django makemigrations --check --dry-run
uv run python demo/manage.py check
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/e2e_test.py
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario write_small --json /tmp/django-fsspec-benchmark-smoke.json
uv run python -m build --wheel --outdir /tmp/django-fsspec-build-check
```

After building the wheel, verify that it does not include `demo/`, top-level
`tests/`, `django_fsspec/tests/`, pycache files, or local SQLite databases. The
generated `django_fsspec/_version.py` should be present in the wheel, but it
should remain ignored in the repository.

## Release Steps

1. Confirm user-visible changes are documented in the relevant English and
   Chinese docs.
2. Update `CHANGELOG.md` under `Unreleased`.
3. If operational commands changed, update the operations runbooks and the
   runbook-level E2E coverage in `benchmarks/e2e_test.py`.
4. Confirm no Django migration is needed with `makemigrations --check --dry-run`.
5. Confirm `uv.lock` is current with `uv lock --check`.
6. Confirm the CI `package-check` job passes on `main`.
7. Create the `vX.Y.Z` tag only after `main` CI is green.
8. Confirm the publish workflow verifies that the wheel metadata version matches
   the tag version.

## Boundaries

- Do not restore compatibility for removed migration-history experiments.
- Do not rewrite user data from package migrations.
- Use explicit operational commands such as `fsspec_repair` and
  `fsspec_rechunk` for data maintenance.
