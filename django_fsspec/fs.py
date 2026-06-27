from fsspec.spec import AbstractBufferedFile, AbstractFileSystem

from . import operations
from .models import get_block_size


class DjangoFileSystem(AbstractFileSystem):
    """A filesystem backed by Django ORM.

    Parameters
    ----------
    namespace : int
        Tenant namespace ID. Files are isolated by namespace.
    """

    protocol = "django"

    def __init__(self, namespace=0, **kwargs):
        super().__init__(**kwargs)
        self.namespace = namespace

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
        # Directories are implicit, no-op
        pass

    def makedirs(self, path, exist_ok=False):
        # Directories are implicit, no-op
        pass

    def rmdir(self, path):
        # Directories are implicit, no-op
        pass

    def rm(self, path, recursive=False, maxdepth=None):
        path = self._strip_protocol(path)
        operations.delete_file(self.namespace, path, recursive=recursive)

    def cp_file(self, path1, path2, **kwargs):
        path1 = self._strip_protocol(path1)
        path2 = self._strip_protocol(path2)
        operations.copy_file(self.namespace, path1, path2)

    def mv(self, path1, path2, recursive=False, maxdepth=None, **kwargs):
        path1 = self._strip_protocol(path1)
        path2 = self._strip_protocol(path2)
        operations.move_file(self.namespace, path1, path2)

    def created(self, path):
        info = self.info(path)
        return info.get("created")

    def modified(self, path):
        info = self.info(path)
        return info.get("updated")


class DjangoFile(AbstractBufferedFile):
    """A file object backed by Django ORM storage blocks."""

    def __init__(self, fs, path, mode="rb", block_size=None, autocommit=True,
                 cache_options=None, **kwargs):
        if mode not in ("rb", "wb", "ab", "xb"):
            raise ValueError(f"Unsupported mode: {mode!r}. Use 'rb', 'wb', 'ab', or 'xb'.")

        # For read mode, get file size
        size = None
        if "r" in mode:
            try:
                info = operations.get_file_info(fs.namespace, path)
                size = info["size"]
            except FileNotFoundError:
                raise FileNotFoundError(f"File not found: {path}")

        # For exclusive create, check existence early
        if mode == "xb":
            if operations.file_exists(fs.namespace, path):
                raise FileExistsError(f"File already exists: {path}")

        # For append, read existing data
        self._append_data = b""
        if mode == "ab":
            try:
                self._append_data = operations.read_file(fs.namespace, path)
            except FileNotFoundError:
                pass

        if block_size is None:
            block_size = get_block_size()

        super().__init__(
            fs, path, mode=mode, block_size=block_size,
            autocommit=autocommit, cache_options=cache_options,
            size=size, **kwargs
        )

    def _fetch_range(self, start, end):
        """Read bytes [start, end) from the file."""
        return operations.read_file_range(self.fs.namespace, self.path, start, end)

    def _initiate_upload(self):
        """Prepare for upload. Called once before _upload_chunk."""
        self._upload_buffer = self._append_data

    def _upload_chunk(self, final=False):
        """Buffer data; on final=True, write everything to the database."""
        if self.buffer is not None:
            self._upload_buffer += self.buffer.getvalue()
            self.buffer.seek(0)
            self.buffer.truncate()

        if final:
            if self.mode == "xb":
                operations.create_file_exclusive(
                    self.fs.namespace, self.path, self._upload_buffer
                )
            else:
                operations.write_file(
                    self.fs.namespace, self.path, self._upload_buffer
                )
            self._upload_buffer = b""
        return True
