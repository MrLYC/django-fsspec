import pytest
from django.db import IntegrityError
from django.test import TestCase

from django_fsspec.models import (
    FileBlock,
    FileNode,
    Namespace,
    StorageBlock,
    get_block_size,
    get_max_file_size,
)


class TestFileNode(TestCase):
    def test_create_file_node(self):
        node = FileNode.objects.create(
            namespace_id=1, path="/test.txt", size=100, block_size=256 * 1024
        )
        assert node.pk is not None
        assert node.version == 1
        assert node.checksum == ""

    def test_unique_constraint(self):
        FileNode.objects.create(namespace_id=1, path="/test.txt", block_size=256 * 1024)
        with pytest.raises(IntegrityError):
            FileNode.objects.create(
                namespace_id=1, path="/test.txt", block_size=256 * 1024
            )

    def test_different_namespaces(self):
        Namespace.objects.create(id=2, name="other")
        FileNode.objects.create(namespace_id=1, path="/test.txt", block_size=256 * 1024)
        node2 = FileNode.objects.create(
            namespace_id=2, path="/test.txt", block_size=256 * 1024
        )
        assert node2.pk is not None

    def test_str(self):
        node = FileNode(namespace_id=1, path="/test.txt")
        assert "test.txt" in str(node)


class TestStorageBlock(TestCase):
    def test_create_block(self):
        block = StorageBlock.objects.create(data=b"hello", size=5)
        assert block.pk is not None
        assert block.is_free is False

    def test_str(self):
        block = StorageBlock(pk=1, size=100, is_free=False)
        assert "100" in str(block)


class TestFileBlock(TestCase):
    def test_create_file_block(self):
        node = FileNode.objects.create(
            namespace_id=1, path="/test.txt", block_size=256 * 1024
        )
        block = StorageBlock.objects.create(data=b"hello", size=5)
        fb = FileBlock.objects.create(file=node, block=block, sequence=0)
        assert fb.pk is not None

    def test_unique_sequence(self):
        node = FileNode.objects.create(
            namespace_id=1, path="/test.txt", block_size=256 * 1024
        )
        block1 = StorageBlock.objects.create(data=b"a", size=1)
        block2 = StorageBlock.objects.create(data=b"b", size=1)
        FileBlock.objects.create(file=node, block=block1, sequence=0)
        with pytest.raises(IntegrityError):
            FileBlock.objects.create(file=node, block=block2, sequence=0)

    def test_ordering(self):
        node = FileNode.objects.create(
            namespace_id=1, path="/test.txt", block_size=256 * 1024
        )
        b1 = StorageBlock.objects.create(data=b"a", size=1)
        b2 = StorageBlock.objects.create(data=b"b", size=1)
        FileBlock.objects.create(file=node, block=b2, sequence=1)
        FileBlock.objects.create(file=node, block=b1, sequence=0)

        blocks = list(FileBlock.objects.filter(file=node))
        assert blocks[0].sequence == 0
        assert blocks[1].sequence == 1

    def test_str(self):
        fb = FileBlock(file_id=1, sequence=0)
        assert "seq=0" in str(fb)


class TestSettings(TestCase):
    def test_default_block_size(self):
        assert get_block_size() == 32 * 1024

    def test_default_max_file_size(self):
        assert get_max_file_size() == 2 * 1024 * 1024

    def test_custom_block_size(self):
        with self.settings(DJANGO_FSSPEC_BLOCK_SIZE=64 * 1024):
            assert get_block_size() == 64 * 1024

    def test_custom_max_file_size(self):
        with self.settings(DJANGO_FSSPEC_MAX_FILE_SIZE=1024):
            assert get_max_file_size() == 1024
