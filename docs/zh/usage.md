# 使用指南

## 文件模式

| 模式 | 说明 |
|------|------|
| `rb` | 只读，文件必须存在 |
| `wb` | 写入，创建或覆盖 |
| `ab` | 追加，创建或追加到已有内容 |
| `xb` | 排他创建，文件已存在则抛出 `FileExistsError` |

## 目录操作

目录是隐式的，不存储目录记录：

```python
fs.mkdir("/any/path")   # no-op，不报错
fs.makedirs("/a/b/c")   # no-op
fs.exists("/dir")        # 检查是否有 /dir/ 开头的文件
fs.info("/dir")          # {"type": "directory", ...}
```

## 列目录

```python
fs.ls("/", detail=False)   # ["/file.txt", "/subdir"]
fs.ls("/", detail=True)    # [{"name": "/file.txt", "size": 100, "type": "file"}, ...]
```

## 删除

```python
fs.rm("/file.txt")                    # 删除单个文件
fs.rm("/dir", recursive=True)         # 递归删除目录
fs.rm("/dir")                         # IsADirectoryError
```

## 复制和移动

```python
fs.cp_file("/src.txt", "/dst.txt")    # 复制（不做块复用）
fs.mv("/src.txt", "/dst.txt")         # 移动（更新路径）
```

## 路径规则

- 必须以 `/` 开头
- 禁止 `\x00` 和控制字符 `\x01-\x1f`
- 禁止 `..` 作为路径分段
- 禁止连续 `/`
- 禁止尾部 `/`（根路径 `/` 除外，仅用于 ls）
- 自动做 Unicode NFC 归一化

## 校验

```python
from django_fsspec.operations import read_file

# 读取时校验 checksum
data = read_file(namespace=0, path="/test.txt", verify_checksum=True)
```

## 事务

使用 `fs.transaction` 将多个操作批量原子化：

```python
# 两个文件一起提交，或一起回滚
with fs.transaction:
    fs.pipe("/config/a.json", b'{"key": "value"}')
    fs.pipe("/config/b.json", b'{"other": "data"}')

# 异常触发回滚——不会有部分写入
try:
    with fs.transaction:
        fs.pipe("/tmp/will_rollback.txt", b"data")
        raise ValueError("oops")
except ValueError:
    pass
# /tmp/will_rollback.txt 不存在
```

也可以和 Django 的 `transaction.atomic()` 配合：

```python
from django.db import transaction

with transaction.atomic():
    MyModel.objects.create(name="test")
    fs.pipe("/related.txt", b"data")
    # Model 和文件一起提交或回滚
```

### 事务注意事项

**`fs.transaction` 外的操作不受事务保护。** 每个 `pipe`、`rm`、`mv` 独立提交。第二个操作失败时，第一个已经持久化：

```python
# 不是原子的——b.txt 失败时 a.txt 已写入
fs.pipe("/a.txt", b"aaa")
fs.pipe("/b.txt", b"bbb")

# 原子的——使用 fs.transaction 或 Django transaction.atomic()
with fs.transaction:
    fs.pipe("/a.txt", b"aaa")
    fs.pipe("/b.txt", b"bbb")
```

**`DjangoFile` 的 `commit()` 和 `discard()` 是空操作。** 事务回滚依赖数据库（Django 的 `atomic()`），不依赖 fsspec 的文件级 commit/discard 模式。

**事务隔离级别取决于数据库。** 在 `fs.transaction` 内，读操作（`ls`、`cat`、`exists`）是否能看到其他连接的并发写入，取决于数据库的隔离级别：

| 数据库 | 默认隔离级别 | `fs.transaction` 内的行为 |
|--------|------------|-------------------------|
| PostgreSQL | READ COMMITTED | 每条查询看到最新已提交的数据 |
| MySQL | REPEATABLE READ | 查询看到事务开始时的快照 |
| SQLite | SERIALIZABLE | 完全隔离（单写者） |

## 线程安全

每次 `fs.open()` 返回独立的 `DjangoFile` 实例，可安全在多线程中使用。

## 数据库路由

确保 django_fsspec 的三张表在同一个数据库上，事务不能跨库。
