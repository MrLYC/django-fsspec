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

在 GitHub Actions (ubuntu-latest) 上测试，默认 256KB 块大小。数据来自 2026-06-27 CI 运行。

### 写入操作

| 操作 | SQLite | MySQL 8.0 | PG 16 | Oracle 23 |
|------|--------|-----------|-------|-----------|
| 写入小文件 (100B) | 2.61ms / 383 ops/s | 4.47ms / 224 ops/s | 3.08ms / 325 ops/s | 4.33ms / 231 ops/s |
| 写入中文件 (10KB) | 2.88ms / 347 ops/s | 4.86ms / 206 ops/s | 3.17ms / 315 ops/s | 3.73ms / 268 ops/s |
| 写入大文件 (1MB) | 6.81ms / 147 ops/s | 27.29ms / 37 ops/s | 26.07ms / 38 ops/s | 11.29ms / 89 ops/s |
| 覆盖写 | 4.10ms / 244 ops/s | 8.88ms / 113 ops/s | 6.77ms / 148 ops/s | 7.29ms / 137 ops/s |

### 读取操作

| 操作 | SQLite | MySQL 8.0 | PG 16 | Oracle 23 |
|------|--------|-----------|-------|-----------|
| 读取小文件 (100B) | 1.32ms / 755 ops/s | 2.46ms / 406 ops/s | 2.58ms / 387 ops/s | 2.65ms / 378 ops/s |
| 读取大文件 (1MB) | 1.78ms / 561 ops/s | 4.89ms / 204 ops/s | 10.55ms / 95 ops/s | 5.66ms / 177 ops/s |
| 随机读 (seek+read) | 1.48ms / 675 ops/s | 3.19ms / 314 ops/s | 4.93ms / 203 ops/s | 3.88ms / 258 ops/s |

### 目录和删除操作

| 操作 | SQLite | MySQL 8.0 | PG 16 | Oracle 23 |
|------|--------|-----------|-------|-----------|
| 列目录 (1000 文件) | 2.72ms / 367 ops/s | 5.14ms / 195 ops/s | 4.15ms / 241 ops/s | 6.31ms / 158 ops/s |
| 列嵌套目录 (100 子目录) | 2.29ms / 436 ops/s | 4.05ms / 247 ops/s | 3.87ms / 258 ops/s | 3.43ms / 292 ops/s |
| 删除 | 3.10ms / 323 ops/s | 5.54ms / 181 ops/s | 3.86ms / 259 ops/s | 3.90ms / 256 ops/s |

### 关键发现

- **SQLite** 全场景最快（无网络开销）
- **PostgreSQL** 写入性能优秀，但大文件读取较慢（TOAST 开销）
- **Oracle** 延迟稳定一致，偶有 P99 尖刺
- **MySQL** 写入最慢，读取表现稳健
- 所有数据库都能良好支持目标场景（3 万小文件）——即使最慢的写入（MySQL 大文件 37 ops/s）也能在约 13 分钟内写完 3 万文件

## 开发环境

```bash
git clone https://github.com/MrLYC/django-fsspec.git
cd django-fsspec
pip install -e ".[dev]"
python -m pytest django_fsspec/tests/ -v --cov=django_fsspec
```
