# 架构设计

![django-fsspec 架构图](../assets/django-fsspec-architecture.png)

`django-fsspec` 把 fsspec 文件系统接口转换为 Django ORM 查询和事务。对外暴露
兼容 fsspec 的 `django://` 协议；底层使用一组关系型表保存文件元数据、可复用
二进制块，以及文件到块的顺序映射。

## 模块职责

| 模块 | 职责 |
|------|------|
| `fs.py` | `DjangoFileSystem` fsspec 适配器、namespace 分区、目录/文件 API、fsspec 事务集成 |
| `buffer.py` | `DjangoFile` 与 `AbstractBufferedFile` 的桥接、缓冲上传、追加写初始化、范围读取 |
| `operations.py` | 事务化文件原语：校验、切块、分配/释放块、读取、列目录、复制、移动、删除 |
| `models.py` | ORM 表结构和存储配置 helper |
| `validators.py` | 路径校验和 Unicode NFC 规范化 |
| `checks.py` | block size 漂移的 Django system check 和启动检查 |
| `migrations_ops.py` | `RechunkOperation`，用于把已有文件重切到新的 block size |
| `management/commands/` | 运维命令：`fsspec_stats`、`fsspec_fsck`、`fsspec_repair`、`fsspec_gc` |

## 三表模型

| 表 | 作用 |
|----|------|
| `FileNode` | 文件元数据：`namespace`、`path`、`size`、`block_size`、checksum、content type、version、时间戳 |
| `StorageBlock` | 二进制块：data、size、checksum、`is_free` 块池标记 |
| `FileBlock` | 文件到块的有序映射，通过 `sequence` 保持块顺序 |

`FileNode` 对 `(namespace, path)` 做唯一约束，因此不同租户 namespace 可以保存
相同路径。`FileBlock` 对 `(file, sequence)` 做唯一约束，并按 `sequence` 排序，
保证文件重组顺序确定。

## 请求路径

1. fsspec 通过 `django` protocol entry point 实例化
   `DjangoFileSystem(namespace_id=...)`。
2. `DjangoFileSystem` 去掉协议前缀、套用 namespace，并把元数据、列目录、复制、
   移动和删除委托给 `operations.py`。
3. `open()` 返回 `DjangoFile`，由它实现 fsspec 的 buffered-file 合约。
4. `DjangoFile` 读取时调用 `operations.read_file_range()`；最终上传时调用
   `operations.write_file()` 或 `operations.create_file_exclusive()`。
5. `operations.py` 校验路径、计算 checksum、切块，并在 Django 事务中写入 ORM 行。

## 隐式目录

不存储目录记录。目录通过文件路径前缀推导，使用数据库侧下推查询：

```python
FileNode.objects.filter(path__startswith=prefix).annotate(
    relative=Substr("path", prefix_len + 1),
    slash_pos=StrIndex("relative", Value("/")),
    next_part=Case(
        When(slash_pos=0, then="relative"),
        default=Substr("relative", 1, F("slash_pos") - 1),
    ),
).values_list("next_part", flat=True).distinct()
```

数据库只返回去重后的下一级名称，传输量 O(子项数) 而非 O(文件总数)。

`find()` 也使用前缀查询获取路径下的文件。传入 `maxdepth` 时，它会在前缀查询后
用 Python 过滤深度；`ls()` 的下一级目录/文件投影仍下推到数据库。

## 写入流程

1. `open(path, "wb")`、`pipe()` 或 `touch()` 创建 `DjangoFile`。
2. `DjangoFile` 通过 `AbstractBufferedFile` 缓冲字节；追加写模式会先把已有内容
   读入上传缓冲区。
3. 最终上传时，`operations.write_file()` 校验路径、检查
   `DJANGO_FSSPEC_MAX_FILE_SIZE`、计算 SHA-256 文件 checksum，并按当前
   `DJANGO_FSSPEC_BLOCK_SIZE` 切块。
4. 在 `transaction.atomic()` 内，已有文件会把旧块释放到空闲池，并带
   `version` 条件更新 `FileNode`；新文件则创建新的 `FileNode`。
5. `_allocate_blocks()` 优先通过批量 `UPDATE` 占用空闲 `StorageBlock`，重写
   data/checksum；不足时创建新块。
6. `FileBlock.bulk_create()` 写入有序的文件到块映射。

独占创建模式（`"xb"`）走 `create_file_exclusive()`；如果 `(namespace, path)`
已存在则抛出 `FileExistsError`。

## 读取流程

1. `open(path, "rb")` 解析文件元数据和文件大小。
2. `read()` 或 `seek()` 调用 `DjangoFile._fetch_range(start, end)`。
3. `operations.read_file_range()` 使用文件自身保存的 `block_size` 计算
   `start_block` 和 `end_block`，只查询对应的 `FileBlock`，关联
   `StorageBlock` 后拼接并裁剪到请求范围。
4. `operations.read_file(..., verify_checksum=True)` 可以校验块级和文件级
   SHA-256 checksum。

## 空闲块

删除、覆盖、递归删除目录或 rechunk 时，旧块会标记为 `is_free=True`，对应的
`FileBlock` 映射会被删除。新写入始终创建新的 `StorageBlock` 记录；这样避免
依赖不同数据库的行锁语义，优先保证写路径正确性。检查或保留窗口结束后，可用
`fsspec_gc` 永久删除空闲块。

## 乐观锁

`FileNode.version` 字段实现乐观锁：

```
UPDATE file_node SET ... WHERE pk=X AND version=old_version
```

如果受影响行数为 0，说明被其他进程修改，抛出 `FileConflictError`。

## 事务

`DjangoTransaction` 把 fsspec transaction 映射到 `django.db.transaction.atomic()`。
Django autocommit 模式下会开启真实数据库事务；如果已经处在外层事务中，则创建
savepoint。discard 时会标记 atomic block 回滚。嵌套 fsspec transaction 不支持，
会抛出 `RuntimeError`。

## Block Size 变更

每个 `FileNode` 都保存写入时使用的 block size，因此不同 block size 的文件可以
共存，范围读取仍能正确工作。修改 `DJANGO_FSSPEC_BLOCK_SIZE` 只影响后续新写入。
如果要重写已有文件，在 migration 中加入 `RechunkOperation(new_block_size=...)`；
Django system check `django_fsspec.W001` 会在已保存文件和当前配置不一致时警告。

## 运维工具

| 命令 / Hook | 作用 |
|-------------|------|
| `fsspec_stats` | 输出 namespace 数、文件数量/大小、已用/空闲块、映射数量 |
| `fsspec_fsck` | 校验块 checksum、块大小、文件 checksum、文件大小、序号连续性，以及指向空闲块的映射 |
| `fsspec_repair` | 尽力修复派生元数据、活动/空闲块标记、序号缺口、不可能存在的目录映射，以及未引用但仍标记为已用的块 |
| `fsspec_gc` | 删除空闲 `StorageBlock`，可选择保留近期空闲记录用于检查 |
| `check_block_size_consistency` | 当已存文件的 block size 和当前配置不一致时发出 Django warning |

## 性能基线

在 GitHub Actions (ubuntu-latest) 上测试，默认 256KB 块大小。格式：平均延迟 / 吞吐量。来源：commit `eb31d73` 上的 CI run [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170)，参数为 `--scale ci --seed 1`。

### 写入操作

| 操作 | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PG 16 / Django 4.2 | PG 16 / Django 5.2 | Oracle 23 |
|------|--------|------------------------|------------------------|--------------------|--------------------|-----------|
| 写入小文件 (100B) | 4.23ms / 236 ops/s | 8.04ms / 124 ops/s | 7.13ms / 140 ops/s | 6.05ms / 165 ops/s | 5.98ms / 167 ops/s | 6.53ms / 153 ops/s |
| 写入中文件 (10KB) | 4.47ms / 223 ops/s | 8.39ms / 119 ops/s | 7.51ms / 133 ops/s | 6.08ms / 164 ops/s | 5.97ms / 168 ops/s | 6.91ms / 145 ops/s |
| 写入大文件 (1MB) | 8.21ms / 122 ops/s | 31.28ms / 32 ops/s | 29.34ms / 34 ops/s | 27.14ms / 37 ops/s | 27.13ms / 37 ops/s | 15.94ms / 63 ops/s |
| 覆盖写 | 4.86ms / 206 ops/s | 10.18ms / 98 ops/s | 9.15ms / 109 ops/s | 7.47ms / 134 ops/s | 7.50ms / 133 ops/s | 8.06ms / 124 ops/s |

### 读取操作

| 操作 | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PG 16 / Django 4.2 | PG 16 / Django 5.2 | Oracle 23 |
|------|--------|------------------------|------------------------|--------------------|--------------------|-----------|
| 读取小文件 (100B) | 1.42ms / 705 ops/s | 2.58ms / 387 ops/s | 2.40ms / 416 ops/s | 2.50ms / 400 ops/s | 2.45ms / 408 ops/s | 2.68ms / 373 ops/s |
| 读取大文件 (1MB) | 1.82ms / 549 ops/s | 4.48ms / 223 ops/s | 4.12ms / 243 ops/s | 8.18ms / 122 ops/s | 8.23ms / 121 ops/s | 5.73ms / 174 ops/s |
| 随机读 (seek+read) | 1.56ms / 642 ops/s | 3.34ms / 299 ops/s | 3.05ms / 328 ops/s | 4.83ms / 207 ops/s | 4.63ms / 216 ops/s | 3.85ms / 260 ops/s |

### 目录和删除操作

| 操作 | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PG 16 / Django 4.2 | PG 16 / Django 5.2 | Oracle 23 |
|------|--------|------------------------|------------------------|--------------------|--------------------|-----------|
| 列目录 (1000 文件) | 4.21ms / 237 ops/s | 7.02ms / 142 ops/s | 6.78ms / 148 ops/s | 6.35ms / 157 ops/s | 6.30ms / 159 ops/s | 8.21ms / 122 ops/s |
| 列嵌套目录 (100 子目录) | 3.95ms / 253 ops/s | 6.10ms / 164 ops/s | 5.84ms / 171 ops/s | 6.28ms / 159 ops/s | 6.21ms / 161 ops/s | 5.60ms / 179 ops/s |
| 删除 | 2.67ms / 375 ops/s | 5.77ms / 173 ops/s | 5.18ms / 193 ops/s | 3.81ms / 263 ops/s | 3.67ms / 273 ops/s | 3.98ms / 251 ops/s |

### 关键发现

- **SQLite** 在本地读取和随机读取上仍然最快，但并发写和混合读写会体现 SQLite 预期内的 `database is locked` 行为。
- **MySQL 8.0** 在 Django 5.2 下多数 CI 场景优于 Django 4.2，但大文件写入仍是最慢路径。
- **PostgreSQL 16** 在 Django 4.2 和 5.2 下表现接近；在该 CI 环境中，大文件读取慢于 SQLite 和 MySQL。
- **Oracle 23** 在本次 run 中拥有网络数据库里最快的大文件写入结果，读取延迟也较稳定。
- 完整 CI 场景数据和手动触发的 medium/large 铺底结果见 [基准测试](benchmarks.md)。

## 开发环境

```bash
git clone https://github.com/MrLYC/django-fsspec.git
cd django-fsspec
pip install -e ".[dev]"
python -m pytest tests/ -v --cov=django_fsspec
```
