# 使用指南

## 文件模式

| 模式 | 说明 |
|------|------|
| `rb` | 只读，文件必须存在 |
| `wb` | 写入，创建或覆盖 |
| `ab` | 追加，创建或追加到已有内容 |
| `xb` | 排他创建，文件已存在则抛出 `FileExistsError` |

## 目录操作

目录可以是显式目录，也可以由文件路径隐式推导：

```python
fs.mkdir("/empty")       # 持久化一个空目录
fs.makedirs("/a/b/c")    # 创建父目录
fs.exists("/dir")        # 显式目录存在，或存在 /dir/ 下的文件时为 True
fs.info("/dir")          # {"type": "directory", ...}
fs.rmdir("/empty")       # 删除空的显式目录
```

为了兼容历史数据，目录仍可由文件路径推导：即使没有显式目录节点，`/dir/file.txt` 也会让 `/dir` 可见。

## 列目录

```python
fs.ls("/", detail=False)   # ["/file.txt", "/subdir"]
fs.ls("/", detail=True)    # [{"name": "/file.txt", "size": 100, "type": "file"}, ...]
fs.ls("/", detail=True, tolerant=True)  # 标记损坏子项，而不是中断整个 listing
```

普通详细 listing 对文件项使用严格模式：如果文件的持久化元数据或块图不一致，
会抛出 `DataIntegrityError`。事故排查时可以使用 `tolerant=True`，这样健康
条目仍会返回，损坏条目会以
`{"name": "/bad.txt", "type": "corrupt", "error": "..."}` 形式暴露。

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
fs.mv("/src", "/archive", recursive=True)  # 目录移动（重写元数据）
```

`copy_file()` 默认会在写入新目标前校验源文件完整性，因此不会把损坏源文件复制
成看起来健康的新文件。

递归 `fs.copy()` 是逐文件复制流程，不是时间点快照。如果复制过程中源文件被覆盖，
结果就是每次文件操作当时看到的版本。不要把递归 copy 单独当作备份级快照。

递归目录 `fs.mv()` 在一个数据库事务中执行原子元数据移动。它重写路径，保留块
记录不变，而不是先复制文件再删除源目录。

## WebDAV 管理接口

`django_fsspec.webdav` 提供一个轻量 WebDAV 管理接口，底层使用 `DjangoFileSystem` 和同一套数据库存储层。

在项目 URLConf 中启用：

```python
from django.urls import include, path

urlpatterns = [
    path("webdav/", include("django_fsspec.webdav.urls")),
]
```

内置 WebDAV view 要求认证用户，最小安全配置是 Basic Auth。请在 Django 认证中间件之后加入：

```python
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_fsspec.webdav.auth.BasicAuthMiddleware",
]
```

如果 WebDAV 不是挂载在 `/webdav/`，需要同步配置中间件前缀：

```python
DJANGO_FSSPEC_WEBDAV_PATH_PREFIX = "/files/"
```

该中间件只保护此前缀下的请求。不要只依赖浏览器 session authentication 对外暴露 WebDAV 写接口。

在 Django admin 中创建 `Namespace` 并配置读写用户组。超级用户可访问所有 namespace；拥有 `django_fsspec.read_namespace` 或 `django_fsspec.write_namespace` 的用户可全局访问。

请求示例：

```bash
curl -i -X OPTIONS http://localhost:8000/webdav/1/
curl -i -u user:password -X MKCOL http://localhost:8000/webdav/1/docs
curl -i -u user:password -T README.md http://localhost:8000/webdav/1/docs/readme.txt
curl -i -u user:password -X PROPFIND -H "Depth: 1" http://localhost:8000/webdav/1/docs
curl -i -u user:password http://localhost:8000/webdav/1/docs/readme.txt
```

支持方法：`OPTIONS`、`PROPFIND`、`GET`、`HEAD`、`PUT`、`DELETE`、`MKCOL`、`COPY`、`MOVE`。暂不支持锁（`LOCK`/`UNLOCK`）、属性修改（`PROPPATCH`）和目录 `COPY`/`MOVE`。

## 路径规则

- 必须以 `/` 开头
- 禁止 `\x00` 和控制字符 `\x01-\x1f`
- 禁止 `.` 或 `..` 作为路径分段
- 禁止连续 `/`
- 禁止尾部 `/`（根路径 `/` 除外，仅用于 ls）
- 自动做 Unicode NFC 归一化

## 校验

```python
from django_fsspec.operations import read_file

# 读取时校验 checksum
data = read_file(1, "/test.txt", verify_checksum=True)
```

也可以显式指定完整性策略：

```python
read_file(1, "/test.txt", integrity="off")       # 兼容模式
read_file(1, "/test.txt", integrity="metadata")  # 结构和元数据校验
read_file(1, "/test.txt", integrity="checksum")  # metadata 加 SHA-256 校验
```

完整性问题会抛出 `DataIntegrityError`。它继承自 `ValueError`，以兼容旧的
checksum 错误处理代码。

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

### 事务注意事项

**`fs.transaction` 外的操作不受事务保护。** 每个 `pipe`、`rm`、`mv` 独立提交。第二个操作失败时，第一个已经持久化：

```python
# 不是原子的——b.txt 失败时 a.txt 已写入
fs.pipe("/a.txt", b"aaa")
fs.pipe("/b.txt", b"bbb")

# 原子的——使用 fs.transaction
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
`DjangoFileSystem` 上的 transaction 状态是线程局部的，因此一个线程中的
`fs.transaction` 不会捕获另一个线程用同一个 filesystem 实例发起的普通写入。

打开的读句柄会在 open 时记录文件 id 和 version。如果后续 range read 之前文件被
其他写入者覆盖，该句柄会抛出 `FileConflictError`，不会返回两个版本混合出的字节。

## 数据库路由

确保 django_fsspec 的三张表在同一个数据库上，事务不能跨库。
