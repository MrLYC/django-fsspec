# django-fsspec

基于 Django ORM 的文件系统，通过 [fsspec](https://filesystem-spec.readthedocs.io/) 提供标准接口。

![django-fsspec 架构图](docs/assets/django-fsspec-architecture.png)

## 特性

- **fsspec 兼容** — 使用标准 `fsspec.filesystem("django")` API
- **多数据库支持** — 通过 Django ORM 适配受支持的关系型数据库
- **可配置块大小** — 按部署需求调整存储粒度
- **乐观锁** — 并发写入冲突检测
- **安全追加 API** — 追加模式使用与公开 API 相同的数据库追加操作
- **命名空间分区** — 整数命名空间提供独立路径空间；授权仍由宿主应用负责
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

在 GitHub Actions (ubuntu-latest) 上测试，默认 256KB 块大小。Push/PR CI 运行有界基准规模（`--scale ci --seed 1`），并上传包含 database、backend、scale、seed 元数据的 JSON artifacts。

| 操作 | SQLite | MySQL 8.0 | PostgreSQL 16 | Oracle 23 |
|------|--------|-----------|---------------|-----------|
| **写入**小文件 (100B) | 2.2ms (450/s) | 4.4ms (226/s) | 3.0ms (333/s) | 3.2ms (313/s) |
| **写入**中文件 (10KB) | 2.3ms (433/s) | 4.9ms (204/s) | 3.1ms (321/s) | 3.6ms (277/s) |
| **写入**大文件 (1MB) | 6.6ms (151/s) | 28.0ms (36/s) | 24.0ms (42/s) | 11.3ms (88/s) |
| **读取**小文件 (100B) | 1.2ms (841/s) | 2.4ms (411/s) | 2.3ms (431/s) | 2.6ms (390/s) |
| **读取**大文件 (1MB) | 1.7ms (598/s) | 4.5ms (223/s) | 7.8ms (129/s) | 5.5ms (183/s) |
| **列目录** 1000 文件 | 2.5ms (394/s) | 5.9ms (168/s) | 3.9ms (254/s) | 6.0ms (167/s) |
| **删除** | 2.4ms (413/s) | 5.3ms (188/s) | 3.5ms (284/s) | 3.7ms (268/s) |

完整基准测试结果（含并发测试）由 CI 在每次推送时自动收集，可在 [GitHub Actions artifacts](https://github.com/MrLYC/django-fsspec/actions) 查看。

更大的铺底数据集可通过手动 GitHub Actions workflow “Large Benchmark” 运行。它支持 `database`（`sqlite`、`mysql`、`postgres`、`oracle`）、`scale`（`medium`、`large`）、`seed` 和可选 `scenario` 输入；运行结果包含铺底数据上的 `ls`、`exists`、`info`、`find` 场景，并上传按数据库、规模、seed 命名的 JSON artifacts。

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
