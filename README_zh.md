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

fs = fsspec.filesystem("django", namespace_id=1)

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

在 GitHub Actions (ubuntu-latest) 上测试，默认 256KB 块大小。下表来自 commit `eb31d73` 上的 CI run [28373685170](https://github.com/MrLYC/django-fsspec/actions/runs/28373685170)，参数为 `--scale ci --seed 1`。格式：平均延迟（吞吐量）。

| 操作 | SQLite | MySQL 8.0 / Django 4.2 | MySQL 8.0 / Django 5.2 | PostgreSQL 16 / Django 4.2 | PostgreSQL 16 / Django 5.2 | Oracle 23 |
|------|--------|------------------------|------------------------|----------------------------|----------------------------|-----------|
| **写入**小文件 (100B) | 4.2ms (236/s) | 8.0ms (124/s) | 7.1ms (140/s) | 6.0ms (165/s) | 6.0ms (167/s) | 6.5ms (153/s) |
| **写入**中文件 (10KB) | 4.5ms (223/s) | 8.4ms (119/s) | 7.5ms (133/s) | 6.1ms (164/s) | 6.0ms (168/s) | 6.9ms (145/s) |
| **写入**大文件 (1MB) | 8.2ms (122/s) | 31.3ms (32/s) | 29.3ms (34/s) | 27.1ms (37/s) | 27.1ms (37/s) | 15.9ms (63/s) |
| **读取**小文件 (100B) | 1.4ms (705/s) | 2.6ms (387/s) | 2.4ms (416/s) | 2.5ms (400/s) | 2.5ms (408/s) | 2.7ms (373/s) |
| **读取**大文件 (1MB) | 1.8ms (549/s) | 4.5ms (223/s) | 4.1ms (243/s) | 8.2ms (122/s) | 8.2ms (121/s) | 5.7ms (174/s) |
| **列目录** 1000 文件 | 4.2ms (237/s) | 7.0ms (142/s) | 6.8ms (148/s) | 6.4ms (157/s) | 6.3ms (159/s) | 8.2ms (122/s) |
| **删除** | 2.7ms (375/s) | 5.8ms (173/s) | 5.2ms (193/s) | 3.8ms (263/s) | 3.7ms (273/s) | 4.0ms (251/s) |

完整基准测试结果（含并发测试和手动触发的 medium 铺底数据集）记录在 [基准测试](docs/zh/benchmarks.md)，也可在 [GitHub Actions artifacts](https://github.com/MrLYC/django-fsspec/actions) 查看。

## 文档

- [快速入门](docs/zh/getting-started.md)
- [配置说明](docs/zh/configuration.md)
- [使用指南](docs/zh/usage.md)
- [架构设计](docs/zh/architecture.md)
- [管理命令](docs/zh/management-commands.md)
- [基准测试](docs/zh/benchmarks.md)
- [块大小迁移](docs/zh/migration-guide.md)
- [异常体系](docs/zh/exceptions.md)

[English](README.md) | [英文文档](README.md)

## 许可证

MIT — 见 [LICENSE](LICENSE)。
