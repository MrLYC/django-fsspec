# 架构设计

## 三表结构

| 表 | 作用 |
|----|------|
| `FileNode` | 文件元数据（路径、大小、checksum、版本） |
| `StorageBlock` | 存储块（二进制数据、大小、checksum、是否空闲） |
| `FileBlock` | 文件↔块映射（文件 ID、块 ID、序号） |

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

## 块池复用

删除/覆盖文件时，旧块标记 `is_free=True`。新写入优先复用空闲块（批量 UPDATE），不够时 `bulk_create` 新块。

## 乐观锁

`FileNode.version` 字段实现乐观锁：

```
UPDATE file_node SET ... WHERE pk=X AND version=old_version
```

如果受影响行数为 0，说明被其他进程修改，抛出 `FileConflictError`。

## 写入流程

1. `open(path, "wb")` → 创建 DjangoFile
2. `write(data)` → AbstractBufferedFile 内置缓冲
3. `close()` → 单事务内：切块 → 分配块 → 创建映射 → 更新 FileNode

## 读取流程

1. `open(path, "rb")` → 获取 FileNode.block_size
2. `read()`/`seek()` → `_fetch_range(start, end)`
3. 按 `block_size` 算术定位块序号，只查询需要的块

## 性能基线

在 GitHub Actions (ubuntu-latest) 上测试，默认 256KB 块大小。格式：平均延迟 / 吞吐量。由 CI 自动更新。

### 写入操作

| 操作 | SQLite | MySQL 5.7 | MySQL 8.0 | PG 9.6 | PG 16 | Oracle 23 |
|------|--------|-----------|-----------|--------|-------|-----------|
| 写入小文件 (100B) | 2.22ms / 450 ops/s | 3.53ms / 283 ops/s | 4.43ms / 226 ops/s | 2.89ms / 346 ops/s | 3.00ms / 333 ops/s | 3.20ms / 313 ops/s |
| 写入中文件 (10KB) | 2.31ms / 433 ops/s | 3.77ms / 265 ops/s | 4.90ms / 204 ops/s | 3.00ms / 334 ops/s | 3.11ms / 321 ops/s | 3.61ms / 277 ops/s |
| 写入大文件 (1MB) | 6.63ms / 151 ops/s | 22.64ms / 44 ops/s | 28.03ms / 36 ops/s | 28.26ms / 35 ops/s | 24.00ms / 42 ops/s | 11.30ms / 88 ops/s |
| 覆盖写 | 3.76ms / 266 ops/s | 7.30ms / 137 ops/s | 9.14ms / 109 ops/s | 6.32ms / 158 ops/s | 6.27ms / 159 ops/s | 7.17ms / 139 ops/s |

### 读取操作

| 操作 | SQLite | MySQL 5.7 | MySQL 8.0 | PG 9.6 | PG 16 | Oracle 23 |
|------|--------|-----------|-----------|--------|-------|-----------|
| 读取小文件 (100B) | 1.19ms / 841 ops/s | 2.35ms / 426 ops/s | 2.43ms / 411 ops/s | 2.17ms / 460 ops/s | 2.32ms / 431 ops/s | 2.56ms / 390 ops/s |
| 读取大文件 (1MB) | 1.67ms / 598 ops/s | 4.04ms / 248 ops/s | 4.48ms / 223 ops/s | 10.88ms / 92 ops/s | 7.77ms / 129 ops/s | 5.48ms / 183 ops/s |
| 随机读 (seek+read) | 1.36ms / 738 ops/s | 2.99ms / 334 ops/s | 3.20ms / 312 ops/s | 4.70ms / 213 ops/s | 4.59ms / 218 ops/s | 3.69ms / 271 ops/s |

### 目录和删除操作

| 操作 | SQLite | MySQL 5.7 | MySQL 8.0 | PG 9.6 | PG 16 | Oracle 23 |
|------|--------|-----------|-----------|--------|-------|-----------|
| 列目录 (1000 文件) | 2.54ms / 394 ops/s | 8.04ms / 124 ops/s | 5.94ms / 168 ops/s | 4.06ms / 246 ops/s | 3.94ms / 254 ops/s | 5.97ms / 167 ops/s |
| 列嵌套目录 (100 子目录) | 2.17ms / 460 ops/s | 5.46ms / 183 ops/s | 4.04ms / 247 ops/s | 3.90ms / 256 ops/s | 3.55ms / 282 ops/s | 3.56ms / 281 ops/s |
| 删除 | 2.42ms / 413 ops/s | 4.55ms / 220 ops/s | 5.33ms / 188 ops/s | 3.59ms / 278 ops/s | 3.52ms / 284 ops/s | 3.73ms / 268 ops/s |

### 关键发现

- **SQLite** 全场景最快（无网络开销）
- **MySQL 5.7 vs 8.0**：5.7 在读写上更快；8.0 在目录列表上快约 50%（查询优化器改进）
- **PG 9.6 vs 16**：PG 16 在大文件读取上快约 30%；其他操作接近
- **PostgreSQL** 小文件写入性能优秀，但大文件读取较慢（TOAST 开销）
- **Oracle** 延迟稳定一致
- 所有数据库都能良好支持目标场景（3 万小文件）——即使最慢的写入（MySQL 8.0 大文件 36 ops/s）也能在约 14 分钟内写完 3 万文件

## 开发环境

```bash
git clone https://github.com/MrLYC/django-fsspec.git
cd django-fsspec
pip install -e ".[dev]"
python -m pytest django_fsspec/tests/ -v --cov=django_fsspec
```
