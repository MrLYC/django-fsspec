import pytest
from django.test import TestCase

from django_fsspec.buffer import DjangoFile
from django_fsspec.fs import DjangoFileSystem
from django_fsspec.models import Namespace
from django_fsspec.operations import write_file


class TestDjangoFileSystem(TestCase):
    def setUp(self):
        self.fs = DjangoFileSystem(namespace=1)

    def test_protocol(self):
        assert DjangoFileSystem.protocol == "django"

    def test_write_and_read(self):
        with self.fs.open("/test.txt", "wb") as f:
            f.write(b"hello world")
        assert self.fs.cat("/test.txt") == b"hello world"

    def test_write_overwrite(self):
        with self.fs.open("/test.txt", "wb") as f:
            f.write(b"first")
        with self.fs.open("/test.txt", "wb") as f:
            f.write(b"second")
        assert self.fs.cat("/test.txt") == b"second"

    def test_exclusive_create(self):
        with self.fs.open("/new.txt", "xb") as f:
            f.write(b"exclusive")
        assert self.fs.cat("/new.txt") == b"exclusive"

    def test_exclusive_create_exists(self):
        with self.fs.open("/test.txt", "wb") as f:
            f.write(b"data")
        with pytest.raises(FileExistsError):
            self.fs.open("/test.txt", "xb")

    def test_append(self):
        with self.fs.open("/log.txt", "wb") as f:
            f.write(b"line1\n")
        with self.fs.open("/log.txt", "ab") as f:
            f.write(b"line2\n")
        assert self.fs.cat("/log.txt") == b"line1\nline2\n"

    def test_append_new_file(self):
        with self.fs.open("/new.txt", "ab") as f:
            f.write(b"data")
        assert self.fs.cat("/new.txt") == b"data"

    def test_read_not_found(self):
        with pytest.raises(FileNotFoundError):
            self.fs.open("/nonexistent.txt", "rb")

    def test_unsupported_mode(self):
        with pytest.raises(ValueError, match="Unsupported mode"):
            self.fs.open("/test.txt", "r+b")

    def test_ls_root(self):
        write_file(1, "/a.txt", b"a")
        write_file(1, "/b.txt", b"b")
        result = self.fs.ls("/", detail=False)
        assert sorted(result) == ["/a.txt", "/b.txt"]

    def test_ls_detail(self):
        write_file(1, "/test.txt", b"hello")
        result = self.fs.ls("/", detail=True)
        assert len(result) == 1
        assert result[0]["name"] == "/test.txt"
        assert result[0]["size"] == 5
        assert result[0]["type"] == "file"

    def test_ls_subdirectory(self):
        write_file(1, "/dir/file.txt", b"data")
        result = self.fs.ls("/dir", detail=False)
        assert result == ["/dir/file.txt"]

    def test_ls_with_implicit_dirs(self):
        write_file(1, "/a.txt", b"a")
        write_file(1, "/sub/b.txt", b"b")
        result = self.fs.ls("/", detail=False)
        assert sorted(result) == ["/a.txt", "/sub"]

    def test_ls_file_directly(self):
        write_file(1, "/test.txt", b"data")
        result = self.fs.ls("/test.txt", detail=False)
        assert result == ["/test.txt"]

    def test_ls_file_directly_detail(self):
        write_file(1, "/test.txt", b"data")
        result = self.fs.ls("/test.txt", detail=True)
        assert len(result) == 1
        assert result[0]["name"] == "/test.txt"
        assert result[0]["type"] == "file"

    def test_ls_not_found(self):
        with pytest.raises(FileNotFoundError):
            self.fs.ls("/nonexistent")

    def test_info_file(self):
        write_file(1, "/test.txt", b"hello")
        info = self.fs.info("/test.txt")
        assert info["type"] == "file"
        assert info["size"] == 5

    def test_info_directory(self):
        write_file(1, "/dir/file.txt", b"data")
        info = self.fs.info("/dir")
        assert info["type"] == "directory"

    def test_info_root(self):
        info = self.fs.info("/")
        assert info["type"] == "directory"

    def test_exists(self):
        write_file(1, "/test.txt", b"data")
        assert self.fs.exists("/test.txt")
        assert not self.fs.exists("/nonexistent.txt")

    def test_exists_root(self):
        assert self.fs.exists("/")

    def test_exists_implicit_dir(self):
        write_file(1, "/dir/file.txt", b"data")
        assert self.fs.exists("/dir")

    def test_rm(self):
        write_file(1, "/test.txt", b"data")
        self.fs.rm("/test.txt")
        assert not self.fs.exists("/test.txt")

    def test_rm_recursive(self):
        write_file(1, "/dir/a.txt", b"a")
        write_file(1, "/dir/b.txt", b"b")
        self.fs.rm("/dir", recursive=True)
        assert not self.fs.exists("/dir")

    def test_rm_directory_not_recursive(self):
        write_file(1, "/dir/file.txt", b"data")
        with pytest.raises(IsADirectoryError):
            self.fs.rm("/dir")

    def test_cp_file(self):
        write_file(1, "/src.txt", b"data")
        self.fs.cp_file("/src.txt", "/dst.txt")
        assert self.fs.cat("/dst.txt") == b"data"
        assert self.fs.exists("/src.txt")

    def test_mv(self):
        write_file(1, "/src.txt", b"data")
        self.fs.mv("/src.txt", "/dst.txt")
        assert self.fs.cat("/dst.txt") == b"data"
        assert not self.fs.exists("/src.txt")

    def test_mkdir_creates_directory(self):
        self.fs.mkdir("/somedir")
        assert self.fs.exists("/somedir")
        assert self.fs.info("/somedir")["type"] == "directory"
        assert self.fs.ls("/", detail=False) == ["/somedir"]

    def test_makedirs_creates_parents(self):
        self.fs.makedirs("/a/b/c")
        assert self.fs.exists("/a")
        assert self.fs.exists("/a/b")
        assert self.fs.exists("/a/b/c")

    def test_rmdir_removes_empty_directory(self):
        self.fs.mkdir("/somedir")
        self.fs.rmdir("/somedir")
        assert not self.fs.exists("/somedir")

    def test_created(self):
        write_file(1, "/test.txt", b"data")
        created = self.fs.created("/test.txt")
        assert created is not None

    def test_modified(self):
        write_file(1, "/test.txt", b"data")
        modified = self.fs.modified("/test.txt")
        assert modified is not None

    def test_namespace_isolation(self):
        Namespace.objects.create(id=2, name="other")
        fs0 = DjangoFileSystem(namespace=1)
        fs1 = DjangoFileSystem(namespace=2)

        with fs0.open("/test.txt", "wb") as f:
            f.write(b"ns0")
        with fs1.open("/test.txt", "wb") as f:
            f.write(b"ns1")

        assert fs0.cat("/test.txt") == b"ns0"
        assert fs1.cat("/test.txt") == b"ns1"

    def test_seek_and_read(self):
        write_file(1, "/test.txt", b"hello world")
        with self.fs.open("/test.txt", "rb") as f:
            f.seek(6)
            assert f.read(5) == b"world"

    def test_read_partial(self):
        write_file(1, "/test.txt", b"hello world")
        with self.fs.open("/test.txt", "rb") as f:
            assert f.read(5) == b"hello"

    def test_ls_detail_implicit_dir(self):
        write_file(1, "/dir/sub/file.txt", b"data")
        result = self.fs.ls("/dir", detail=True)
        assert len(result) == 1
        assert result[0]["type"] == "directory"
        assert result[0]["name"] == "/dir/sub"


class TestDjangoFileSystemExtendedAPI(TestCase):
    """Test extended fsspec API methods."""

    def setUp(self):
        self.fs = DjangoFileSystem(namespace=1)

    def test_rm_file(self):
        write_file(1, "/rmfile.txt", b"data")
        self.fs.rm_file("/rmfile.txt")
        assert not self.fs.exists("/rmfile.txt")

    def test_rm_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            self.fs.rm_file("/nonexistent.txt")

    def test_internal_rm(self):
        write_file(1, "/rm_internal.txt", b"data")
        self.fs._rm("/rm_internal.txt")
        assert not self.fs.exists("/rm_internal.txt")

    def test_touch_create(self):
        self.fs.touch("/touched.txt")
        assert self.fs.exists("/touched.txt")
        assert self.fs.cat("/touched.txt") == b""

    def test_touch_truncate(self):
        write_file(1, "/touch_trunc.txt", b"existing data")
        self.fs.touch("/touch_trunc.txt", truncate=True)
        assert self.fs.cat("/touch_trunc.txt") == b""

    def test_touch_no_truncate_existing(self):
        write_file(1, "/touch_keep.txt", b"keep me")
        self.fs.touch("/touch_keep.txt", truncate=False)
        assert self.fs.cat("/touch_keep.txt") == b"keep me"

    def test_touch_no_truncate_new(self):
        self.fs.touch("/touch_new.txt", truncate=False)
        assert self.fs.exists("/touch_new.txt")

    def test_checksum_returns_sha256(self):
        write_file(1, "/chk.txt", b"hello")
        import hashlib
        expected = hashlib.sha256(b"hello").hexdigest()
        assert self.fs.checksum("/chk.txt") == expected

    def test_checksum_directory(self):
        write_file(1, "/dir/file.txt", b"data")
        result = self.fs.checksum("/dir")
        assert result == ""  # directories have no checksum

    def test_ukey_includes_version(self):
        write_file(1, "/ukey.txt", b"v1")
        key1 = self.fs.ukey("/ukey.txt")
        write_file(1, "/ukey.txt", b"v2")
        key2 = self.fs.ukey("/ukey.txt")
        assert key1 != key2  # version changed

    def test_ukey_stable_for_same_version(self):
        write_file(1, "/ukey_stable.txt", b"data")
        assert self.fs.ukey("/ukey_stable.txt") == self.fs.ukey("/ukey_stable.txt")

    def test_sign_not_supported(self):
        write_file(1, "/sign.txt", b"data")
        with pytest.raises(NotImplementedError, match="not supported"):
            self.fs.sign("/sign.txt")

    def test_find_flat(self):
        write_file(1, "/find/a.txt", b"a")
        write_file(1, "/find/b.txt", b"b")
        result = self.fs.find("/find")
        assert sorted(result) == ["/find/a.txt", "/find/b.txt"]

    def test_find_nested(self):
        write_file(1, "/find2/a.txt", b"a")
        write_file(1, "/find2/sub/b.txt", b"b")
        write_file(1, "/find2/sub/deep/c.txt", b"c")
        result = self.fs.find("/find2")
        assert sorted(result) == ["/find2/a.txt", "/find2/sub/b.txt", "/find2/sub/deep/c.txt"]

    def test_find_maxdepth(self):
        write_file(1, "/findmd/a.txt", b"a")
        write_file(1, "/findmd/sub/b.txt", b"b")
        write_file(1, "/findmd/sub/deep/c.txt", b"c")
        # maxdepth=1: direct children only
        result = self.fs.find("/findmd", maxdepth=1)
        assert sorted(result) == ["/findmd/a.txt"]
        # maxdepth=2: one level of nesting
        result = self.fs.find("/findmd", maxdepth=2)
        assert sorted(result) == ["/findmd/a.txt", "/findmd/sub/b.txt"]

    def test_find_detail(self):
        write_file(1, "/findd/test.txt", b"hello")
        result = self.fs.find("/findd", detail=True)
        assert "/findd/test.txt" in result
        assert result["/findd/test.txt"]["size"] == 5
        assert result["/findd/test.txt"]["type"] == "file"

    def test_find_withdirs(self):
        write_file(1, "/findwd/sub/file.txt", b"data")
        result = self.fs.find("/findwd", withdirs=True)
        assert "/findwd/sub" in result
        assert "/findwd/sub/file.txt" in result

    def test_find_root(self):
        write_file(1, "/root_find.txt", b"data")
        result = self.fs.find("/")
        assert "/root_find.txt" in result

    def test_size(self):
        write_file(1, "/sized.txt", b"12345")
        assert self.fs.size("/sized.txt") == 5

    def test_isfile(self):
        write_file(1, "/isf.txt", b"data")
        assert self.fs.isfile("/isf.txt")
        assert not self.fs.isfile("/nonexistent")

    def test_isdir(self):
        write_file(1, "/isd/file.txt", b"data")
        assert self.fs.isdir("/isd")
        assert not self.fs.isdir("/isd/file.txt")

    def test_head(self):
        write_file(1, "/head.txt", b"hello world")
        assert self.fs.head("/head.txt", size=5) == b"hello"

    def test_tail(self):
        write_file(1, "/tail.txt", b"hello world")
        assert self.fs.tail("/tail.txt", size=5) == b"world"

    def test_read_text(self):
        write_file(1, "/text.txt", b"hello")
        assert self.fs.read_text("/text.txt") == "hello"

    def test_write_text(self):
        self.fs.write_text("/wtext.txt", "hello")
        assert self.fs.cat("/wtext.txt") == b"hello"

    def test_pipe_and_cat(self):
        self.fs.pipe_file("/pipe.txt", b"piped")
        assert self.fs.cat_file("/pipe.txt") == b"piped"

    def test_walk(self):
        write_file(1, "/walk/a.txt", b"a")
        write_file(1, "/walk/sub/b.txt", b"b")
        walked = list(self.fs.walk("/walk"))
        # walk returns (dirpath, dirnames, filenames)
        assert len(walked) >= 1

    def test_glob(self):
        write_file(1, "/glob/foo.txt", b"foo")
        write_file(1, "/glob/bar.py", b"bar")
        result = self.fs.glob("/glob/*.txt")
        assert "/glob/foo.txt" in result
        assert "/glob/bar.py" not in result


class TestDjangoFileSystemFsspec(TestCase):
    """Test fsspec.filesystem() registration."""

    def test_fsspec_filesystem(self):
        import fsspec

        fs = fsspec.filesystem("django", namespace_id=1)
        assert isinstance(fs, DjangoFileSystem)

    def test_fsspec_roundtrip(self):
        import fsspec

        fs = fsspec.filesystem("django", namespace_id=1)
        fs.pipe("/roundtrip.txt", b"fsspec data")
        assert fs.cat("/roundtrip.txt") == b"fsspec data"
        fs.rm("/roundtrip.txt")


class TestDjangoTransaction(TestCase):
    """Test fsspec transaction backed by Django database transaction."""

    def setUp(self):
        self.fs = DjangoFileSystem(namespace=1)

    def test_transaction_commit(self):
        """Files written in a transaction should be visible after commit."""
        with self.fs.transaction:
            self.fs.pipe("/tx/a.txt", b"aaa")
            self.fs.pipe("/tx/b.txt", b"bbb")

        assert self.fs.cat("/tx/a.txt") == b"aaa"
        assert self.fs.cat("/tx/b.txt") == b"bbb"

    def test_transaction_rollback_on_exception(self):
        """Files written in a transaction should be rolled back on exception."""
        from django_fsspec.models import FileNode

        try:
            with self.fs.transaction:
                self.fs.pipe("/tx/will_rollback.txt", b"data")
                # Verify it's written within the transaction
                assert FileNode.objects.filter(path="/tx/will_rollback.txt").exists()
                raise ValueError("Intentional error")
        except ValueError:
            pass

        # After rollback, file should not exist
        assert not self.fs.exists("/tx/will_rollback.txt")
        assert not FileNode.objects.filter(path="/tx/will_rollback.txt").exists()

    def test_transaction_rollback_blocks_cleaned(self):
        """Rolled back transaction should not leave orphaned storage blocks."""
        from django_fsspec.models import StorageBlock

        initial_blocks = StorageBlock.objects.count()

        try:
            with self.fs.transaction:
                self.fs.pipe("/tx/block_test.txt", b"some data here")
                raise ValueError("Rollback")
        except ValueError:
            pass

        assert StorageBlock.objects.count() == initial_blocks

    def test_transaction_partial_rollback(self):
        """Data written before the transaction should survive the rollback."""
        self.fs.pipe("/tx/before.txt", b"before transaction")

        try:
            with self.fs.transaction:
                self.fs.pipe("/tx/during.txt", b"during transaction")
                raise ValueError("Rollback")
        except ValueError:
            pass

        assert self.fs.cat("/tx/before.txt") == b"before transaction"
        assert not self.fs.exists("/tx/during.txt")

    def test_transaction_multiple_operations(self):
        """Mix of write, overwrite, delete within a transaction."""
        self.fs.pipe("/tx/existing.txt", b"original")

        with self.fs.transaction:
            self.fs.pipe("/tx/new.txt", b"new file")
            self.fs.pipe("/tx/existing.txt", b"overwritten")
            self.fs.rm("/tx/new.txt")

        assert self.fs.cat("/tx/existing.txt") == b"overwritten"
        assert not self.fs.exists("/tx/new.txt")

    def test_transaction_type(self):
        """DjangoFileSystem should use DjangoTransaction."""
        from django_fsspec.fs import DjangoTransaction

        assert self.fs.transaction_type is DjangoTransaction

    def test_no_transaction_autocommit(self):
        """Without transaction, each operation commits immediately."""
        self.fs.pipe("/tx/auto.txt", b"auto")
        assert self.fs.exists("/tx/auto.txt")

    def test_nested_transaction_raises(self):
        """Nested transactions should raise RuntimeError."""
        with self.fs.transaction:
            with pytest.raises(RuntimeError, match="Nested"):
                with self.fs.transaction:
                    pass
