import hashlib

import pytest
from django.test import TestCase

from django_fsspec.exceptions import DataIntegrityError, FileConflictError, FileTooLargeError
from django_fsspec.fs import DjangoFileSystem
from django_fsspec.models import FileBlock, FileNode, StorageBlock
from django_fsspec.operations import (
    StreamingFileWriter,
    append_file,
    copy_file,
    read_file,
    read_file_range,
    read_file_streaming_range,
    write_file,
)


class TestStreamingFileWriter(TestCase):
    def test_empty_file_write(self):
        node = write_file(1, "/empty.txt", b"")
        assert node.size == 0
        assert node.checksum == hashlib.sha256(b"").hexdigest()
        assert FileBlock.objects.filter(file=node).count() == 0

    def test_single_chunk_write(self):
        node = write_file(1, "/single.txt", b"hello")
        assert node.size == 5
        assert read_file(1, "/single.txt") == b"hello"

    def test_multi_chunk_write_via_fs(self):
        """Writing a file in multiple flushes should persist incrementally."""
        fs = DjangoFileSystem(namespace_id=1)
        with fs.open("/multi.txt", "wb", block_size=10) as f:
            for _ in range(4):
                f.write(b"a" * 10)
        node = FileNode.objects.get(path="/multi.txt")
        assert node.size == 40
        blocks = FileBlock.objects.filter(file=node).order_by("sequence")
        assert blocks.count() == 4
        assert read_file(1, "/multi.txt") == b"a" * 40

    def test_append_does_not_load_whole_file(self):
        fs = DjangoFileSystem(namespace_id=1)
        with fs.open("/append.txt", "wb") as f:
            f.write(b"first")
        with fs.open("/append.txt", "ab") as f:
            f.write(b"-second")
        assert read_file(1, "/append.txt") == b"first-second"
        node = FileNode.objects.get(path="/append.txt")
        assert FileBlock.objects.filter(file=node).count() == 2

    def test_overwrite_releases_old_blocks(self):
        node1 = write_file(1, "/overwrite.txt", b"old")
        old_block_ids = set(
            FileBlock.objects.filter(file=node1).values_list("block_id", flat=True)
        )
        node2 = write_file(1, "/overwrite.txt", b"new")
        new_block_ids = set(
            FileBlock.objects.filter(file=node2).values_list("block_id", flat=True)
        )
        assert node1.pk == node2.pk
        assert not old_block_ids & new_block_ids
        # Old storage blocks should be marked free.
        assert StorageBlock.objects.filter(
            id__in=old_block_ids, is_free=True
        ).count() == len(old_block_ids)

    def test_version_conflict_on_overwrite(self):
        from unittest.mock import patch

        from django_fsspec.exceptions import FileConflictError

        write_file(1, "/conflict.txt", b"v1")

        real_get = FileNode.objects.get

        def stale_get(*args, **kwargs):
            obj = real_get(*args, **kwargs)
            if getattr(obj, "path", None) == "/conflict.txt":
                FileNode.objects.filter(pk=obj.pk).update(version=obj.version + 10)
            return obj

        with patch.object(FileNode.objects, "get", side_effect=stale_get):
            with pytest.raises(FileConflictError):
                write_file(1, "/conflict.txt", b"v2")

    def test_write_exceeds_max_file_size_rolls_back(self):
        from django.conf import settings
        from django_fsspec.exceptions import FileTooLargeError

        with pytest.raises(FileTooLargeError):
            write_file(1, "/huge.txt", b"x" * (settings.DATA_UPLOAD_MAX_MEMORY_SIZE + 1))
        assert not FileNode.objects.filter(path="/huge.txt").exists()

    def test_range_read_after_append(self):
        append_file(1, "/range-append.txt", b"abcdefghij")
        append_file(1, "/range-append.txt", b"klmnopqrst")
        assert read_file_range(1, "/range-append.txt", 5, 15) == b"fghijklmno"

    def test_copy_file_streams_blocks(self):
        write_file(1, "/copy-src.txt", b"copy me please")
        copy_file(1, "/copy-src.txt", "/copy-dst.txt", integrity="checksum")
        assert read_file(1, "/copy-dst.txt") == b"copy me please"

    def test_writer_rejects_invalid_mode(self):
        with pytest.raises(ValueError, match="Unsupported writer mode"):
            StreamingFileWriter(1, "/mode.txt", "rb")

    def test_writer_already_started(self):
        writer = StreamingFileWriter(1, "/started.txt", "wb")
        writer.start()
        try:
            with pytest.raises(RuntimeError, match="Writer already started"):
                writer.start()
        finally:
            writer.discard()

    def test_writer_must_be_started_before_write(self):
        writer = StreamingFileWriter(1, "/not-started.txt", "wb")
        with pytest.raises(RuntimeError, match="Writer must be started"):
            writer.write_chunk(b"data")

    def test_writer_file_node_before_start(self):
        writer = StreamingFileWriter(1, "/no-node.txt", "wb")
        with pytest.raises(RuntimeError, match="Writer has not been started"):
            writer.file_node

    def test_writer_closed_after_commit(self):
        writer = StreamingFileWriter(1, "/closed.txt", "wb")
        writer.start()
        writer.write_chunk(b"x", final=True)
        writer.commit()
        with pytest.raises(ValueError, match="Writer is closed"):
            writer.write_chunk(b"y")

    def test_copy_file_integrity_metadata_detects_free_block(self):
        write_file(1, "/src-free.txt", b"data")
        node = FileNode.objects.get(path="/src-free.txt")
        block_id = FileBlock.objects.filter(file=node).first().block_id
        StorageBlock.objects.filter(pk=block_id).update(is_free=True)
        with pytest.raises(DataIntegrityError):
            copy_file(1, "/src-free.txt", "/dst-free.txt", integrity="metadata")

    def test_copy_file_integrity_checksum_detects_corruption(self):
        write_file(1, "/src-bad.txt", b"data")
        node = FileNode.objects.get(path="/src-bad.txt")
        block = FileBlock.objects.filter(file=node).first().block
        block.data = b"XXXX"
        block.checksum = hashlib.sha256(b"data").hexdigest()
        block.save()
        with pytest.raises(DataIntegrityError):
            copy_file(1, "/src-bad.txt", "/dst-bad.txt", integrity="checksum")

    def test_read_file_streaming_range_invalid_range_returns_empty(self):
        write_file(1, "/stream-range.txt", b"abcdef")
        assert b"".join(read_file_streaming_range(1, "/stream-range.txt", 3, 3)) == b""
        assert b"".join(read_file_streaming_range(1, "/stream-range.txt", 10, 20)) == b""

    def test_read_file_streaming_range_directory(self):
        from django_fsspec.operations import make_directory
        make_directory(1, "/stream-dir")
        with pytest.raises(IsADirectoryError):
            list(read_file_streaming_range(1, "/stream-dir", 0, 1))

    def test_read_file_streaming_range_version_mismatch(self):
        write_file(1, "/stream-version.txt", b"data")
        node = FileNode.objects.get(path="/stream-version.txt")
        with pytest.raises(FileConflictError):
            list(read_file_streaming_range(1, "/stream-version.txt", 0, 4, version=node.version + 1))

    def test_read_file_range_past_eof_returns_empty(self):
        write_file(1, "/range-past.txt", b"abc")
        assert read_file_range(1, "/range-past.txt", 5, 10) == b""
        assert read_file_range(1, "/range-past.txt", 2, 10) == b"c"

    def test_get_read_integrity_rejects_invalid_policy(self):
        from django_fsspec.operations import _get_read_integrity
        with pytest.raises(ValueError, match="DJANGO_FSSPEC_READ_INTEGRITY"):
            _get_read_integrity("invalid")

    def test_copy_file_source_not_found(self):
        with pytest.raises(FileNotFoundError):
            copy_file(1, "/missing-src.txt", "/dst.txt")

    def test_ensure_parent_directory_require_exists(self):
        from django_fsspec.operations import _ensure_parent_directory
        with pytest.raises(FileNotFoundError):
            _ensure_parent_directory(1, "/missing/file.txt", require_exists=True)

    def test_append_to_empty_file_starts_sequence_at_zero(self):
        write_file(1, "/empty-append.txt", b"")
        append_file(1, "/empty-append.txt", b"data")
        node = FileNode.objects.get(path="/empty-append.txt")
        sequences = list(
            FileBlock.objects.filter(file=node)
            .order_by("sequence")
            .values_list("sequence", flat=True)
        )
        assert sequences == [0]

    def test_read_file_streaming_range_file_id_not_found(self):
        with pytest.raises(FileNotFoundError):
            list(read_file_streaming_range(1, "/no-id.txt", 0, 1, file_id=999999))
