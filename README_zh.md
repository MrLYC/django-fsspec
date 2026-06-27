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

## 性能

在 GitHub Actions (ubuntu-latest) 上测试，默认 256KB 块大小。

| 操作 | SQLite | MySQL 8.0 | PostgreSQL 16 | Oracle 23 |
|------|--------|-----------|---------------|-----------|
| **写入**小文件 (100B) | 2.2ms (455/s) | 4.3ms (230/s) | 3.2ms (316/s) | 3.2ms (314/s) |
| **写入**中文件 (10KB) | 2.3ms (438/s) | 4.8ms (207/s) | 3.2ms (312/s) | 3.7ms (270/s) |
| **写入**大文件 (1MB) | 6.2ms (161/s) | 26.9ms (37/s) | 26.2ms (38/s) | 11.0ms (91/s) |
| **读取**小文件 (100B) | 1.2ms (851/s) | 2.4ms (417/s) | 2.3ms (434/s) | 2.5ms (399/s) |
| **读取**大文件 (1MB) | 1.7ms (597/s) | 4.7ms (212/s) | 10.8ms (93/s) | 5.4ms (186/s) |
| **列目录** 1000 文件 | 2.5ms (395/s) | 5.1ms (197/s) | 4.4ms (228/s) | 6.0ms (165/s) |
| **删除** | 2.3ms (432/s) | 5.2ms (192/s) | 4.0ms (252/s) | 4.0ms (253/s) |

完整基准测试结果（含 MySQL 5.7、PG 9.6、并发测试）由 CI 在每次推送时自动收集，可在 [GitHub Actions artifacts](https://github.com/MrLYC/django-fsspec/actions) 查看。

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
