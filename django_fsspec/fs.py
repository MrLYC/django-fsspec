from collections import deque
import threading

from django.db import transaction as db_transaction
from fsspec.implementations.local import trailing_sep
from fsspec.spec import AbstractFileSystem
from fsspec.transaction import Transaction
from fsspec.utils import other_paths

from ._django import ensure_django_ready


def _operations():
    ensure_django_ready()
    from . import operations

    return operations


def _django_file():
    ensure_django_ready()
    from .buffer import DjangoFile

    return DjangoFile


def _file_node_model():
    ensure_django_ready()
    from .models import FileNode

    return FileNode


def _node_types():
    ensure_django_ready()
    from .models import NODE_TYPE_DIRECTORY, NODE_TYPE_FILE

    return NODE_TYPE_DIRECTORY, NODE_TYPE_FILE


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
        self.files = deque()
        self.fs._intrans = True
        self._atomic = db_transaction.atomic()
        self._atomic.__enter__()

    def complete(self, commit=True):
        try:
            try:
                while self.files:
                    f = self.files.popleft()
                    if commit:
                        f.commit()
                    else:
                        f.discard()
            except Exception as e:
                db_transaction.set_rollback(True)
                self._atomic.__exit__(type(e), e, e.__traceback__)
                raise

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
        ensure_django_ready()
        self._thread_state = threading.local()
        super().__init__(**kwargs)
        self.namespace = namespace_id

    @property
    def _intrans(self):
        return getattr(self._thread_state, "intrans", False)

    @_intrans.setter
    def _intrans(self, value):
        self._thread_state.intrans = value

    @property
    def _transaction(self):
        return getattr(self._thread_state, "transaction", None)

    @_transaction.setter
    def _transaction(self, value):
        self._thread_state.transaction = value

    def ls(self, path, detail=True, **kwargs):
        operations = _operations()
        path = self._strip_protocol(path)
        tolerant = kwargs.get("tolerant", False)
        if not path or path == "/":
            entries = operations.list_directory(self.namespace, "/")
            if detail:
                return operations.list_directory_detail(
                    self.namespace,
                    "/",
                    tolerant=tolerant,
                )
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

        # Try as directory. list_directory validates that the path exists and is a
        # directory; an empty result is a valid empty directory.
        entries = operations.list_directory(self.namespace, path)

        prefix = path.rstrip("/") + "/"
        if detail:
            return operations.list_directory_detail(
                self.namespace,
                path,
                tolerant=tolerant,
            )
        return [prefix + name for name in entries]

    def info(self, path, **kwargs):
        operations = _operations()
        path = self._strip_protocol(path)
        if not path or path == "/":
            return {"name": "/", "size": 0, "type": "directory"}
        return operations.get_file_info(self.namespace, path)

    def exists(self, path, **kwargs):
        operations = _operations()
        path = self._strip_protocol(path)
        if not path or path == "/":
            return True
        return operations.file_exists(self.namespace, path)

    def _open(self, path, mode="rb", block_size=None, autocommit=True,
              cache_options=None, **kwargs):
        DjangoFile = _django_file()
        path = self._strip_protocol(path)
        return DjangoFile(
            self, path, mode=mode, block_size=block_size,
            autocommit=autocommit, cache_options=cache_options, **kwargs
        )

    def mkdir(self, path, create_parents=True, **kwargs):
        operations = _operations()
        path = self._strip_protocol(path)
        if not path or path == "/":
            return
        operations.make_directory(
            self.namespace,
            path,
            create_parents=create_parents,
        )

    def makedirs(self, path, exist_ok=False):
        operations = _operations()
        path = self._strip_protocol(path)
        if not path or path == "/":
            return
        try:
            operations.make_directory(self.namespace, path, create_parents=True)
        except FileExistsError:
            if not exist_ok:
                raise

    def rmdir(self, path):
        operations = _operations()
        path = self._strip_protocol(path)
        operations.remove_directory(self.namespace, path, recursive=False)

    def _rm(self, path):
        operations = _operations()
        path = self._strip_protocol(path)
        operations.delete_file(self.namespace, path, recursive=False)

    def rm(self, path, recursive=False, maxdepth=None):
        operations = _operations()
        path = self._strip_protocol(path)
        operations.delete_file(self.namespace, path, recursive=recursive)

    def rm_file(self, path):
        self._rm(path)

    def cp_file(self, path1, path2, **kwargs):
        operations = _operations()
        path1 = self._strip_protocol(path1)
        path2 = self._strip_protocol(path2)
        operations.copy_file(self.namespace, path1, path2)

    def copy(
        self, path1, path2, recursive=False, maxdepth=None, on_error=None, **kwargs
    ):
        if not recursive:
            return super().copy(
                path1,
                path2,
                recursive=recursive,
                maxdepth=maxdepth,
                on_error=on_error,
                **kwargs,
            )

        source = self._strip_protocol(path1)
        sources = self.find(source, maxdepth=maxdepth, withdirs=True)
        if not sources and self.isfile(source):
            sources = [source]
        if not sources:
            if not self.isdir(source):
                if on_error == "ignore":
                    return
                raise FileNotFoundError(source)

            destination = self._strip_protocol(path2).rstrip("/")
            if trailing_sep(path2) or self.isdir(path2):
                destination = destination + "/" + source.rstrip("/").rsplit("/", 1)[-1]
            self.makedirs(destination, exist_ok=True)
            return

        dest_is_dir = isinstance(path2, str) and (
            trailing_sep(path2) or self.isdir(path2)
        )
        exists = isinstance(path1, str) and dest_is_dir and not trailing_sep(path1)
        destinations = other_paths(sources, path2, exists=exists, flatten=False)

        for src, dst in zip(sources, destinations):
            try:
                if self.isdir(src):
                    self.makedirs(dst, exist_ok=True)
                else:
                    self.cp_file(src, dst, **kwargs)
            except FileNotFoundError:
                if on_error != "ignore":
                    raise

    def mv(self, path1, path2, recursive=False, maxdepth=None, **kwargs):
        operations = _operations()
        path1 = self._strip_protocol(path1)
        path2 = self._strip_protocol(path2)
        if path1 == path2:
            return
        if recursive or self.isdir(path1):
            if trailing_sep(path2) or self.isdir(path2):
                path2 = path2.rstrip("/") + "/" + path1.rstrip("/").rsplit("/", 1)[-1]
            operations.move_directory(
                self.namespace,
                path1,
                path2,
                overwrite=kwargs.get("overwrite", False),
            )
            return
        if trailing_sep(path2) or self.isdir(path2):
            path2 = path2.rstrip("/") + "/" + path1.rsplit("/", 1)[-1]
        operations.move_file(
            self.namespace,
            path1,
            path2,
            overwrite=kwargs.get("overwrite", False),
        )

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
        operations = _operations()
        node_type_directory, node_type_file = _node_types()
        path = self._strip_protocol(path)
        if path and path != "/":
            try:
                info = operations.get_file_info(self.namespace, path)
            except FileNotFoundError:
                pass
            else:
                if info["type"] == node_type_file:
                    if detail:
                        return {path: info}
                    return [path]

        if not path or path == "/":
            prefix = "/"
        else:
            prefix = path.rstrip("/") + "/"

        FileNode = _file_node_model()
        all_nodes = list(
            FileNode.objects.filter(
                namespace=self.namespace,
                path__startswith=prefix,
            )
        )

        def within_maxdepth(node_path):
            if maxdepth is None:
                return True
            return node_path[len(prefix):].count("/") < maxdepth

        results = {}
        for node in all_nodes:
            if not within_maxdepth(node.path):
                continue
            if node.node_type == node_type_directory and not withdirs:
                continue
            entry = {
                "name": node.path,
                "size": node.size if node.node_type != node_type_directory else 0,
                "type": node.node_type,
            }
            results[node.path] = entry

        if withdirs:
            # Collect implicit directories
            dirs = set()
            for node in all_nodes:
                node_path = node.path
                relative = node_path[len(prefix):]
                parts = relative.split("/")
                for i in range(len(parts) - 1):
                    dir_path = prefix + "/".join(parts[:i + 1])
                    if within_maxdepth(dir_path):
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
