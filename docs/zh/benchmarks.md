# 基准测试

`benchmarks/run.py` 用于衡量 django-fsspec 在不同数据库后端上的行为。基准测试被拆成较小的 CI 规模和需要手动触发的更大规模：普通 pull request 反馈保持快速，同时仍能观测大表铺底数据下的表现。

## 本地运行

```bash
# 使用已配置的 SQLite 后端运行默认 CI 规模 benchmark
DJANGO_FSSPEC_BENCH_DB=sqlite python benchmarks/run.py --db sqlite --scale ci --seed 1

# 只运行一个场景
DJANGO_FSSPEC_BENCH_DB=sqlite python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario write_small

# 保存 JSON 输出
DJANGO_FSSPEC_BENCH_DB=sqlite python benchmarks/run.py --db sqlite --scale ci --seed 1 --json /tmp/bench.json
```

`--db` 是结果展示标签。实际 Django 数据库后端在启动前通过 `DJANGO_FSSPEC_BENCH_DB` 选择。

## 测试与 E2E 配置

单元测试、E2E、benchmark 和手动管理命令都使用共享的示例 Django 项目 `demo.settings`。`demo/` 保持在可安装的 `django_fsspec` 包之外，测试文件放在顶层 `tests/` 目录，因此 wheel 中不会带入 `demo/` 或测试代码。

settings 会根据 `DJANGO_FSSPEC_BENCH_DB` 为 E2E 和 benchmark 选择数据库后端：

| 值 | 后端 |
|----|------|
| 未设置 | 单元测试使用内存 SQLite |
| `sqlite` | 文件型 SQLite benchmark 数据库 |
| `mysql` | 使用 `MYSQL_*` 环境变量的 MySQL |
| `postgres` | 使用 `POSTGRES_*` 环境变量的 PostgreSQL |
| `oracle` | 使用 `ORACLE_*` 环境变量的 Oracle |

`benchmarks/e2e_test.py` 会针对选中的真实数据库后端验证行为。SQLite 会有意跳过并发写场景，因为 SQLite 写入是串行化的；MySQL、PostgreSQL 和 Oracle 会在 CI 中运行完整并发场景。

E2E 覆盖这些面向用户的工作流：

| 领域 | 覆盖内容 |
|------|----------|
| 核心文件 API | 写入、读取、覆盖、空文件、多块文件、范围读取、校验和验证 |
| 目录语义 | 列目录、隐式目录、持久空目录、递归删除、递归复制/移动、`find`/`tree` 视图 |
| 冲突处理 | 文件/目录路径冲突、隐式目录目标、已存在移动目标、根目录删除保护 |
| namespace 行为 | 不同 namespace 下相同路径隔离，以及文件/目录树混合冲突隔离 |
| fsspec 互操作 | `pipe`、`cat`、`ls`、`find`、`mv`、`copy`、`rm` 与底层 operations API 混合使用 |
| 事务 | 提交、回滚、冲突目录工作流回滚、未关闭写句柄、block 清理 |
| 并发 | 不同文件写入、同文件覆盖、同文件 append、读写交错、删除/list 竞态、block pool 完整性 |

## 完整本地验证

发布前或修改存储语义时建议运行：

```bash
python -m pytest tests/ -q --cov=django_fsspec --cov-report=term-missing
DJANGO_SETTINGS_MODULE=demo.settings python -m django makemigrations --check --dry-run
python demo/manage.py check
python benchmarks/e2e_test.py
python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario write_small --json /tmp/django-fsspec-benchmark-smoke.json
python -m build --wheel --outdir /tmp/django-fsspec-build-check
```

构建 wheel 后，应确认 wheel 内容中没有 `demo/`、顶层 `tests/` 或 `django_fsspec/tests/`。生成的 `django_fsspec/_version.py` 应存在于 wheel 中，但它由 `hatch-vcs` 生成，仓库中会忽略该文件。

## 规模

| 规模 | 用途 | 铺底文件数 | 铺底目录数 | 铺底操作重复次数 | 铺底 `find` 重复次数 |
|------|------|------------|------------|------------------|----------------------|
| `ci` | 快速 push/PR benchmark 和 smoke test | 100 | 10 | 25 | 1 |
| `medium` | 手动中等规模大表 benchmark | 10,000 | 100 | 250 | 5 |
| `large` | 手动大规模大表 benchmark | 50,000 | 500 | 500 | 3 |

所有规模都会保留原有写入、读取、删除、列目录和并发场景的固定操作次数。Push/PR CI 只运行 `--scale ci --seed 1`。

## 最新 GitHub 结果

以下数据均来自 commit `eb31d73`，运行时间为 2026-06-29。CI 小规模结果来自 run [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170)。手动 medium 铺底结果按数据库分别触发：

| Artifact | Run | 范围 |
|----------|-----|------|
| `benchmark-sqlite` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | CI 规模，SQLite |
| `benchmark-mysql-8.0-django-4.2` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | CI 规模，MySQL 8.0 + Django 4.2 |
| `benchmark-mysql-8.0-django-5.2` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | CI 规模，MySQL 8.0 + Django 5.2 |
| `benchmark-postgres-16-django-4.2` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | CI 规模，PostgreSQL 16 + Django 4.2 |
| `benchmark-postgres-16-django-5.2` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | CI 规模，PostgreSQL 16 + Django 5.2 |
| `benchmark-oracle` | [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170) | CI 规模，Oracle 23 |
| `large-benchmark-sqlite-medium-seed-1` | [28381604379](https://github.com/MrLYC/django-fsspec/actions/runs/28381604379) | Medium 规模，SQLite，Django 5.2.15 |
| `large-benchmark-mysql-medium-seed-1` | [28381612421](https://github.com/MrLYC/django-fsspec/actions/runs/28381612421) | Medium 规模，MySQL 8.0，Django 5.2.15 |
| `large-benchmark-postgres-medium-seed-1` | [28381595934](https://github.com/MrLYC/django-fsspec/actions/runs/28381595934) | Medium 规模，PostgreSQL 16，Django 5.2.15 |
| `large-benchmark-oracle-medium-seed-1` | [28381618404](https://github.com/MrLYC/django-fsspec/actions/runs/28381618404) | Medium 规模，Oracle 23，Django 5.2.15 |

格式：平均延迟 / 吞吐量。SQLite 的 `concurrent_write` 和 `concurrent_mixed` 返回 `database is locked`；这是 SQLite 串行化写入模型下的合理结果，因此作为 benchmark 结果如实记录。

### CI 规模结果

| 场景 | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PostgreSQL 16 / Django 4.2 | PostgreSQL 16 / Django 5.2 | Oracle 23 |
|------|--------|------------------------|------------------------|----------------------------|----------------------------|-----------|
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

### Medium 铺底结果

这些 run 使用 `--scale medium --seed 1`，即在 100 个目录中铺底 10,000 个文件。铺底数据创建时间不计入测量耗时。

| 场景 | SQLite / Django 5.2.15 | MySQL 8.0 / Django 5.2.15 | PostgreSQL 16 / Django 5.2.15 | Oracle 23 / Django 5.2.15 |
|------|------------------------|----------------------------|--------------------------------|---------------------------|
| `seeded_ls_root` | 13.21ms / 76 ops/s | 21.62ms / 46 ops/s | 26.86ms / 37 ops/s | 13.14ms / 76 ops/s |
| `seeded_ls_deep` | 4.01ms / 250 ops/s | 7.90ms / 127 ops/s | 6.04ms / 165 ops/s | 4.21ms / 237 ops/s |
| `seeded_exists` | 0.99ms / 1014 ops/s | 2.07ms / 483 ops/s | 1.53ms / 652 ops/s | 0.85ms / 1171 ops/s |
| `seeded_info` | 0.33ms / 3076 ops/s | 0.72ms / 1381 ops/s | 0.69ms / 1448 ops/s | 0.64ms / 1568 ops/s |
| `seeded_find` | 122.00ms / 8 ops/s | 144.07ms / 7 ops/s | 100.83ms / 10 ops/s | 140.27ms / 7 ops/s |

## 默认 CI 场景

`--scale ci` 默认运行这些场景，并保持稳定的操作名，便于 CI artifacts 对比：

| 场景 | 设计 |
|------|------|
| `write_small` | 写入 1,000 个 100 B 文件。 |
| `write_medium` | 写入 200 个 10 KB 文件。 |
| `write_large` | 写入 50 个 1 MB 文件。 |
| `read_small` | 预先创建 1,000 个 100 B 文件，然后计时读取。 |
| `read_large` | 预先创建 50 个 1 MB 文件，然后计时读取。 |
| `overwrite` | 对同一个文件覆盖写入 500 次。 |
| `ls_flat` | 在同一个目录创建 1,000 个文件，然后列目录 100 次。 |
| `ls_nested` | 创建 100 个目录，每个目录 10 个文件，然后列父目录 100 次。 |
| `delete` | 预先创建 500 个文件，然后计时删除。 |
| `seek_read` | 创建 1 MB 文件，然后执行 100 次确定性的随机 seek/read。 |
| `concurrent_write` | 8 个线程写入 100 个文件，并保证批次拆分不丢操作。 |
| `concurrent_read` | 8 个线程读取 100 个预创建文件。 |
| `concurrent_mixed` | 8 个线程执行 200 次混合读写操作。 |

## 铺底大表场景

铺底场景会先在 `/bench/seeded` 下构造确定性数据集，再开始计时。数据集创建时间不计入操作耗时。`--seed` 控制路径分布，因此相同 seed 的运行可对比，不同 seed 可改变目录分布。

`--scale medium` 和 `--scale large` 默认包含这些场景。也可以在任意规模下通过 `--scenario` 显式选择它们。

| 场景 | 设计 |
|------|------|
| `seeded_ls_root` | 铺底后重复列 `/bench/seeded`。 |
| `seeded_ls_deep` | 重复列一个确定性的、包含文件的深层目录。 |
| `seeded_exists` | 重复检查 50/50 混合的存在路径和缺失路径。 |
| `seeded_info` | 重复获取确定性存在路径的元数据。 |
| `seeded_find` | 重复对 `/bench/seeded` 执行递归 `find`；由于会扫描整棵铺底树，重复次数刻意较低。 |

## GitHub Actions

普通 CI 在每次 push 和 pull request 中运行有界 benchmark，并为每条 JSON 结果附加这些元数据：

- `db`：通过 `--db` 传入的展示标签
- `backend`：`DJANGO_FSSPEC_BENCH_DB`
- `scale`
- `seed`

更大的铺底数据通过手动 GitHub Actions workflow **Large Benchmark** 运行。输入项：

| 输入 | 可选值 |
|------|--------|
| `database` | `sqlite`、`mysql`、`postgres`、`oracle` |
| `scale` | `medium`、`large` |
| `seed` | 整数 seed，默认 `1` |
| `scenario` | `all` 或任意 benchmark 场景名 |

手动 workflow 每次只运行一个数据库，并上传按数据库、规模、seed 命名的 JSON artifacts。
