# 基准测试

`benchmarks/run.py` 用于衡量 django-fsspec 在不同数据库后端上的行为。基准测试被拆成较小的 CI 规模和需要手动触发的更大规模：普通 pull request 反馈保持快速，同时仍能观测大表铺底数据下的表现。

## 本地运行

```bash
# 使用已配置的 SQLite 后端运行默认 CI 规模 benchmark
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1

# 只运行一个场景
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario write_small

# 使用指定 block size 运行一个场景
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario write_large --block-size 32768

# 保存 JSON 输出
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --json /tmp/bench.json
```

`--db` 是结果展示标签。实际 Django 数据库后端在启动前通过 `DJANGO_FSSPEC_BENCH_DB` 选择。`--block-size` 会在本次 benchmark 进程中覆盖 `DJANGO_FSSPEC_BLOCK_SIZE`，并以 `block_size` 写入每条 JSON 结果。

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
| fsspec 互操作 | `pipe`、`cat`、`ls`、`find`、`mv`、`copy`、`rm`、与底层 operations API 混合使用，以及本地缓存包装器（`filecache`、`simplecache`、`blockcache`、`cached`） |
| 运维 Runbook | `fsspec_migrate` roundtrip、`fsck`/`repair`、`rechunk`/`gc`、JSON 输出和关注退出码 |
| 事务 | 提交、回滚、冲突目录工作流回滚、未关闭写句柄、block 清理 |
| 并发 | 不同文件写入、同文件覆盖、同文件 append、读写交错、删除/list 竞态、block pool 完整性 |

## 完整本地验证

发布前或修改存储语义时建议运行：

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

构建 wheel 后，应确认 wheel 内容中没有 `demo/`、顶层 `tests/` 或 `django_fsspec/tests/`。生成的 `django_fsspec/_version.py` 应存在于 wheel 中，但它由 `hatch-vcs` 生成，仓库中会忽略该文件。

## 规模

| 规模 | 用途 | 铺底文件数 | 铺底目录数 | 铺底操作重复次数 | 铺底 `find` 重复次数 |
|------|------|------------|------------|------------------|----------------------|
| `ci` | 快速 push/PR benchmark 和 smoke test | 100 | 10 | 25 | 1 |
| `small` | 手动中间档，用于衔接 CI 和更大的铺底 run | 1,000 | 50 | 100 | 5 |
| `medium` | 手动中等规模大表 benchmark | 10,000 | 100 | 250 | 5 |
| `large` | 手动大规模大表 benchmark | 50,000 | 500 | 500 | 3 |

所有规模都会保留原有写入、读取、删除、列目录和并发场景的固定操作次数。Push/PR CI 只运行 `--scale ci --seed 1`。手动 `small` 规模用于避免从 CI 的 100 个铺底文件直接跳到 medium 的 10,000 个铺底文件。

缓存场景已加入默认 CI 规模 benchmark 集合：

| 场景 | 衡量内容 | CI 重复次数 | Small | Medium | Large |
|------|----------|-------------|-------|--------|-------|
| `cache_filecache_read_large` | `filecache` 首次复制 1MB 文件后的全文件热读取 | 50 | 100 | 250 | 500 |
| `cache_simplecache_read_large` | `simplecache` 首次复制 1MB 文件后的全文件热读取 | 50 | 100 | 250 | 500 |
| `cache_blockcache_seek_read` | 通过 `blockcache` 对 1MB 文件执行重复 4KB seek 读取，缓存块大小 64KB | 100 | 200 | 500 | 1000 |

`cached` 别名由 E2E 覆盖，它和 `blockcache` 使用同一实现，因此 benchmark 矩阵中不重复展开。

## Block Size 对比

部分数据库会把 Django `BinaryField` 落到 text/CLOB 类存储。对这些实现来说，256KB 单行数据可能比更小的块更慢，因为编码、内存拷贝、redo/undo 日志和 out-of-row LOB 处理都会更明显。`django-fsspec` 默认使用 32KB，作为小文件和广泛数据库兼容性的保守基线。生产中覆盖默认值前，建议在同一个数据库和同一个规模下对比多个 block size：

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

建议至少覆盖 `write_large`、`read_large`、`seek_read`、`overwrite`、`concurrent_write`，以及一个铺底场景，例如 `seeded_find`。手动 GitHub Actions workflow 默认 `block_size_kb=32`，也可以设置为 `all` 一次跑完整 block-size 矩阵。

## 性能预期

下面的耗时范围来自 [GitHub 真实数据](#github-真实数据) 中列出的 GitHub Actions 结果。它们适合用来建立同一套 benchmark 场景下的方向性预期，不应直接当作生产容量上限。GitHub Actions runner 分配、数据库容器预热状态和宿主机负载都可能造成单次运行波动。

### CI 规模操作范围

CI 规模最适合横向比较所有支持的数据库后端，因为同一张表的数据来自同一次成功 CI run。MySQL 和 PostgreSQL 的范围同时包含 Django 4.2 与 Django 5.2 job。

| 操作类型 | SQLite | MySQL 8.0 | PostgreSQL 16 | Oracle 23 | 使用预期 |
|----------|--------|-----------|---------------|-----------|----------|
| 小/中文件写入 | 4.21-4.38ms | 7.23-8.07ms | 6.24-6.59ms | 6.29-6.54ms | 小文件写入通常应保持个位数毫秒；服务端数据库比 SQLite 多出数毫秒。 |
| 大文件写入，1 MB | 8.44ms | 29.95-30.17ms | 29.93-33.39ms | 14.04ms | 大文件写入主要受 block 持久化影响；网络数据库上应预期低几十毫秒。 |
| 读取与 seek | 1.44-1.87ms | 2.44-4.57ms | 2.54-9.48ms | 2.81-5.32ms | 读取通常是个位数毫秒；本次 CI 中 PostgreSQL 的大文件读取是网络数据库里最慢的读取路径。 |
| 覆盖/删除 | 3.40-5.80ms | 6.41-11.56ms | 5.03-9.50ms | 5.11-9.32ms | 修改已有路径通常处在个位数到低两位数毫秒。 |
| 目录列举 | 4.03-4.28ms | 5.87-7.09ms | 6.54-7.17ms | 5.44-7.58ms | 有界的平铺目录和浅层嵌套目录列举，在各服务端数据库上表现接近。 |
| 8 线程并发 | 只读 2.14ms；写密集锁冲突 | 2.84-7.83ms | 2.37-6.74ms | 3.16-7.19ms | 并发写入应使用服务端数据库。SQLite 适合本地或读多写少场景，但写密集并发会遇到 `database is locked`。 |

### 铺底规模预期

下方已发布的手动铺底 run 展示了元数据表已有 10,000 或 50,000 个文件时的行为。铺底数据创建时间不计入测量耗时，因此这些数字衡量的是已存在目录树上的操作成本。新的手动 run 也可以使用 `--scale small`，在 CI 和 medium 之间增加 1,000 文件的中间档。

| 操作 | Medium，10,000 文件 | Large，50,000 文件 | 使用预期 |
|------|---------------------|--------------------|----------|
| 直接 `info` | 0.33-0.72ms | 0.38-0.74ms | 直接元数据查询基本保持稳定，因为可以很好利用索引。 |
| `exists` | 0.85-2.07ms | 1.04-6.54ms | 存在性检查仍然便宜，但存在/缺失混合和不同后端的查询计划在更大数据量下会开始体现差异。 |
| 深层目录 `ls` | 4.01-7.90ms | 4.99-18.61ms | 列一个有内容的深层目录会随规模温和增长。 |
| 根目录 `ls` | 13.14-26.86ms | 51.53-124.37ms | 根目录列举会受根路径下索引路径总量影响，不应作为大目录树的高频热路径。 |
| 递归 `find` | 100.83-144.07ms | 494.83-753.99ms | 全树扫描接近随文件数线性增长；请求路径里应避免频繁完整 `find`。 |

这些结果里的后端适配判断比较明确：SQLite 适合单元测试、本地开发和低并发部署；存在并发写入时应使用 MySQL、PostgreSQL 或 Oracle。PostgreSQL 在本组数据中对并发和递归 `find` 最均衡。Oracle 在大文件写入、large 根目录列举和 large `exists` 上表现较好。MySQL 仍然可用，但本组数据里大文件写入和递归 `find` 相对更慢。

Django 版本会带来可见差异，但不是主导因素。在最新 CI run 中，MySQL 8.0 使用 Django 5.2 时，整张测量表平均延迟比 Django 4.2 低约 6.1%。PostgreSQL 16 使用 Django 5.2 时平均延迟低约 3.8%，但单个场景既有轻微退化，也有更明显改善。

解释 `small`、`medium` 和 `large` 时有一个关键点：普通读写、删除、列目录和并发场景保留与 CI 相同的固定操作次数，并且每个场景开始前都会重置数据库。规模变大影响的是 seeded 数据集大小和 seeded 场景重复次数。因此，手动铺底 artifact 里的普通读写行用于确认同一 workflow 下的正常操作表现；真正对大表规模敏感的是 seeded 行。

## GitHub 真实数据

CI 规模数据来自 2026-06-30 的成功 GitHub Actions CI run [`28412676243`](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243)，commit 为 `2236341`。Medium 和 large 铺底数据来自 2026-06-29 手动触发的 Large Benchmark run。这些铺底 run 是目前最新可用的大表参考 artifact；应按规模和场景比较，不应视为与最新 CI 小规模表来自同一个 commit。

| Artifact | Run | Commit | 范围 |
|----------|-----|--------|------|
| `benchmark-sqlite` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI 规模，SQLite |
| `benchmark-mysql-8.0-django-4.2` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI 规模，MySQL 8.0 + Django 4.2 |
| `benchmark-mysql-8.0-django-5.2` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI 规模，MySQL 8.0 + Django 5.2 |
| `benchmark-postgres-16-django-4.2` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI 规模，PostgreSQL 16 + Django 4.2 |
| `benchmark-postgres-16-django-5.2` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI 规模，PostgreSQL 16 + Django 5.2 |
| `benchmark-oracle` | [28412676243](https://github.com/MrLYC/django-fsspec/actions/runs/28412676243) | `2236341` | CI 规模，Oracle 23 |
| `large-benchmark-sqlite-medium-seed-1` | [28381604379](https://github.com/MrLYC/django-fsspec/actions/runs/28381604379) | `eb31d73` | Medium 规模，SQLite |
| `large-benchmark-mysql-medium-seed-1` | [28381612421](https://github.com/MrLYC/django-fsspec/actions/runs/28381612421) | `eb31d73` | Medium 规模，MySQL 8.0 |
| `large-benchmark-postgres-medium-seed-1` | [28381595934](https://github.com/MrLYC/django-fsspec/actions/runs/28381595934) | `eb31d73` | Medium 规模，PostgreSQL 16 |
| `large-benchmark-oracle-medium-seed-1` | [28381618404](https://github.com/MrLYC/django-fsspec/actions/runs/28381618404) | `eb31d73` | Medium 规模，Oracle 23 |
| `large-benchmark-sqlite-large-seed-1` | [28373589555](https://github.com/MrLYC/django-fsspec/actions/runs/28373589555) | `205aee6` | Large 规模，SQLite |
| `large-benchmark-mysql-large-seed-1` | [28373568411](https://github.com/MrLYC/django-fsspec/actions/runs/28373568411) | `205aee6` | Large 规模，MySQL 8.0 |
| `large-benchmark-postgres-large-seed-1` | [28373362314](https://github.com/MrLYC/django-fsspec/actions/runs/28373362314) | `205aee6` | Large 规模，PostgreSQL 16 |
| `large-benchmark-oracle-large-seed-1` | [28373585625](https://github.com/MrLYC/django-fsspec/actions/runs/28373585625) | `205aee6` | Large 规模，Oracle 23 |

格式：平均延迟 / 吞吐量。成功的并发结果会在 `op` 名称中带上配置的 `8t` 线程数后缀。SQLite 的 `concurrent_write` 和 `concurrent_mixed` 返回 `database is locked`；这是 SQLite 串行化写入模型下的合理结果，因此作为 benchmark 结果如实记录。

### CI 规模结果

| 场景 | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PostgreSQL 16 / Django 4.2 | PostgreSQL 16 / Django 5.2 | Oracle 23 |
|------|--------|------------------------|------------------------|----------------------------|----------------------------|-----------|
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

### Medium 常规操作结果

这些行来自与铺底结果相同的 `--scale medium --seed 1` artifacts。常规场景使用 [默认 CI 场景](#默认-ci-场景) 中的固定操作次数，并且每个场景开始前都会重置数据库，因此它们不是在 10,000 文件的 seeded 树上测量的。

| 场景 | SQLite / Django 5.2.15 | MySQL 8.0 / Django 5.2.15 | PostgreSQL 16 / Django 5.2.15 | Oracle 23 / Django 5.2.15 |
|------|------------------------|----------------------------|--------------------------------|---------------------------|
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

### Medium 铺底结果

这些 run 使用 `--scale medium --seed 1`，即在 100 个目录中铺底 10,000 个文件。铺底数据创建时间不计入测量耗时。

| 场景 | SQLite / Django 5.2.15 | MySQL 8.0 / Django 5.2.15 | PostgreSQL 16 / Django 5.2.15 | Oracle 23 / Django 5.2.15 |
|------|------------------------|----------------------------|--------------------------------|---------------------------|
| `seeded_ls_root` | 13.21ms / 76 ops/s | 21.62ms / 46 ops/s | 26.86ms / 37 ops/s | 13.14ms / 76 ops/s |
| `seeded_ls_deep` | 4.01ms / 250 ops/s | 7.90ms / 127 ops/s | 6.04ms / 165 ops/s | 4.21ms / 237 ops/s |
| `seeded_exists` | 0.99ms / 1014 ops/s | 2.07ms / 483 ops/s | 1.53ms / 652 ops/s | 0.85ms / 1171 ops/s |
| `seeded_info` | 0.33ms / 3076 ops/s | 0.72ms / 1381 ops/s | 0.69ms / 1448 ops/s | 0.64ms / 1568 ops/s |
| `seeded_find` | 122.00ms / 8 ops/s | 144.07ms / 7 ops/s | 100.83ms / 10 ops/s | 140.27ms / 7 ops/s |

### Large 常规操作结果

这些行来自与铺底结果相同的 `--scale large --seed 1` artifacts。常规场景同样使用固定操作次数，并且每个场景开始前都会重置数据库；这里补充这些行，是为了让手动 large benchmark 也能看到普通读写表现。

| 场景 | SQLite | MySQL 8.0 | PostgreSQL 16 | Oracle 23 |
|------|--------|-----------|---------------|-----------|
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

### Large 铺底结果

这些 run 使用 `--scale large --seed 1`，即在 500 个目录中铺底 50,000 个文件。铺底数据创建时间不计入测量耗时。手动 Large Benchmark workflow 不运行 Django 版本矩阵，而是在运行时安装项目正常的 `django>=4.2,<6.0` 依赖集合。

| 场景 | SQLite | MySQL 8.0 | PostgreSQL 16 | Oracle 23 |
|------|--------|-----------|---------------|-----------|
| `seeded_ls_root` | 60.14ms / 17 ops/s | 86.93ms / 12 ops/s | 124.37ms / 8 ops/s | 51.53ms / 19 ops/s |
| `seeded_ls_deep` | 8.97ms / 111 ops/s | 18.61ms / 54 ops/s | 10.81ms / 93 ops/s | 4.99ms / 200 ops/s |
| `seeded_exists` | 3.39ms / 295 ops/s | 6.54ms / 153 ops/s | 3.99ms / 251 ops/s | 1.04ms / 961 ops/s |
| `seeded_info` | 0.38ms / 2626 ops/s | 0.73ms / 1370 ops/s | 0.65ms / 1542 ops/s | 0.74ms / 1356 ops/s |
| `seeded_find` | 644.41ms / 2 ops/s | 736.55ms / 1 ops/s | 494.83ms / 2 ops/s | 753.99ms / 1 ops/s |

## 默认 CI 场景

`--scale ci` 默认运行这些场景。同一规模下结果里的 `op` 名称保持稳定，部分名称会带上配置规模或线程数，例如 `ls_flat_1000` 和 `concurrent_write_8t`：

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

`--scale small`、`--scale medium` 和 `--scale large` 默认包含这些场景。也可以在任意规模下通过 `--scenario` 显式选择它们。

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
- `block_size`：本次 benchmark 实际使用的 `DJANGO_FSSPEC_BLOCK_SIZE`，单位为字节

手动铺底数据通过 GitHub Actions workflow **Large Benchmark** 运行。输入项：

| 输入 | 可选值 |
|------|--------|
| `database` | `sqlite`、`mysql`、`postgres`、`oracle` |
| `scale` | `small`、`medium`、`large` |
| `seed` | 整数 seed，默认 `1` |
| `scenario` | `all` 或任意 benchmark 场景名 |
| `block_size_kb` | `32`（默认）、`64`、`128`、`256` 或 `all` |

手动 workflow 每次只运行一个数据库，并上传按数据库、规模、seed 和 block size 命名的 JSON artifacts。
