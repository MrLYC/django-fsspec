# Usage Guide

## File Modes

| Mode | Description |
|------|-------------|
| `rb` | Read only (file must exist) |
| `wb` | Write (create or overwrite) |
| `ab` | Append (create or append to existing) |
| `xb` | Exclusive create (raises `FileExistsError` if file exists) |

## Local Cache Wrappers

fsspec's local directory cache wrappers work with `DjangoFileSystem`:
`filecache`, `simplecache`, `blockcache`, and the `cached` alias. Use them when
the workload benefits from local hot reads or sparse random reads, and keep the
cache directory outside the database-backed namespace.

See [Local Cache Guide](local-cache.md) for selection rules, examples, and the
mutable-source caveats for `simplecache` and `blockcache`.

## Directory Operations

Directories can be explicit or implicit:

```python
fs.mkdir("/empty")       # persists an empty directory
fs.makedirs("/a/b/c")    # creates parent directories
fs.exists("/dir")        # True for explicit dirs or files under /dir/
fs.info("/dir")          # {"type": "directory", ...}
fs.rmdir("/empty")       # removes an empty explicit directory
```

Directories are also inferred from file paths for backward compatibility, so `/dir/file.txt` still makes `/dir` visible even if no directory node was created.

## Listing

```python
fs.ls("/", detail=False)  # ["/file.txt", "/subdir"]
fs.ls("/", detail=True)   # [{"name": "/file.txt", "size": 100, "type": "file"}, ...]
fs.ls("/", detail=True, tolerant=True)  # validates and marks corrupt children
```

Normal detailed listing is metadata-only and does not validate file contents or
block graphs. Use `tolerant=True` during incident inventory to validate entries,
keep healthy entries visible, and return corrupt entries as metadata such as
`{"name": "/bad.txt", "type": "corrupt", "error": "..."}`.

## Deletion

```python
fs.rm("/file.txt")                   # Delete a single file
fs.rm("/dir", recursive=True)        # Recursively delete directory
fs.rm("/dir")                        # Raises IsADirectoryError
```

## Copy and Move

```python
fs.cp_file("/src.txt", "/dst.txt")   # Copy (no block reuse)
fs.mv("/src.txt", "/dst.txt")        # Move (updates path field)
fs.mv("/src", "/archive", recursive=True)  # Directory move (metadata rewrite)
```

`copy_file()` preserves the normal low-overhead behavior by default. For
backup-style copies, request verification explicitly:

```python
from django_fsspec.operations import copy_file

copy_file(1, "/src.txt", "/dst.txt", integrity="checksum")
```

Recursive `fs.copy()` is a per-file copy workflow, not a point-in-time snapshot.
If source files are overwritten while a recursive copy is running, the result is
a best-effort copy of the versions seen by each file operation. Do not use
recursive copy alone as a backup-quality snapshot.

Recursive directory `fs.mv()` is implemented as an atomic metadata move inside
one database transaction. It rewrites paths and leaves block rows untouched,
rather than copying files and then deleting the source tree.

## WebDAV Management Interface

`django_fsspec.webdav` exposes a small WebDAV management API backed by `DjangoFileSystem` and the same database storage layer.

Enable it in your project URLConf:

```python
from django.urls import include, path

urlpatterns = [
    path("webdav/", include("django_fsspec.webdav.urls")),
]
```

The bundled WebDAV view requires authenticated users and the minimal safe setup is Basic Auth. Add the middleware after Django authentication middleware:

```python
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_fsspec.webdav.auth.BasicAuthMiddleware",
]
```

If you mount WebDAV somewhere other than `/webdav/`, configure the middleware prefix to match:

```python
DJANGO_FSSPEC_WEBDAV_PATH_PREFIX = "/files/"
```

The middleware protects only requests under that prefix. Do not expose WebDAV writes using browser session authentication alone.

Create a `Namespace` in Django admin and assign read/write groups. Superusers can access all namespaces; users with `django_fsspec.read_namespace` or `django_fsspec.write_namespace` can access all namespaces globally.

Example requests:

```bash
curl -i -X OPTIONS http://localhost:8000/webdav/1/
curl -i -u user:password -X MKCOL http://localhost:8000/webdav/1/docs
curl -i -u user:password -T README.md http://localhost:8000/webdav/1/docs/readme.txt
curl -i -u user:password -X PROPFIND -H "Depth: 1" http://localhost:8000/webdav/1/docs
curl -i -u user:password http://localhost:8000/webdav/1/docs/readme.txt
```

Supported methods: `OPTIONS`, `PROPFIND`, `GET`, `HEAD`, `PUT`, `DELETE`, `MKCOL`, `COPY`, and `MOVE`. Locking (`LOCK`/`UNLOCK`), property mutation (`PROPPATCH`), and directory `COPY`/`MOVE` are not supported.

## Path Rules

- Must start with `/`
- No null bytes or control characters (`\x00`-`\x1f`)
- No `.` or `..` path segments
- No consecutive slashes (`//`)
- No trailing slash (except `/` for ls)
- Unicode NFC normalization applied automatically

## Checksum Verification

```python
from django_fsspec.operations import read_file

data = read_file(1, "/test.txt", verify_checksum=True)
# Raises DataIntegrityError on checksum or structural mismatch
```

For explicit policy control:

```python
read_file(1, "/test.txt", integrity="off")       # compatibility mode
read_file(1, "/test.txt", integrity="metadata")  # structure and metadata checks
read_file(1, "/test.txt", integrity="checksum")  # metadata plus SHA-256 checks
```

`DataIntegrityError` is a `ValueError` subclass for backward compatibility with
older checksum-handling code.

## Transactions

Use `fs.transaction` to batch multiple operations atomically:

```python
# All-or-nothing: both files committed together, or both rolled back
with fs.transaction:
    fs.pipe("/config/a.json", b'{"key": "value"}')
    fs.pipe("/config/b.json", b'{"other": "data"}')

# Exception triggers rollback — no partial writes
try:
    with fs.transaction:
        fs.pipe("/tmp/will_rollback.txt", b"data")
        raise ValueError("oops")
except ValueError:
    pass
# /tmp/will_rollback.txt does not exist
```

### Transaction Pitfalls

**Operations outside `fs.transaction` are not grouped.** Each `pipe`, `rm`, `mv` etc. commits independently. If the second operation fails, the first is already persisted:

```python
# NOT atomic — if pipe to b.txt fails, a.txt is already written
fs.pipe("/a.txt", b"aaa")
fs.pipe("/b.txt", b"bbb")

# Atomic — use fs.transaction
with fs.transaction:
    fs.pipe("/a.txt", b"aaa")
    fs.pipe("/b.txt", b"bbb")
```

**`commit()` and `discard()` on `DjangoFile` are no-ops.** Transaction rollback relies on the database (Django's `atomic()`), not on fsspec's file-level commit/discard pattern. This means `fs.transaction` only works with a database that supports transactions — which all supported databases do.

**Transaction isolation depends on your database.** Within `fs.transaction`, reads (`ls`, `cat`, `exists`) may or may not see concurrent writes from other connections, depending on the database's isolation level:

| Database | Default Isolation | Behavior in `fs.transaction` |
|----------|------------------|------------------------------|
| PostgreSQL | READ COMMITTED | Each query sees latest committed data |
| MySQL | REPEATABLE READ | Queries see a snapshot from transaction start |
| SQLite | SERIALIZABLE | Full isolation (single-writer) |

If you need consistent reads within a transaction, be aware that PostgreSQL may show changes committed by other connections between two queries within the same `fs.transaction`.

## Thread Safety

Each `fs.open()` returns an independent `DjangoFile` instance. Transaction state
on `DjangoFileSystem` is thread-local, so one thread's `fs.transaction` does not
capture normal writes from another thread using the same filesystem instance.

Open read handles capture the file id and version at open time. If another
writer overwrites the file before a later range read, the handle raises
`FileConflictError` instead of returning mixed bytes from two versions.

## Database Routing

Ensure all three django_fsspec tables reside on the same database. Transactions cannot span multiple databases.
