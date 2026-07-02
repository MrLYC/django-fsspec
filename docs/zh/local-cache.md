# 本地缓存选择指南

`django-fsspec` 可以配合 fsspec 的本地目录缓存包装器使用：
`filecache`、`simplecache`、`blockcache` 和 `cached`。这些包装器位于
`DjangoFileSystem` 之上，不会改变数据库里的存储格式。

fsspec 官方参考：<https://filesystem-spec.readthedocs.io/en/latest/features.html#caching-files-locally>

## 如何选择

| 需求 | 优先选择 | 原因 | 注意事项 |
|------|----------|------|----------|
| 稳定文件的重复读取 | `filecache` | 首次读取会把完整文件复制到本地，并维护 metadata 以支持过期和检查；热读取不再访问数据库。 | 首次读取仍会下载完整文件。源文件会变化时使用 `check_files=True`，或在写入后清理缓存。 |
| 不可变文件、简单本地副本、多进程/多线程共享 | `simplecache` | 全文件缓存，不维护 metadata。fsspec 文档中它是可安全跨进程/线程共享的缓存包装器，并支持写入。 | 没有过期和远端检查。缓存文件会一直陈旧，直到清理或替换 cache 目录。 |
| 大型稳定文件的随机 `seek()` 读取 | `blockcache` | 按稀疏块写入本地缓存，不需要首次复制完整文件，适合大文件局部读取。 | 同一个缓存文件必须保持相同 `block_size`。本地文件系统需要支持稀疏文件。源文件同路径覆盖后要清理或更换 cache 目录。 |
| 既有代码已经使用 fsspec 通用 cached 协议 | `cached` | 它是与 `blockcache` 相同的块缓存实现别名。 | 按 `blockcache` 处理；不要和全文件缓存共用 cache 目录。 |
| 通过缓存包装器写入 | `simplecache` | 写入先落本地，关闭文件时写回目标文件系统。 | 文件句柄关闭前，不要假设数据库侧文件已经存在。 |

如果源文件频繁变化，建议先不要启用本地缓存。直接走数据库路径更简单，也能避免本地旧数据。

## 示例

以代码方式创建：

```python
import fsspec

fs = fsspec.filesystem(
    "filecache",
    target_protocol="django",
    target_options={"namespace_id": 1},
    cache_storage="/var/cache/myapp/django-fsspec/filecache",
    check_files=True,
)

data = fs.cat("/reports/monthly.csv")
```

URL 链式写法：

```python
import fsspec

with fsspec.open(
    "filecache::django:///reports/monthly.csv",
    mode="rb",
    filecache={
        "cache_storage": "/var/cache/myapp/django-fsspec/filecache",
        "check_files": True,
    },
    django={"namespace_id": 1},
) as f:
    data = f.read()
```

`simplecache` 写回：

```python
import fsspec

fs = fsspec.filesystem(
    "simplecache",
    target_protocol="django",
    target_options={"namespace_id": 1},
    cache_storage="/var/cache/myapp/django-fsspec/simplecache",
)

with fs.open("/exports/result.bin", "wb") as f:
    f.write(b"payload")
```

`blockcache` 用于重复随机读取：

```python
import fsspec

fs = fsspec.filesystem(
    "blockcache",
    target_protocol="django",
    target_options={"namespace_id": 1},
    cache_storage="/var/cache/myapp/django-fsspec/blockcache",
)

with fs.open("/datasets/large.bin", "rb", block_size=64 * 1024) as f:
    f.seek(10 * 1024 * 1024)
    chunk = f.read(4096)
```

## 运维规则

每种协议和每类 workload 使用独立 cache 目录。不要把 `filecache` 或
`simplecache` 与 `blockcache`/`cached` 放在同一个目录。

对于可能变化的源路径：

- `filecache` 可以使用 `check_files=True`，因为 `DjangoFileSystem.ukey()`
  包含文件 checksum 和 version。
- `simplecache` 不检查远端变化。源文件写入后，需要清理或替换 cache 目录。
- `blockcache` 和 `cached` 应按稳定文件缓存处理。同路径覆盖后，再次读取该路径前
  应调用 `clear_cache()` 或使用新的 cache 目录；否则本地稀疏缓存文件里已经落盘的
  旧块可能继续存在。

cache 目录不是授权边界。缓存文件包含与数据库文件相同的字节，应按下载后的应用数据
来保护目录权限。

需要监控磁盘占用并设置清理策略。全文件缓存最终可能增长到所有已读取文件的总大小。
块缓存只会随实际触达的块增长，但大量随机读取也可能逐步物化大文件的大部分内容。

## 测试和 benchmark 覆盖

`tests/test_fsspec_local_cache.py` 覆盖：

- `filecache` 的陈旧读取、`check_files=True` 和 `clear_cache()`
- `simplecache` 的陈旧读取和写回行为
- `blockcache` 与 `cached` 的 seek 读取、block size 不匹配处理，以及源文件更新后的显式清理

`benchmarks/e2e_test.py` 会在选中的真实数据库后端上运行同一组缓存包装器。

`benchmarks/run.py` 包含 CI 规模的缓存 benchmark 场景：

```bash
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario cache_filecache_read_large
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario cache_simplecache_read_large
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario cache_blockcache_seek_read
```

