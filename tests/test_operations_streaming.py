import hashlib

import pytest
from django.test import TestCase

from django_fsspec.fs import DjangoFileSystem
from django_fsspec.models import FileBlock, FileNode, StorageBlock
from django_fsspec.operations import (
    append_file,
    read_file,
    read_file_range,
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
        from django_fsspec.operations import copy_file

        copy_file(1, "/copy-src.txt", "/copy-dst.txt", integrity="checksum")
        assert read_file(1, "/copy-dst.txt") == b"copy me please"
