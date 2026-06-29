from django.db import transaction as db_transaction
from fsspec.spec import AbstractFileSystem
from fsspec.transaction import Transaction

from . import operations
from .buffer import DjangoFile


class DjangoTransaction(Transaction):
    """Filesystem transaction backed by Django database transaction.

    Uses Django's ``transaction.atomic()`` which works correctly in all modes:
    - **autocommit mode** (default): opens a real database transaction
    - **inside existing transaction**: creates a savepoint

    On commit, the atomic block exits normally (commit/savepoint release).
    On discard, ``set_rollback(True)`` triggers rollback on exit.

    Nested fsspec transactions are not supported — attempting to start a
    transaction while one is active raises RuntimeError.
    """

    def start(self):
        if self.fs._intrans:
            raise RuntimeError("Nested transactions are not supported")
        self.files = []
        self.fs._intrans = True
        self._atomic = db_transaction.atomic()
        self._atomic.__enter__()

    def complete(self, commit=True):
        try:
            if not commit:
                db_transaction.set_rollback(True)
            self._atomic.__exit__(None, None, None)
        finally:
            self.fs._intrans = False
            self.fs._transaction = None
            self.fs = None


class DjangoFileSystem(AbstractFileSystem):
    """A filesystem backed by Django ORM.

    Supports fsspec transactions via Django database transactions:

        with fs.transaction:
            fs.pipe("/a.txt", b"data a")
            fs.pipe("/b.txt", b"data b")
            # Both committed together, or both rolled back on exception

    Parameters
    ----------
    namespace_id : int
        Tenant namespace ID. Files are isolated by namespace.
    """

    protocol = "django"
    transaction_type = DjangoTransaction

    def __init__(self, namespace_id=1, **kwargs):
        if "namespace" in kwargs:
            raise TypeError("Use namespace_id, not namespace")
        super().__init__(**kwargs)
        self.namespace = namespace_id

    def ls(self, path, detail=True, **kwargs):
        path = self._strip_protocol(path)
        if not path or path == "/":
            entries = operations.list_directory(self.namespace, "/")
            if detail:
                return operations.list_directory_detail(self.namespace, "/")
            return ["/" + name for name in entries]

        # Check if path is a file
        try:
            info = operations.get_file_info(self.namespace, path)
            if info["type"] == "file":
                if detail:
                    return [info]
                return [info["name"]]
        except FileNotFoundError:
            pass

        # Try as directory
        entries = operations.list_directory(self.namespace, path)
        if not entries:
            raise FileNotFoundError(f"Path not found: {path}")

        prefix = path.rstrip("/") + "/"
        if detail:
            return operations.list_directory_detail(self.namespace, path)
        return [prefix + name for name in entries]

    def info(self, path, **kwargs):
        path = self._strip_protocol(path)
        if not path or path == "/":
            return {"name": "/", "size": 0, "type": "directory"}
        return operations.get_file_info(self.namespace, path)

    def exists(self, path, **kwargs):
        path = self._strip_protocol(path)
        if not path or path == "/":
            return True
        return operations.file_exists(self.namespace, path)

    def _open(self, path, mode="rb", block_size=None, autocommit=True,
              cache_options=None, **kwargs):
        path = self._strip_protocol(path)
        return DjangoFile(
            self, path, mode=mode, block_size=block_size,
            autocommit=autocommit, cache_options=cache_options, **kwargs
        )

    def mkdir(self, path, create_parents=True, **kwargs):
        path = self._strip_protocol(path)
        operations.make_directory(
            self.namespace,
            path,
            create_parents=create_parents,
        )

    def makedirs(self, path, exist_ok=False):
        path = self._strip_protocol(path)
        try:
            operations.make_directory(self.namespace, path, create_parents=True)
        except FileExistsError:
            if not exist_ok:
                raise

    def rmdir(self, path):
        path = self._strip_protocol(path)
        operations.remove_directory(self.namespace, path, recursive=False)

    def _rm(self, path):
        path = self._strip_protocol(path)
        operations.delete_file(self.namespace, path, recursive=False)

    def rm(self, path, recursive=False, maxdepth=None):
        path = self._strip_protocol(path)
        operations.delete_file(self.namespace, path, recursive=recursive)

    def rm_file(self, path):
        self._rm(path)

    def cp_file(self, path1, path2, **kwargs):
        path1 = self._strip_protocol(path1)
        path2 = self._strip_protocol(path2)
        operations.copy_file(self.namespace, path1, path2)

    def mv(self, path1, path2, recursive=False, maxdepth=None, **kwargs):
        path1 = self._strip_protocol(path1)
        path2 = self._strip_protocol(path2)
        operations.move_file(self.namespace, path1, path2)

    def touch(self, path, truncate=True, **kwargs):
        path = self._strip_protocol(path)
        if truncate or not self.exists(path):
            with self.open(path, "wb") as f:
                pass
        # truncate=False on existing file: no-op (update timestamp not supported)

    def checksum(self, path):
        """Return the stored SHA-256 checksum of the file."""
        info = self.info(path)
        return info.get("checksum", "")

    def ukey(self, path):
        """Return a unique key for the current version of the file."""
        info = self.info(path)
        return f"{info.get('checksum', '')}:{info.get('version', '')}"

    def sign(self, path, expiration=100, **kwargs):
        raise NotImplementedError(
            "Signing URLs is not supported by DjangoFileSystem"
        )

    def find(self, path, maxdepth=None, withdirs=False, detail=False, **kwargs):
        """List all files under path, using database prefix query."""
        path = self._strip_protocol(path)
        if not path or path == "/":
            prefix = "/"
        else:
            prefix = path.rstrip("/") + "/"

        from .models import NODE_TYPE_DIRECTORY, FileNode

        nodes = list(
            FileNode.objects.filter(
                namespace=self.namespace,
                path__startswith=prefix,
            )
        )
        if maxdepth is not None:
            # Filter by depth: count slashes in relative path
            # maxdepth=1 → only direct children (0 slashes in relative)
            # maxdepth=2 → up to one nested level (0 or 1 slashes)
            nodes = [
                n for n in nodes
                if n.path[len(prefix):].count("/") < maxdepth
            ]

        results = {}
        for node in nodes:
            entry = {
                "name": node.path,
                "size": node.size if node.node_type != NODE_TYPE_DIRECTORY else 0,
                "type": node.node_type,
            }
            results[node.path] = entry

        if withdirs:
            # Collect implicit directories
            dirs = set()
            for node_path in results:
                relative = node_path[len(prefix):]
                parts = relative.split("/")
                for i in range(len(parts) - 1):
                    dir_path = prefix + "/".join(parts[:i + 1])
                    dirs.add(dir_path)
            for d in dirs:
                if d not in results:
                    results[d] = {"name": d, "size": 0, "type": "directory"}

        if detail:
            return results
        return sorted(results.keys())

    def created(self, path):
        info = self.info(path)
        return info.get("created")

    def modified(self, path):
        info = self.info(path)
        return info.get("updated")
