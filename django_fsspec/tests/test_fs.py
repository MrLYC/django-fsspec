import pytest
from django.test import TestCase

from django_fsspec.buffer import DjangoFile
from django_fsspec.fs import DjangoFileSystem
from django_fsspec.operations import write_file


class TestDjangoFileSystem(TestCase):
    def setUp(self):
        self.fs = DjangoFileSystem(namespace=0)

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
        write_file(0, "/a.txt", b"a")
        write_file(0, "/b.txt", b"b")
        result = self.fs.ls("/", detail=False)
        assert sorted(result) == ["/a.txt", "/b.txt"]

    def test_ls_detail(self):
        write_file(0, "/test.txt", b"hello")
        result = self.fs.ls("/", detail=True)
        assert len(result) == 1
        assert result[0]["name"] == "/test.txt"
        assert result[0]["size"] == 5
        assert result[0]["type"] == "file"

    def test_ls_subdirectory(self):
        write_file(0, "/dir/file.txt", b"data")
        result = self.fs.ls("/dir", detail=False)
        assert result == ["/dir/file.txt"]

    def test_ls_with_implicit_dirs(self):
        write_file(0, "/a.txt", b"a")
        write_file(0, "/sub/b.txt", b"b")
        result = self.fs.ls("/", detail=False)
        assert sorted(result) == ["/a.txt", "/sub"]

    def test_ls_file_directly(self):
        write_file(0, "/test.txt", b"data")
        result = self.fs.ls("/test.txt", detail=False)
        assert result == ["/test.txt"]

    def test_ls_file_directly_detail(self):
        write_file(0, "/test.txt", b"data")
        result = self.fs.ls("/test.txt", detail=True)
        assert len(result) == 1
        assert result[0]["name"] == "/test.txt"
        assert result[0]["type"] == "file"

    def test_ls_not_found(self):
        with pytest.raises(FileNotFoundError):
            self.fs.ls("/nonexistent")

    def test_info_file(self):
        write_file(0, "/test.txt", b"hello")
        info = self.fs.info("/test.txt")
        assert info["type"] == "file"
        assert info["size"] == 5

    def test_info_directory(self):
        write_file(0, "/dir/file.txt", b"data")
        info = self.fs.info("/dir")
        assert info["type"] == "directory"

    def test_info_root(self):
        info = self.fs.info("/")
        assert info["type"] == "directory"

    def test_exists(self):
        write_file(0, "/test.txt", b"data")
        assert self.fs.exists("/test.txt")
        assert not self.fs.exists("/nonexistent.txt")

    def test_exists_root(self):
        assert self.fs.exists("/")

    def test_exists_implicit_dir(self):
        write_file(0, "/dir/file.txt", b"data")
        assert self.fs.exists("/dir")

    def test_rm(self):
        write_file(0, "/test.txt", b"data")
        self.fs.rm("/test.txt")
        assert not self.fs.exists("/test.txt")

    def test_rm_recursive(self):
        write_file(0, "/dir/a.txt", b"a")
        write_file(0, "/dir/b.txt", b"b")
        self.fs.rm("/dir", recursive=True)
        assert not self.fs.exists("/dir")

    def test_rm_directory_not_recursive(self):
        write_file(0, "/dir/file.txt", b"data")
        with pytest.raises(IsADirectoryError):
            self.fs.rm("/dir")

    def test_cp_file(self):
        write_file(0, "/src.txt", b"data")
        self.fs.cp_file("/src.txt", "/dst.txt")
        assert self.fs.cat("/dst.txt") == b"data"
        assert self.fs.exists("/src.txt")

    def test_mv(self):
        write_file(0, "/src.txt", b"data")
        self.fs.mv("/src.txt", "/dst.txt")
        assert self.fs.cat("/dst.txt") == b"data"
        assert not self.fs.exists("/src.txt")

    def test_mkdir_noop(self):
        self.fs.mkdir("/somedir")  # should not raise

    def test_makedirs_noop(self):
        self.fs.makedirs("/a/b/c")  # should not raise

    def test_rmdir_noop(self):
        self.fs.rmdir("/somedir")  # should not raise

    def test_created(self):
        write_file(0, "/test.txt", b"data")
        created = self.fs.created("/test.txt")
        assert created is not None

    def test_modified(self):
        write_file(0, "/test.txt", b"data")
        modified = self.fs.modified("/test.txt")
        assert modified is not None

    def test_namespace_isolation(self):
        fs0 = DjangoFileSystem(namespace=0)
        fs1 = DjangoFileSystem(namespace=1)

        with fs0.open("/test.txt", "wb") as f:
            f.write(b"ns0")
        with fs1.open("/test.txt", "wb") as f:
            f.write(b"ns1")

        assert fs0.cat("/test.txt") == b"ns0"
        assert fs1.cat("/test.txt") == b"ns1"

    def test_seek_and_read(self):
        write_file(0, "/test.txt", b"hello world")
        with self.fs.open("/test.txt", "rb") as f:
            f.seek(6)
            assert f.read(5) == b"world"

    def test_read_partial(self):
        write_file(0, "/test.txt", b"hello world")
        with self.fs.open("/test.txt", "rb") as f:
            assert f.read(5) == b"hello"

    def test_ls_detail_implicit_dir(self):
        write_file(0, "/dir/sub/file.txt", b"data")
        result = self.fs.ls("/dir", detail=True)
        assert len(result) == 1
        assert result[0]["type"] == "directory"
        assert result[0]["name"] == "/dir/sub"


class TestDjangoFileSystemFsspec(TestCase):
    """Test fsspec.filesystem() registration."""

    def test_fsspec_filesystem(self):
        import fsspec

        fs = fsspec.filesystem("django", namespace=0)
        assert isinstance(fs, DjangoFileSystem)

    def test_fsspec_roundtrip(self):
        import fsspec

        fs = fsspec.filesystem("django", namespace=0)
        fs.pipe("/roundtrip.txt", b"fsspec data")
        assert fs.cat("/roundtrip.txt") == b"fsspec data"
        fs.rm("/roundtrip.txt")
