from fsspec.spec import AbstractBufferedFile

from ._django import ensure_django_ready


def _operations():
    ensure_django_ready()
    from . import operations

    return operations


def _get_block_size():
    ensure_django_ready()
    from .models import get_block_size

    return get_block_size()


class DjangoFile(AbstractBufferedFile):
    """A file object backed by Django ORM storage blocks.

    Supports modes: rb, wb, ab, xb.
    """

    def __init__(self, fs, path, mode="rb", block_size=None, autocommit=True,
                 cache_options=None, **kwargs):
        operations = _operations()
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
            block_size = _get_block_size()

        self._writer = None

        super().__init__(
            fs, path, mode=mode, block_size=block_size,
            autocommit=autocommit, cache_options=cache_options,
            size=size, **kwargs
        )

    def _fetch_range(self, start, end):
        """Read bytes [start, end) from the file."""
        operations = _operations()
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
        operations = _operations()
        content_type = self.kwargs.pop("content_type", "")
        self._writer = operations.StreamingFileWriter(
            self.fs.namespace,
            self.path,
            self.mode,
            content_type=content_type,
            block_size=self.blocksize,
        )
        self._writer.start()

    def _upload_chunk(self, final=False):
        """Persist the current buffer chunk to the database."""
        if self.buffer is not None:
            chunk = self.buffer.getvalue()
            self.buffer.seek(0)
            self.buffer.truncate()
        else:
            chunk = b""
        self._writer.write_chunk(chunk, final=final)
        if final and self.autocommit:
            self._writer.commit()
            self._writer = None
        return True

    def commit(self):
        """Finalize a deferred transaction write before the DB transaction exits."""
        if not self.closed:
            self.close()
        if self._writer is not None:
            try:
                self._writer.commit()
            finally:
                self._writer = None

    def discard(self):
        """Discard a deferred transaction write without flushing it later."""
        self.closed = True
        self.buffer = None
        if self._writer is not None:
            try:
                self._writer.discard()
            finally:
                self._writer = None
