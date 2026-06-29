from django.db import connection
from django.test import TestCase, override_settings

from django_fsspec.migrations_ops import RechunkOperation
from django_fsspec.models import FileBlock, FileNode, StorageBlock
from django_fsspec.operations import read_file, write_file


class TestRechunkOperation(TestCase):
    def test_rechunk_basic(self):
        data = b"A" * 1000
        write_file(1, "/test.txt", data)

        node = FileNode.objects.get(path="/test.txt")
        assert FileBlock.objects.filter(file=node).count() == 1

        self._do_rechunk(500)

        node.refresh_from_db()
        assert node.block_size == 500
        assert FileBlock.objects.filter(file=node).count() == 2
        assert read_file(1, "/test.txt") == data

    def test_rechunk_to_larger(self):
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            data = b"B" * 350
            write_file(1, "/test.txt", data)

        node = FileNode.objects.get(path="/test.txt")
        assert node.block_size == 100
        assert FileBlock.objects.filter(file=node).count() == 4

        self._do_rechunk(500)

        node.refresh_from_db()
        assert node.block_size == 500
        assert FileBlock.objects.filter(file=node).count() == 1
        assert read_file(1, "/test.txt") == data

    def test_rechunk_skips_matching(self):
        write_file(1, "/test.txt", b"data")
        node = FileNode.objects.get(path="/test.txt")
        original_block_size = node.block_size

        self._do_rechunk(original_block_size)

        node.refresh_from_db()
        assert node.block_size == original_block_size

    def test_rechunk_empty_file(self):
        write_file(1, "/empty.txt", b"")
        self._do_rechunk(500)
        node = FileNode.objects.get(path="/empty.txt")
        assert node.block_size == 500
        assert read_file(1, "/empty.txt") == b""

    def test_rechunk_multiple_files(self):
        write_file(1, "/a.txt", b"aaa")
        write_file(1, "/b.txt", b"bbb")

        self._do_rechunk(2)

        for path, data in [("/a.txt", b"aaa"), ("/b.txt", b"bbb")]:
            node = FileNode.objects.get(path=path)
            assert node.block_size == 2
            assert read_file(1, path) == data

    def test_rechunk_marks_old_blocks_free(self):
        write_file(1, "/test.txt", b"data")
        old_block_count = StorageBlock.objects.filter(is_free=False).count()

        self._do_rechunk(2)

        free_count = StorageBlock.objects.filter(is_free=True).count()
        assert free_count == old_block_count

    def _do_rechunk(self, new_block_size):
        """Use the actual RechunkOperation class via database_forwards."""
        op = RechunkOperation(new_block_size=new_block_size)

        class MockSchemaEditor:
            connection = connection

        # Create a mock state that provides get_model
        class MockState:
            class apps:
                @staticmethod
                def get_model(app_label, model_name):
                    return {
                        "FileNode": FileNode,
                        "FileBlock": FileBlock,
                        "StorageBlock": StorageBlock,
                    }[model_name]

        op.database_forwards("django_fsspec", MockSchemaEditor(), MockState(), MockState())


class TestRechunkOperationMeta(TestCase):
    def test_describe(self):
        op = RechunkOperation(new_block_size=64 * 1024)
        assert "65536" in op.describe()
        assert "block size" in op.describe().lower()

    def test_deconstruct(self):
        op = RechunkOperation(new_block_size=64 * 1024)
        name, args, kwargs = op.deconstruct()
        assert kwargs["new_block_size"] == 64 * 1024
        assert args == []

    def test_state_forwards_noop(self):
        op = RechunkOperation(new_block_size=64 * 1024)
        op.state_forwards("django_fsspec", None)  # should not raise

    def test_reduces_to_sql(self):
        op = RechunkOperation(new_block_size=64 * 1024)
        assert op.reduces_to_sql is False

    def test_invalid_block_size_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            RechunkOperation(new_block_size=0)

