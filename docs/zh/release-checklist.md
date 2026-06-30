# 发布检查清单

创建 `v*` tag 前使用这份清单。发布由 tag 触发，因此只有在 `main` 已经通过 CI 后
才应该打 tag。

## 必跑检查

```bash
uv sync --extra dev --frozen
uv run python -m pytest tests/ -q --cov=django_fsspec --cov-report=term-missing
DJANGO_SETTINGS_MODULE=demo.settings uv run python -m django makemigrations --check --dry-run
uv run python demo/manage.py check
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/e2e_test.py
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario write_small --json /tmp/django-fsspec-benchmark-smoke.json
uv run python -m build --wheel --outdir /tmp/django-fsspec-build-check
```

构建 wheel 后，需要确认其中不包含 `demo/`、顶层 `tests/`、
`django_fsspec/tests/`、pycache 文件或本地 SQLite 数据库。生成的
`django_fsspec/_version.py` 应该出现在 wheel 中，但仓库里仍应被忽略。

## 发布步骤

1. 确认用户可见变更已经同步到中英文相关文档。
2. 更新 `CHANGELOG.md` 的 `Unreleased` 段落。
3. 用 `makemigrations --check --dry-run` 确认不需要新的 Django migration。
4. 用 `uv lock --check` 确认 `uv.lock` 已同步。
5. 确认 `main` 上的 CI `package-check` job 通过。
6. 只有在 `main` CI 全绿后，才创建 `vX.Y.Z` tag。
7. 确认发布 workflow 会校验 wheel metadata 版本与 tag 版本一致。

## 边界

- 不恢复已移除的 migration 历史实验兼容。
- 不在包自带 migration 中重写用户数据。
- 数据维护使用显式运维命令，例如 `fsspec_repair` 和 `fsspec_rechunk`。
