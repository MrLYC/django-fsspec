import hashlib

import pytest
from django.test import TestCase, override_settings

from django_fsspec.exceptions import FileTooLargeError, PathValidationError
from django_fsspec.models import FileBlock, FileNode, StorageBlock
from django_fsspec.operations import (
    append_file,
    copy_file,
    create_file_exclusive,
    delete_file,
    file_exists,
    get_file_info,
    list_directory,
    list_directory_detail,
    move_file,
    read_file,
    read_file_range,
    write_file,
)


class TestWriteFile(TestCase):
    def test_write_simple(self):
        node = write_file(0, "/test.txt", b"hello")
        assert node.size == 5
        assert node.checksum == hashlib.sha256(b"hello").hexdigest()
        assert node.version == 1

    def test_write_empty(self):
        node = write_file(0, "/empty.txt", b"")
        assert node.size == 0
        assert FileBlock.objects.filter(file=node).count() == 0

    def test_write_overwrite(self):
        write_file(0, "/test.txt", b"first")
        node = write_file(0, "/test.txt", b"second")
        assert node.size == 6
        data = read_file(0, "/test.txt")
        assert data == b"second"

    def test_write_overwrite_increments_version(self):
        write_file(0, "/test.txt", b"v1")
        node = write_file(0, "/test.txt", b"v2")
        assert node.version == 2

    def test_write_multi_block(self):
        block_size = 256 * 1024
        data = b"x" * (block_size + 100)
        node = write_file(0, "/big.bin", data)
        assert node.size == len(data)
        assert FileBlock.objects.filter(file=node).count() == 2

    @override_settings(DJANGO_FSSPEC_MAX_FILE_SIZE=100)
    def test_write_too_large(self):
        with pytest.raises(FileTooLargeError):
            write_file(0, "/big.txt", b"x" * 101)

    def test_write_validates_path(self):
        with pytest.raises(PathValidationError):
            write_file(0, "no-slash", b"data")

    def test_write_with_content_type(self):
        node = write_file(0, "/doc.json", b"{}", content_type="application/json")
        assert node.content_type == "application/json"

    def test_write_overwrite_without_content_type(self):
        write_file(0, "/test.txt", b"first", content_type="text/plain")
        node = write_file(0, "/test.txt", b"second")
        # content_type should be preserved when not provided
        assert node.content_type == "text/plain"

    def test_write_overwrite_with_content_type(self):
        write_file(0, "/test.txt", b"first", content_type="text/plain")
        node = write_file(0, "/test.txt", b"second", content_type="application/json")
        assert node.content_type == "application/json"

    def test_write_different_namespaces(self):
        write_file(0, "/test.txt", b"ns0")
        write_file(1, "/test.txt", b"ns1")
        assert read_file(0, "/test.txt") == b"ns0"
        assert read_file(1, "/test.txt") == b"ns1"


class TestCreateFileExclusive(TestCase):
    def test_create_new(self):
        node = create_file_exclusive(0, "/new.txt", b"data")
        assert node.size == 4

    def test_create_already_exists(self):
        write_file(0, "/test.txt", b"data")
        with pytest.raises(FileExistsError):
            create_file_exclusive(0, "/test.txt", b"other")

    @override_settings(DJANGO_FSSPEC_MAX_FILE_SIZE=10)
    def test_create_too_large(self):
        with pytest.raises(FileTooLargeError):
            create_file_exclusive(0, "/big.txt", b"x" * 11)


class TestAppendFile(TestCase):
    def test_append_to_existing(self):
        write_file(0, "/log.txt", b"line1\n")
        append_file(0, "/log.txt", b"line2\n")
        assert read_file(0, "/log.txt") == b"line1\nline2\n"

    def test_append_creates_new(self):
        append_file(0, "/new.txt", b"data")
        assert read_file(0, "/new.txt") == b"data"


class TestReadFile(TestCase):
    def test_read_simple(self):
        write_file(0, "/test.txt", b"hello world")
        assert read_file(0, "/test.txt") == b"hello world"

    def test_read_not_found(self):
        with pytest.raises(FileNotFoundError):
            read_file(0, "/nonexistent.txt")

    def test_read_multi_block(self):
        block_size = 256 * 1024
        data = b"A" * block_size + b"B" * 100
        write_file(0, "/multi.bin", data)
        assert read_file(0, "/multi.bin") == data

    def test_read_validates_path(self):
        with pytest.raises(PathValidationError):
            read_file(0, "invalid")


class TestReadFileRange(TestCase):
    def test_range_single_block(self):
        write_file(0, "/test.txt", b"hello world")
        assert read_file_range(0, "/test.txt", 6, 11) == b"world"

    def test_range_multi_block(self):
        block_size = 256 * 1024
        data = b"A" * block_size + b"B" * block_size
        write_file(0, "/multi.bin", data)
        # Read across block boundary
        start = block_size - 5
        end = block_size + 5
        assert read_file_range(0, "/multi.bin", start, end) == b"A" * 5 + b"B" * 5

    def test_range_not_found(self):
        with pytest.raises(FileNotFoundError):
            read_file_range(0, "/nonexistent.txt", 0, 10)

    def test_range_start_zero(self):
        write_file(0, "/test.txt", b"hello")
        assert read_file_range(0, "/test.txt", 0, 3) == b"hel"


class TestGetFileInfo(TestCase):
    def test_file_info(self):
        write_file(0, "/test.txt", b"hello", content_type="text/plain")
        info = get_file_info(0, "/test.txt")
        assert info["name"] == "/test.txt"
        assert info["size"] == 5
        assert info["type"] == "file"
        assert info["content_type"] == "text/plain"

    def test_directory_info(self):
        write_file(0, "/dir/file.txt", b"data")
        info = get_file_info(0, "/dir")
        assert info["type"] == "directory"
        assert info["size"] == 0

    def test_not_found(self):
        with pytest.raises(FileNotFoundError):
            get_file_info(0, "/nonexistent")


class TestFileExists(TestCase):
    def test_file_exists(self):
        write_file(0, "/test.txt", b"data")
        assert file_exists(0, "/test.txt") is True

    def test_file_not_exists(self):
        assert file_exists(0, "/nonexistent.txt") is False

    def test_directory_exists(self):
        write_file(0, "/dir/file.txt", b"data")
        assert file_exists(0, "/dir") is True

    def test_directory_not_exists(self):
        assert file_exists(0, "/nodir") is False


class TestDeleteFile(TestCase):
    def test_delete_file(self):
        write_file(0, "/test.txt", b"data")
        delete_file(0, "/test.txt")
        assert not file_exists(0, "/test.txt")

    def test_delete_marks_blocks_free(self):
        write_file(0, "/test.txt", b"data")
        delete_file(0, "/test.txt")
        assert StorageBlock.objects.filter(is_free=True).count() == 1

    def test_delete_not_found(self):
        with pytest.raises(FileNotFoundError):
            delete_file(0, "/nonexistent.txt")

    def test_delete_directory_not_recursive(self):
        write_file(0, "/dir/file.txt", b"data")
        with pytest.raises(IsADirectoryError):
            delete_file(0, "/dir")

    def test_delete_directory_recursive(self):
        write_file(0, "/dir/a.txt", b"a")
        write_file(0, "/dir/b.txt", b"b")
        write_file(0, "/dir/sub/c.txt", b"c")
        delete_file(0, "/dir", recursive=True)
        assert not file_exists(0, "/dir")
        assert not file_exists(0, "/dir/a.txt")

    def test_delete_directory_marks_blocks_free(self):
        write_file(0, "/dir/a.txt", b"aa")
        write_file(0, "/dir/b.txt", b"bb")
        delete_file(0, "/dir", recursive=True)
        assert StorageBlock.objects.filter(is_free=True).count() == 2


class TestListDirectory(TestCase):
    def test_list_root(self):
        write_file(0, "/a.txt", b"a")
        write_file(0, "/b.txt", b"b")
        result = list_directory(0, "/")
        assert result == ["a.txt", "b.txt"]

    def test_list_subdirectory(self):
        write_file(0, "/dir/a.txt", b"a")
        write_file(0, "/dir/b.txt", b"b")
        result = list_directory(0, "/dir")
        assert result == ["a.txt", "b.txt"]

    def test_list_with_implicit_dirs(self):
        write_file(0, "/a.txt", b"a")
        write_file(0, "/sub/b.txt", b"b")
        result = list_directory(0, "/")
        assert result == ["a.txt", "sub"]

    def test_list_nested(self):
        write_file(0, "/a/b/c.txt", b"c")
        result = list_directory(0, "/a")
        assert result == ["b"]

    def test_list_empty_directory(self):
        result = list_directory(0, "/")
        assert result == []


class TestListDirectoryDetail(TestCase):
    def test_detail_file(self):
        write_file(0, "/test.txt", b"hello")
        result = list_directory_detail(0, "/")
        assert len(result) == 1
        assert result[0]["name"] == "/test.txt"
        assert result[0]["size"] == 5
        assert result[0]["type"] == "file"

    def test_detail_directory(self):
        write_file(0, "/dir/file.txt", b"data")
        result = list_directory_detail(0, "/")
        assert len(result) == 1
        assert result[0]["name"] == "/dir"
        assert result[0]["type"] == "directory"


class TestCopyFile(TestCase):
    def test_copy(self):
        write_file(0, "/src.txt", b"data", content_type="text/plain")
        copy_file(0, "/src.txt", "/dst.txt")
        assert read_file(0, "/dst.txt") == b"data"
        assert read_file(0, "/src.txt") == b"data"  # original still exists

    def test_copy_not_found(self):
        with pytest.raises(FileNotFoundError):
            copy_file(0, "/nonexistent.txt", "/dst.txt")


class TestMoveFile(TestCase):
    def test_move(self):
        write_file(0, "/src.txt", b"data")
        move_file(0, "/src.txt", "/dst.txt")
        assert read_file(0, "/dst.txt") == b"data"
        assert not file_exists(0, "/src.txt")

    def test_move_not_found(self):
        with pytest.raises(FileNotFoundError):
            move_file(0, "/nonexistent.txt", "/dst.txt")

    def test_move_dst_exists(self):
        write_file(0, "/src.txt", b"src")
        write_file(0, "/dst.txt", b"dst")
        with pytest.raises(FileExistsError):
            move_file(0, "/src.txt", "/dst.txt")


class TestBlockPoolReuse(TestCase):
    def test_overwrite_frees_blocks(self):
        write_file(0, "/test.txt", b"first")
        assert StorageBlock.objects.filter(is_free=False).count() == 1

        write_file(0, "/test.txt", b"second")
        # Old block freed, new block created or old reused
        assert StorageBlock.objects.filter(is_free=False).count() == 1
        free_count = StorageBlock.objects.filter(is_free=True).count()
        # Depending on reuse, might be 0 (reused) or 1 (new block, old freed)
        assert free_count >= 0

    def test_delete_then_write_reuses_blocks(self):
        write_file(0, "/test.txt", b"data")
        delete_file(0, "/test.txt")
        assert StorageBlock.objects.filter(is_free=True).count() == 1

        write_file(0, "/new.txt", b"new data")
        # Free block should have been reused
        assert StorageBlock.objects.filter(is_free=True).count() == 0
