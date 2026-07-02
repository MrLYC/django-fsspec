from fsspec.spec import AbstractBufferedFile

from . import operations
from .models import get_block_size


class DjangoFile(AbstractBufferedFile):
    """A file object backed by Django ORM storage blocks.

    Supports modes: rb, wb, ab, xb.
    """

    def __init__(self, fs, path, mode="rb", block_size=None, autocommit=True,
                 cache_options=None, **kwargs):
        if mode not in ("rb", "wb", "ab", "xb"):
            raise ValueError(f"Unsupported mode: {mode!r}. Use 'rb', 'wb', 'ab', or 'xb'.")

        # fsspec cache wrappers may pass a known size into the target _open().
        # DjangoFile still resolves the current FileNode so read handles keep
        # their file/version conflict guard, but must not forward size twice.
        supplied_size = kwargs.pop("size", None)

        # For read mode, get file size
        size = None
        self._file_id = None
        self._file_version = None
        if "r" in mode:
            try:
                info = operations.get_file_info(fs.namespace, path)
                size = info["size"]
                self._file_id = info.get("id")
                self._file_version = info.get("version")
            except FileNotFoundError:
                raise FileNotFoundError(f"File not found: {path}")
        elif supplied_size is not None:
            size = supplied_size

        # For exclusive create, check existence early
        if mode == "xb":
            if operations.file_exists(fs.namespace, path):
                raise FileExistsError(f"File already exists: {path}")

        if block_size is None:
            block_size = get_block_size()

        super().__init__(
            fs, path, mode=mode, block_size=block_size,
            autocommit=autocommit, cache_options=cache_options,
            size=size, **kwargs
        )

    def _fetch_range(self, start, end):
        """Read bytes [start, end) from the file."""
        return operations.read_file_range(
            self.fs.namespace,
            self.path,
            start,
            end,
            file_id=self._file_id,
            version=self._file_version,
        )

    def _initiate_upload(self):
        """Prepare for upload. Called once before _upload_chunk."""
        self._upload_buffer = b""

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
            elif self.mode == "ab":
                operations.append_file(
                    self.fs.namespace, self.path, self._upload_buffer
                )
            else:
                operations.write_file(
                    self.fs.namespace, self.path, self._upload_buffer
                )
            self._upload_buffer = b""
        return True

    def commit(self):
        """Finalize a deferred transaction write before the DB transaction exits."""
        if not self.closed:
            self.close()

    def discard(self):
        """Discard a deferred transaction write without flushing it later."""
        self.closed = True
        self.buffer = None
        self._upload_buffer = b""
