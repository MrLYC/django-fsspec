# Local Cache Guide

`django-fsspec` works with fsspec's local directory cache wrappers:
`filecache`, `simplecache`, `blockcache`, and `cached`. These wrappers live
above `DjangoFileSystem`; they do not change how data is stored in the database.

Official fsspec reference: <https://filesystem-spec.readthedocs.io/en/latest/features.html#caching-files-locally>

## Choosing a Cache

| Need | Prefer | Why | Watch out |
|------|--------|-----|-----------|
| Repeated reads of stable files | `filecache` | Copies the whole file locally and keeps metadata for expiry/checking. Hot reads avoid database reads. | First read still downloads the full file. Use `check_files=True` or clear the cache when source files are mutable. |
| Immutable files, simple local copies, process/thread sharing | `simplecache` | Whole-file cache with no metadata database. fsspec documents it as the cache wrapper that is safe to share between processes/threads, and it supports writes. | No expiry or remote-file checking. A cached file remains stale until the cache directory is cleared or replaced. |
| Large stable files with random `seek()` reads | `blockcache` | Stores sparse local blocks instead of copying the full file up front. This fits partial reads on large files. | Keep the same `block_size` for the same cached file. Use a filesystem that supports sparse files. Clear or replace the cache after overwriting the source file. |
| Existing code already using fsspec's generic cached protocol | `cached` | Alias for the same block-caching implementation as `blockcache`. | Treat it like `blockcache`; do not share its cache directory with whole-file caches. |
| Writes through a cached wrapper | `simplecache` | Writes are staged locally and written back to the target on close. | Close the file handle before expecting the database-backed file to exist. |

If the source file is expected to change often, start without a local cache.
The database path is simpler and avoids stale local data.

## Examples

Programmatic construction:

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

URL chaining:

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

`simplecache` write-through:

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

`blockcache` for repeated random reads:

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

## Operational Rules

Use a dedicated cache directory per protocol and workload. Do not mix
`filecache` or `simplecache` with `blockcache`/`cached` in the same directory.

For mutable source paths:

- `filecache` can use `check_files=True` because `DjangoFileSystem.ukey()`
  includes the stored checksum and version.
- `simplecache` does not check remote changes. Clear or replace its cache
  directory after source writes.
- `blockcache` and `cached` should be treated as caches for stable files. After
  a same-path overwrite, call `clear_cache()` or use a new cache directory before
  reading that path again. Previously materialized sparse blocks may otherwise
  survive in the local cache file.

Do not use the cache directory as an authorization boundary. Cache files contain
the same bytes as the database-backed files, so protect the directory with the
same care as downloaded application data.

Monitor disk usage and apply normal cleanup policy. Whole-file caches can grow
to the total size of files read through them. Block caches grow with the blocks
that are actually touched, but repeated random reads can eventually materialize
most of a large file.

## Test and Benchmark Coverage

`tests/test_fsspec_local_cache.py` covers:

- `filecache` stale reads, `check_files=True`, and `clear_cache()`
- `simplecache` stale reads and write-through behavior
- `blockcache` and `cached` seek reads, block-size mismatch handling, and
  explicit cache clearing after source updates

`benchmarks/e2e_test.py` runs the same cache wrappers against the selected real
database backend.

`benchmarks/run.py` includes CI-scale cache scenarios:

```bash
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario cache_filecache_read_large
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario cache_simplecache_read_large
DJANGO_FSSPEC_BENCH_DB=sqlite uv run python benchmarks/run.py --db sqlite --scale ci --seed 1 --scenario cache_blockcache_seek_read
```

