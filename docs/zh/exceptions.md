# 异常体系

## 自定义异常

### DjangoFsspecError

所有 django-fsspec 自定义异常的基类。

```python
from django_fsspec.exceptions import DjangoFsspecError
```

### FileConflictError

乐观锁冲突。当两个进程同时修改同一文件时，后提交的进程会收到此异常。

```python
from django_fsspec.exceptions import FileConflictError

try:
    write_file(0, "/config.json", new_data)
except FileConflictError:
    # 文件被其他进程修改，重新读取后重试
    pass
```

### PathValidationError

路径校验失败。路径包含非法字符、`..` 遍历、连续斜杠等。

```python
from django_fsspec.exceptions import PathValidationError

try:
    write_file(0, "../etc/passwd", b"data")
except PathValidationError:
    # 路径不合法
    pass
```

### FileTooLargeError

文件大小超过 `DJANGO_FSSPEC_MAX_FILE_SIZE` 配置。

## 内置异常

| 异常 | 触发场景 |
|------|---------|
| `FileNotFoundError` | 读取或删除不存在的文件 |
| `FileExistsError` | `xb` 模式写入已存在的文件；`mv` 目标已存在 |
| `ValueError` | 使用不支持的文件模式（如 `r+b`）；`verify_checksum` 检测到数据损坏 |
| `IsADirectoryError` | 对目录执行 `rm` 但未指定 `recursive=True` |
