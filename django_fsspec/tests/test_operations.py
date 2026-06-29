import hashlib

import pytest
from django.test import TestCase, override_settings

from django_fsspec.exceptions import (
    FileConflictError,
    FileTooLargeError,
    NamespaceNotFoundError,
    PathValidationError,
)
from django_fsspec.models import FileBlock, FileNode, Namespace, StorageBlock
from django_fsspec.operations import (
    append_file,
    copy_file,
    create_file_exclusive,
    delete_file,
    file_exists,
    get_file_info,
    list_directory,
    list_directory_detail,
    make_directory,
    move_file,
    read_file,
    read_file_range,
    remove_directory,
    write_file,
)


class TestWriteFile(TestCase):
    def test_write_simple(self):
        node = write_file(1, "/test.txt", b"hello")
        assert node.size == 5
        assert node.checksum == hashlib.sha256(b"hello").hexdigest()
        assert node.version == 1

    def test_write_empty(self):
        node = write_file(1, "/empty.txt", b"")
        assert node.size == 0
        assert FileBlock.objects.filter(file=node).count() == 0

    def test_write_overwrite(self):
        write_file(1, "/test.txt", b"first")
        node = write_file(1, "/test.txt", b"second")
        assert node.size == 6
        data = read_file(1, "/test.txt")
        assert data == b"second"

    def test_write_overwrite_increments_version(self):
        write_file(1, "/test.txt", b"v1")
        node = write_file(1, "/test.txt", b"v2")
        assert node.version == 2

    def test_write_multi_block(self):
        block_size = 256 * 1024
        data = b"x" * (block_size + 100)
        node = write_file(1, "/big.bin", data)
        assert node.size == len(data)
        assert FileBlock.objects.filter(file=node).count() == 2

    @override_settings(DJANGO_FSSPEC_MAX_FILE_SIZE=100)
    def test_write_too_large(self):
        with pytest.raises(FileTooLargeError):
            write_file(1, "/big.txt", b"x" * 101)

    def test_write_validates_path(self):
        with pytest.raises(PathValidationError):
            write_file(1, "no-slash", b"data")

    def test_write_root_path_fails(self):
        with pytest.raises(IsADirectoryError):
            write_file(1, "/", b"data")

    def test_write_with_content_type(self):
        node = write_file(1, "/doc.json", b"{}", content_type="application/json")
        assert node.content_type == "application/json"

    def test_write_overwrite_without_content_type(self):
        write_file(1, "/test.txt", b"first", content_type="text/plain")
        node = write_file(1, "/test.txt", b"second")
        # content_type should be preserved when not provided
        assert node.content_type == "text/plain"

    def test_write_overwrite_with_content_type(self):
        write_file(1, "/test.txt", b"first", content_type="text/plain")
        node = write_file(1, "/test.txt", b"second", content_type="application/json")
        assert node.content_type == "application/json"

    def test_write_different_namespaces(self):
        from django_fsspec.models import Namespace

        Namespace.objects.create(id=2, name="other")
        write_file(1, "/test.txt", b"ns1")
        write_file(2, "/test.txt", b"ns2")
        assert read_file(1, "/test.txt") == b"ns1"
        assert read_file(2, "/test.txt") == b"ns2"

    def test_write_missing_namespace_raises_clear_error(self):
        with pytest.raises(NamespaceNotFoundError, match="Namespace not found: 0"):
            write_file(0, "/test.txt", b"data")

    def test_write_rejects_implicit_directory_target(self):
        write_file(1, "/reports/2026/q1.csv", b"q1")

        with pytest.raises(IsADirectoryError):
            write_file(1, "/reports/2026", b"not a directory")

        assert read_file(1, "/reports/2026/q1.csv") == b"q1"
        assert get_file_info(1, "/reports/2026")["type"] == "directory"

    def test_write_rejects_file_ancestor(self):
        write_file(1, "/reports", b"file")

        with pytest.raises(NotADirectoryError):
            write_file(1, "/reports/2026/q1.csv", b"q1")

        assert read_file(1, "/reports") == b"file"
        assert not file_exists(1, "/reports/2026")


class TestCreateFileExclusive(TestCase):
    def test_create_new(self):
        node = create_file_exclusive(1, "/new.txt", b"data")
        assert node.size == 4

    def test_create_already_exists(self):
        write_file(1, "/test.txt", b"data")
        with pytest.raises(FileExistsError):
            create_file_exclusive(1, "/test.txt", b"other")

    def test_create_rejects_implicit_directory_target(self):
        write_file(1, "/archive/2026/january.txt", b"jan")

        with pytest.raises(IsADirectoryError):
            create_file_exclusive(1, "/archive/2026", b"not a directory")

    @override_settings(DJANGO_FSSPEC_MAX_FILE_SIZE=10)
    def test_create_too_large(self):
        with pytest.raises(FileTooLargeError):
            create_file_exclusive(1, "/big.txt", b"x" * 11)


class TestAppendFile(TestCase):
    def test_append_to_existing(self):
        write_file(1, "/log.txt", b"line1\n")
        append_file(1, "/log.txt", b"line2\n")
        assert read_file(1, "/log.txt") == b"line1\nline2\n"

    def test_append_creates_new(self):
        append_file(1, "/new.txt", b"data")
        assert read_file(1, "/new.txt") == b"data"

    def test_append_increments_version(self):
        write_file(1, "/ver.txt", b"v1")
        append_file(1, "/ver.txt", b"+v2")
        node = FileNode.objects.get(path="/ver.txt")
        assert node.version == 2

    def test_append_preserves_content_type(self):
        write_file(1, "/typed.txt", b"start", content_type="text/plain")
        append_file(1, "/typed.txt", b"+more")
        node = FileNode.objects.get(path="/typed.txt")
        assert node.content_type == "text/plain"

    @override_settings(DJANGO_FSSPEC_MAX_FILE_SIZE=20)
    def test_append_too_large(self):
        write_file(1, "/big.txt", b"x" * 15)
        with pytest.raises(FileTooLargeError):
            append_file(1, "/big.txt", b"y" * 10)
        # Original data should be intact (transaction rolled back)
        assert read_file(1, "/big.txt") == b"x" * 15

    @override_settings(DJANGO_FSSPEC_MAX_FILE_SIZE=5)
    def test_append_new_file_too_large(self):
        with pytest.raises(FileTooLargeError):
            append_file(1, "/new_big.txt", b"x" * 6)

        assert not file_exists(1, "/new_big.txt")

    def test_append_conflict(self):
        """Simulate optimistic lock conflict during append."""
        from unittest.mock import patch

        write_file(1, "/conflict.txt", b"original")

        real_get = FileNode.objects.get

        def stale_get(**kwargs):
            obj = real_get(**kwargs)
            if kwargs.get("path") == "/conflict.txt" or \
               (kwargs.get("namespace_id") == 1 and hasattr(obj, 'path') and obj.path == "/conflict.txt"):
                FileNode.objects.filter(pk=obj.pk).update(version=999)
            return obj

        with patch.object(FileNode.objects, "get", side_effect=stale_get):
            with pytest.raises(FileConflictError):
                append_file(1, "/conflict.txt", b"+appended")

    def test_append_rejects_implicit_directory_target(self):
        write_file(1, "/logs/2026/app.log", b"line1\n")

        with pytest.raises(IsADirectoryError):
            append_file(1, "/logs/2026", b"line2\n")

        assert read_file(1, "/logs/2026/app.log") == b"line1\n"

    def test_append_rejects_file_ancestor(self):
        write_file(1, "/logs", b"flat log")

        with pytest.raises(NotADirectoryError):
            append_file(1, "/logs/2026/app.log", b"line\n")

        assert read_file(1, "/logs") == b"flat log"


class TestReadFile(TestCase):
    def test_read_simple(self):
        write_file(1, "/test.txt", b"hello world")
        assert read_file(1, "/test.txt") == b"hello world"

    def test_read_not_found(self):
        with pytest.raises(FileNotFoundError):
            read_file(1, "/nonexistent.txt")

    def test_read_multi_block(self):
        block_size = 256 * 1024
        data = b"A" * block_size + b"B" * 100
        write_file(1, "/multi.bin", data)
        assert read_file(1, "/multi.bin") == data

    def test_read_validates_path(self):
        with pytest.raises(PathValidationError):
            read_file(1, "invalid")


class TestReadFileRange(TestCase):
    def test_range_single_block(self):
        write_file(1, "/test.txt", b"hello world")
        assert read_file_range(1, "/test.txt", 6, 11) == b"world"

    def test_range_multi_block(self):
        block_size = 256 * 1024
        data = b"A" * block_size + b"B" * block_size
        write_file(1, "/multi.bin", data)
        # Read across block boundary
        start = block_size - 5
        end = block_size + 5
        assert read_file_range(1, "/multi.bin", start, end) == b"A" * 5 + b"B" * 5

    def test_range_not_found(self):
        with pytest.raises(FileNotFoundError):
            read_file_range(1, "/nonexistent.txt", 0, 10)

    def test_range_start_zero(self):
        write_file(1, "/test.txt", b"hello")
        assert read_file_range(1, "/test.txt", 0, 3) == b"hel"

    def test_range_directory_fails(self):
        make_directory(1, "/dir")

        with pytest.raises(IsADirectoryError):
            read_file_range(1, "/dir", 0, 1)


class TestGetFileInfo(TestCase):
    def test_file_info(self):
        write_file(1, "/test.txt", b"hello", content_type="text/plain")
        info = get_file_info(1, "/test.txt")
        assert info["name"] == "/test.txt"
        assert info["size"] == 5
        assert info["type"] == "file"
        assert info["content_type"] == "text/plain"

    def test_directory_info(self):
        write_file(1, "/dir/file.txt", b"data")
        info = get_file_info(1, "/dir")
        assert info["type"] == "directory"
        assert info["size"] == 0

    def test_not_found(self):
        with pytest.raises(FileNotFoundError):
            get_file_info(1, "/nonexistent")


class TestFileExists(TestCase):
    def test_file_exists(self):
        write_file(1, "/test.txt", b"data")
        assert file_exists(1, "/test.txt") is True

    def test_file_not_exists(self):
        assert file_exists(1, "/nonexistent.txt") is False

    def test_directory_exists(self):
        write_file(1, "/dir/file.txt", b"data")
        assert file_exists(1, "/dir") is True

    def test_directory_not_exists(self):
        assert file_exists(1, "/nodir") is False


class TestDeleteFile(TestCase):
    def test_delete_file(self):
        write_file(1, "/test.txt", b"data")
        delete_file(1, "/test.txt")
        assert not file_exists(1, "/test.txt")

    def test_delete_marks_blocks_free(self):
        write_file(1, "/test.txt", b"data")
        delete_file(1, "/test.txt")
        assert StorageBlock.objects.filter(is_free=True).count() == 1

    def test_delete_not_found(self):
        with pytest.raises(FileNotFoundError):
            delete_file(1, "/nonexistent.txt")

    def test_delete_directory_not_recursive(self):
        write_file(1, "/dir/file.txt", b"data")
        with pytest.raises(IsADirectoryError):
            delete_file(1, "/dir")

    def test_delete_directory_recursive(self):
        write_file(1, "/dir/a.txt", b"a")
        write_file(1, "/dir/b.txt", b"b")
        write_file(1, "/dir/sub/c.txt", b"c")
        delete_file(1, "/dir", recursive=True)
        assert not file_exists(1, "/dir")
        assert not file_exists(1, "/dir/a.txt")

    def test_delete_directory_marks_blocks_free(self):
        write_file(1, "/dir/a.txt", b"aa")
        write_file(1, "/dir/b.txt", b"bb")
        delete_file(1, "/dir", recursive=True)
        assert StorageBlock.objects.filter(is_free=True).count() == 2

    def test_delete_root_recursive_is_rejected_and_preserves_namespace(self):
        Namespace.objects.create(id=2, name="other")
        write_file(1, "/a.txt", b"default")
        write_file(2, "/a.txt", b"other")

        with pytest.raises(IsADirectoryError):
            delete_file(1, "/", recursive=True)

        assert read_file(1, "/a.txt") == b"default"
        assert read_file(2, "/a.txt") == b"other"


class TestListDirectory(TestCase):
    def test_list_root(self):
        write_file(1, "/a.txt", b"a")
        write_file(1, "/b.txt", b"b")
        result = list_directory(1, "/")
        assert result == ["a.txt", "b.txt"]

    def test_list_subdirectory(self):
        write_file(1, "/dir/a.txt", b"a")
        write_file(1, "/dir/b.txt", b"b")
        result = list_directory(1, "/dir")
        assert result == ["a.txt", "b.txt"]

    def test_list_with_implicit_dirs(self):
        write_file(1, "/a.txt", b"a")
        write_file(1, "/sub/b.txt", b"b")
        result = list_directory(1, "/")
        assert result == ["a.txt", "sub"]

    def test_list_nested(self):
        write_file(1, "/a/b/c.txt", b"c")
        result = list_directory(1, "/a")
        assert result == ["b"]

    def test_list_empty_directory(self):
        result = list_directory(1, "/")
        assert result == []

    def test_list_file_path_fails(self):
        write_file(1, "/file.txt", b"data")

        with pytest.raises(NotADirectoryError):
            list_directory(1, "/file.txt")


class TestListDirectoryDetail(TestCase):
    def test_detail_file(self):
        write_file(1, "/test.txt", b"hello")
        result = list_directory_detail(1, "/")
        assert len(result) == 1
        assert result[0]["name"] == "/test.txt"
        assert result[0]["size"] == 5
        assert result[0]["type"] == "file"

    def test_detail_directory(self):
        write_file(1, "/dir/file.txt", b"data")
        result = list_directory_detail(1, "/")
        assert len(result) == 1
        assert result[0]["name"] == "/dir"
        assert result[0]["type"] == "directory"


class TestDirectoryOperations(TestCase):
    def test_make_directory_creates_empty_directory(self):
        node = make_directory(1, "/empty")
        assert node.node_type == "directory"
        assert file_exists(1, "/empty") is True
        assert get_file_info(1, "/empty")["type"] == "directory"
        assert list_directory(1, "/") == ["empty"]

    def test_make_directory_with_parents(self):
        make_directory(1, "/a/b/c", create_parents=True)
        assert get_file_info(1, "/a")["type"] == "directory"
        assert get_file_info(1, "/a/b")["type"] == "directory"
        assert get_file_info(1, "/a/b/c")["type"] == "directory"

    def test_make_directory_parent_missing(self):
        with pytest.raises(FileNotFoundError):
            make_directory(1, "/a/b")

    def test_make_directory_root_fails(self):
        with pytest.raises(FileExistsError):
            make_directory(1, "/")

    def test_make_directory_existing_directory_fails(self):
        make_directory(1, "/dir")

        with pytest.raises(FileExistsError):
            make_directory(1, "/dir")

    def test_make_directory_existing_file_fails(self):
        write_file(1, "/dir", b"file")

        with pytest.raises(FileExistsError):
            make_directory(1, "/dir")

    def test_make_directory_existing_implicit_directory_fails(self):
        write_file(1, "/dir/file.txt", b"file")

        with pytest.raises(FileExistsError):
            make_directory(1, "/dir")

    def test_write_file_on_directory_fails(self):
        make_directory(1, "/dir")
        with pytest.raises(IsADirectoryError):
            write_file(1, "/dir", b"data")

    def test_make_directory_under_file_fails(self):
        write_file(1, "/dir", b"file")

        with pytest.raises(NotADirectoryError):
            make_directory(1, "/dir/sub", create_parents=True)

    def test_remove_empty_directory(self):
        make_directory(1, "/empty")
        remove_directory(1, "/empty")
        assert not file_exists(1, "/empty")

    def test_remove_root_directory_fails(self):
        with pytest.raises(IsADirectoryError):
            remove_directory(1, "/")

    def test_remove_implicit_directory_recursive(self):
        write_file(1, "/implicit/file.txt", b"data")

        remove_directory(1, "/implicit", recursive=True)

        assert not file_exists(1, "/implicit")

    def test_remove_non_empty_directory_requires_recursive(self):
        make_directory(1, "/dir")
        write_file(1, "/dir/file.txt", b"x")
        with pytest.raises(IsADirectoryError):
            remove_directory(1, "/dir")

    def test_remove_directory_recursive_releases_blocks(self):
        make_directory(1, "/dir")
        write_file(1, "/dir/file.txt", b"x")
        remove_directory(1, "/dir", recursive=True)
        assert not file_exists(1, "/dir")
        assert StorageBlock.objects.filter(is_free=True).count() == 1

    def test_remove_directory_rejects_file_path(self):
        write_file(1, "/file.txt", b"data")

        with pytest.raises(NotADirectoryError):
            remove_directory(1, "/file.txt")

        assert read_file(1, "/file.txt") == b"data"
        assert StorageBlock.objects.filter(is_free=False).count() == 1


class TestCopyFile(TestCase):
    def test_copy(self):
        write_file(1, "/src.txt", b"data", content_type="text/plain")
        copy_file(1, "/src.txt", "/dst.txt")
        assert read_file(1, "/dst.txt") == b"data"
        assert read_file(1, "/src.txt") == b"data"  # original still exists

    def test_copy_not_found(self):
        with pytest.raises(FileNotFoundError):
            copy_file(1, "/nonexistent.txt", "/dst.txt")

    def test_copy_rejects_implicit_directory_destination(self):
        write_file(1, "/src.txt", b"src")
        write_file(1, "/dst/existing.txt", b"existing")

        with pytest.raises(IsADirectoryError):
            copy_file(1, "/src.txt", "/dst")

        assert read_file(1, "/src.txt") == b"src"
        assert read_file(1, "/dst/existing.txt") == b"existing"

    def test_copy_rejects_source_directory(self):
        write_file(1, "/src/file.txt", b"src")

        with pytest.raises(IsADirectoryError):
            copy_file(1, "/src", "/dst.txt")


class TestMoveFile(TestCase):
    def test_move(self):
        write_file(1, "/src.txt", b"data")
        move_file(1, "/src.txt", "/dst.txt")
        assert read_file(1, "/dst.txt") == b"data"
        assert not file_exists(1, "/src.txt")

    def test_move_not_found(self):
        with pytest.raises(FileNotFoundError):
            move_file(1, "/nonexistent.txt", "/dst.txt")

    def test_move_explicit_directory_source_fails(self):
        make_directory(1, "/src")

        with pytest.raises(IsADirectoryError):
            move_file(1, "/src", "/dst")

    def test_move_dst_exists(self):
        write_file(1, "/src.txt", b"src")
        write_file(1, "/dst.txt", b"dst")
        with pytest.raises(FileExistsError):
            move_file(1, "/src.txt", "/dst.txt")

    def test_move_same_path_is_noop(self):
        write_file(1, "/same.txt", b"data")
        node = FileNode.objects.get(path="/same.txt")
        block_ids = list(
            FileBlock.objects.filter(file=node).values_list("block_id", flat=True)
        )

        move_file(1, "/same.txt", "/same.txt", overwrite=True)

        node.refresh_from_db()
        assert read_file(1, "/same.txt") == b"data"
        assert node.version == 1
        assert list(
            FileBlock.objects.filter(file=node).values_list("block_id", flat=True)
        ) == block_ids

    def test_move_overwrite_existing_file(self):
        write_file(1, "/src.txt", b"src")
        write_file(1, "/dst.txt", b"dst")

        move_file(1, "/src.txt", "/dst.txt", overwrite=True)

        assert read_file(1, "/dst.txt") == b"src"
        assert not file_exists(1, "/src.txt")
        assert StorageBlock.objects.filter(is_free=True).count() == 1

    def test_move_rejects_implicit_directory_source(self):
        write_file(1, "/src/file.txt", b"src")

        with pytest.raises(IsADirectoryError):
            move_file(1, "/src", "/dst.txt")

        assert read_file(1, "/src/file.txt") == b"src"

    def test_move_rejects_implicit_directory_destination(self):
        write_file(1, "/src.txt", b"src")
        write_file(1, "/dst/existing.txt", b"existing")

        with pytest.raises(IsADirectoryError):
            move_file(1, "/src.txt", "/dst")

        assert read_file(1, "/src.txt") == b"src"
        assert read_file(1, "/dst/existing.txt") == b"existing"

    def test_move_rejects_explicit_directory_destination(self):
        write_file(1, "/src.txt", b"src")
        make_directory(1, "/dst")

        with pytest.raises(IsADirectoryError):
            move_file(1, "/src.txt", "/dst", overwrite=True)

        assert read_file(1, "/src.txt") == b"src"


class TestBlockPoolReuse(TestCase):
    def test_overwrite_frees_blocks(self):
        write_file(1, "/test.txt", b"first")
        assert StorageBlock.objects.filter(is_free=False).count() == 1

        write_file(1, "/test.txt", b"second")
        assert StorageBlock.objects.filter(is_free=False).count() == 1
        assert StorageBlock.objects.filter(is_free=True).count() == 1

    def test_delete_then_write_does_not_reuse_free_blocks(self):
        write_file(1, "/test.txt", b"data")
        delete_file(1, "/test.txt")
        assert StorageBlock.objects.filter(is_free=True).count() == 1

        write_file(1, "/new.txt", b"new data")
        assert StorageBlock.objects.filter(is_free=True).count() == 1
        assert StorageBlock.objects.filter(is_free=False).count() == 1


class TestOptimisticLocking(TestCase):
    def test_version_increments_on_overwrite(self):
        write_file(1, "/test.txt", b"v1")
        node = FileNode.objects.get(namespace_id=1, path="/test.txt")
        assert node.version == 1

        write_file(1, "/test.txt", b"v2")
        node.refresh_from_db()
        assert node.version == 2

    def test_conflict_raises_error(self):
        """Simulate optimistic lock conflict by patching the version read."""
        from unittest.mock import patch

        write_file(1, "/test.txt", b"original")
        node = FileNode.objects.get(namespace_id=1, path="/test.txt")
        assert node.version == 1

        # Patch get() to return a stale version, simulating a race condition
        real_get = FileNode.objects.get

        def stale_get(**kwargs):
            obj = real_get(**kwargs)
            # Simulate another process having updated the version
            FileNode.objects.filter(pk=obj.pk).update(version=99)
            return obj

        with patch.object(FileNode.objects, "get", side_effect=stale_get):
            with pytest.raises(FileConflictError, match="modified by another process"):
                write_file(1, "/test.txt", b"conflict")

    def test_create_new_file_no_conflict(self):
        """Creating a new file should not involve optimistic lock."""
        node = write_file(1, "/new.txt", b"data")
        assert node.version == 1


class TestVerifyChecksum(TestCase):
    def test_read_with_verify_passes(self):
        write_file(1, "/test.txt", b"hello")
        data = read_file(1, "/test.txt", verify_checksum=True)
        assert data == b"hello"

    def test_read_without_verify_skips(self):
        write_file(1, "/test.txt", b"hello")
        # Corrupt block checksum
        block = StorageBlock.objects.filter(is_free=False).first()
        block.checksum = "bad"
        block.save(update_fields=["checksum"])
        # Should not raise without verify
        data = read_file(1, "/test.txt", verify_checksum=False)
        assert data == b"hello"

    def test_read_with_verify_detects_block_corruption(self):
        write_file(1, "/test.txt", b"hello")
        block = StorageBlock.objects.filter(is_free=False).first()
        block.checksum = "bad_checksum"
        block.save(update_fields=["checksum"])
        with pytest.raises(ValueError, match="Block.*checksum mismatch"):
            read_file(1, "/test.txt", verify_checksum=True)

    def test_read_with_verify_detects_file_corruption(self):
        write_file(1, "/test.txt", b"hello")
        node = FileNode.objects.get(path="/test.txt")
        node.checksum = "bad_file_checksum"
        node.save(update_fields=["checksum"])
        with pytest.raises(ValueError, match="File.*checksum mismatch"):
            read_file(1, "/test.txt", verify_checksum=True)


class TestBlockSizeCoexistence(TestCase):
    def test_different_block_sizes_coexist(self):
        """Files written with different block sizes can coexist and be read."""
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/small_blocks.txt", b"A" * 250)

        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=256 * 1024):
            write_file(1, "/large_blocks.txt", b"B" * 250)

        # Both should be readable
        assert read_file(1, "/small_blocks.txt") == b"A" * 250
        assert read_file(1, "/large_blocks.txt") == b"B" * 250

        # Check they have different block sizes
        small = FileNode.objects.get(path="/small_blocks.txt")
        large = FileNode.objects.get(path="/large_blocks.txt")
        assert small.block_size == 100
        assert large.block_size == 256 * 1024

        # Small file should have multiple blocks, large file should have one
        assert FileBlock.objects.filter(file=small).count() == 3
        assert FileBlock.objects.filter(file=large).count() == 1

    def test_range_read_with_custom_block_size(self):
        """Range read should work correctly with non-default block size."""
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=10):
            data = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            write_file(1, "/alpha.txt", data)

        # Read across block boundary
        assert read_file_range(1, "/alpha.txt", 8, 15) == b"IJKLMNO"
