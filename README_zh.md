# django-fsspec

基于 Django ORM 的文件系统，通过 [fsspec](https://filesystem-spec.readthedocs.io/) 提供标准接口。

## 特性

- **fsspec 兼容** — 使用标准 `fsspec.filesystem("django")` API
- **多数据库支持** — MySQL、PostgreSQL、Oracle、SQLite、信创数据库
- **可配置块大小** — 按部署需求调整存储粒度
- **乐观锁** — 安全的并发写入与冲突检测
- **块池复用** — 空闲块回收，高效存储
- **命名空间隔离** — 整数命名空间实现多租户
- **路径校验** — 黑名单规则 + Unicode NFC 归一化
- **隐式目录** — 无目录记录，从文件路径推导
- **管理命令** — `fsspec_gc`、`fsspec_fsck`、`fsspec_stats`

## 快速开始

```bash
pip install django-fsspec
```

添加到 `INSTALLED_APPS`：

```python
INSTALLED_APPS = [
    ...
    "django_fsspec",
]
```

运行迁移：

```bash
python manage.py migrate
```

使用：

```python
import fsspec

fs = fsspec.filesystem("django", namespace=0)

# 写入
with fs.open("/hello.txt", "wb") as f:
    f.write(b"Hello World")

# 读取
data = fs.cat("/hello.txt")  # b"Hello World"

# 列目录
fs.ls("/")  # ["/hello.txt"]

# 删除
fs.rm("/hello.txt")
```

## 配置

在 Django `settings.py` 中添加：

```python
# 块大小（字节），默认 256KB
DJANGO_FSSPEC_BLOCK_SIZE = 64 * 1024

# 文件大小上限（字节），默认 2MB
DJANGO_FSSPEC_MAX_FILE_SIZE = 2 * 1024 * 1024
```

## 支持的文件模式

| 模式 | 说明 |
|------|------|
| `rb` | 只读（文件必须存在） |
| `wb` | 写入（创建或覆盖） |
| `ab` | 追加（创建或追加） |
| `xb` | 排他创建（文件必须不存在） |

## 文档

- [快速入门](docs/zh/getting-started.md)
- [配置说明](docs/zh/configuration.md)
- [使用指南](docs/zh/usage.md)
- [架构设计](docs/zh/architecture.md)
- [管理命令](docs/zh/management-commands.md)
- [块大小迁移](docs/zh/migration-guide.md)
- [异常体系](docs/zh/exceptions.md)

[English](README.md) | [英文文档](README.md)

## 许可证

MIT — 见 [LICENSE](LICENSE)。
