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

## 线程安全

每次 `fs.open()` 返回独立的 `DjangoFile` 实例，可安全在多线程中使用。

## 数据库路由

确保 django_fsspec 的三张表在同一个数据库上，事务不能跨库。
