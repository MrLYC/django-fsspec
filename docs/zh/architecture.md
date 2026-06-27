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

## 开发环境

```bash
git clone https://github.com/MrLYC/django-fsspec.git
cd django-fsspec
pip install -e ".[dev]"
python -m pytest django_fsspec/tests/ -v --cov=django_fsspec
```
