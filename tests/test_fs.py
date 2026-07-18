import pytest
from django.test import TestCase

from django_fsspec.buffer import DjangoFile
from django_fsspec.exceptions import NamespaceNotFoundError
from django_fsspec.fs import DjangoFileSystem
from django_fsspec.models import Namespace
from django_fsspec.operations import append_file, copy_file, move_file, write_file


class TestDjangoFileSystem(TestCase):
    def setUp(self):
        self.fs = DjangoFileSystem(namespace_id=1)

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

    def test_append_two_open_handles_preserves_both_appends(self):
        with self.fs.open("/log.txt", "wb") as f:
            f.write(b"start\n")

        first = self.fs.open("/log.txt", "ab")
        second = self.fs.open("/log.txt", "ab")
        try:
            first.write(b"first\n")
            second.write(b"second\n")
        finally:
            first.close()
            second.close()

        assert self.fs.cat("/log.txt") == b"start\nfirst\nsecond\n"

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

    def test_read_with_supplied_size_kwarg(self):
        """Cache wrappers may pass a known size to _open(); ensure it is accepted."""
        write_file(1, "/sized-read.txt", b"hello")
        f = DjangoFile(self.fs, "/sized-read.txt", mode="rb", size=5)
        assert f.size == 5
        f.close()

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

    def test_copy_file_into_existing_directory_uses_basename(self):
        self.fs.mkdir("/dst")
        self.fs.pipe("/src.txt", b"data")

        self.fs.copy("/src.txt", "/dst/")

        assert self.fs.cat("/dst/src.txt") == b"data"
        assert self.fs.cat("/src.txt") == b"data"

    def test_copy_recursive_directory_preserves_relative_paths(self):
        self.fs.mkdir("/src/empty", create_parents=True)
        self.fs.pipe("/src/a.txt", b"a")
        self.fs.pipe("/src/sub/b.txt", b"b")

        self.fs.copy("/src", "/dst", recursive=True)

        assert self.fs.cat("/dst/a.txt") == b"a"
        assert self.fs.cat("/dst/sub/b.txt") == b"b"
        assert self.fs.isdir("/dst/empty")
        assert self.fs.exists("/src/a.txt")

    def test_copy_recursive_empty_directory_into_existing_directory(self):
        self.fs.mkdir("/src_empty")
        self.fs.mkdir("/dst")

        self.fs.copy("/src_empty", "/dst/", recursive=True)

        assert self.fs.isdir("/dst/src_empty")
        assert self.fs.isdir("/src_empty")

    def test_copy_recursive_missing_source_ignored(self):
        self.fs.copy("/missing", "/dst", recursive=True, on_error="ignore")

        assert not self.fs.exists("/dst")

    def test_copy_recursive_missing_source_raises(self):
        with pytest.raises(FileNotFoundError):
            self.fs.copy("/missing", "/dst", recursive=True)

    def test_mv(self):
        write_file(1, "/src.txt", b"data")
        self.fs.mv("/src.txt", "/dst.txt")
        assert self.fs.cat("/dst.txt") == b"data"
        assert not self.fs.exists("/src.txt")

    def test_mv_file_into_existing_directory_uses_basename(self):
        self.fs.mkdir("/dst")
        self.fs.pipe("/src.txt", b"data")

        self.fs.mv("/src.txt", "/dst/")

        assert self.fs.cat("/dst/src.txt") == b"data"
        assert not self.fs.exists("/src.txt")

    def test_mv_recursive_directory_preserves_tree(self):
        self.fs.mkdir("/src/empty", create_parents=True)
        self.fs.pipe("/src/a.txt", b"a")
        self.fs.pipe("/src/sub/b.txt", b"b")

        self.fs.mv("/src", "/dst", recursive=True)

        assert self.fs.cat("/dst/a.txt") == b"a"
        assert self.fs.cat("/dst/sub/b.txt") == b"b"
        assert self.fs.isdir("/dst/empty")
        assert not self.fs.exists("/src")

    def test_mkdir_creates_directory(self):
        self.fs.mkdir("/somedir")
        assert self.fs.exists("/somedir")
        assert self.fs.info("/somedir")["type"] == "directory"
        assert self.fs.ls("/", detail=False) == ["/somedir"]

    def test_empty_directory_fsspec_views_after_mkdir(self):
        self.fs.mkdir("/empty")

        assert self.fs.ls("/empty", detail=False) == []
        assert self.fs.ls("/empty", detail=True) == []
        assert self.fs.find("/empty") == []
        assert list(self.fs.walk("/empty")) == [("/empty", [], [])]
        assert self.fs.glob("/empty/*") == []
        assert "empty" in self.fs.tree("/empty")

    def test_mixed_explicit_and_implicit_directory_journey(self):
        self.fs.mkdir("/journey")
        self.fs.mkdir("/journey/empty")
        self.fs.pipe("/journey/file.txt", b"root")
        self.fs.pipe("/journey/implicit/deep.txt", b"deep")
        self.fs.pipe("/journey/empty/child.txt", b"child")
        self.fs.pipe("/journey/empty/child.txt", b"updated child")
        self.fs.mv("/journey/file.txt", "/journey/implicit/moved.txt")
        self.fs.rm("/journey/empty/child.txt")

        assert self.fs.exists("/journey")
        assert self.fs.isdir("/journey")
        assert self.fs.isdir("/journey/empty")
        assert self.fs.isdir("/journey/implicit")
        assert not self.fs.exists("/journey/file.txt")
        assert self.fs.cat("/journey/implicit/moved.txt") == b"root"
        assert self.fs.ls("/journey/empty", detail=False) == []

        assert sorted(self.fs.ls("/journey", detail=False)) == [
            "/journey/empty",
            "/journey/implicit",
        ]
        assert sorted(self.fs.find("/journey")) == [
            "/journey/implicit/deep.txt",
            "/journey/implicit/moved.txt",
        ]
        assert sorted(self.fs.find("/journey", withdirs=True)) == [
            "/journey/empty",
            "/journey/implicit",
            "/journey/implicit/deep.txt",
            "/journey/implicit/moved.txt",
        ]
        tree = self.fs.tree("/journey")
        assert "empty" in tree
        assert "deep.txt" in tree
        assert "moved.txt" in tree

    def test_rejects_file_directory_path_conflicts(self):
        self.fs.pipe("/conflict", b"flat file")

        with pytest.raises(NotADirectoryError):
            self.fs.pipe("/conflict/child.txt", b"child")

        assert self.fs.cat("/conflict") == b"flat file"
        assert not self.fs.exists("/conflict/child.txt")

        self.fs.pipe("/reports/2026/q1.csv", b"q1")

        with pytest.raises(IsADirectoryError):
            self.fs.pipe("/reports/2026", b"not a directory")

        assert self.fs.cat("/reports/2026/q1.csv") == b"q1"
        assert self.fs.isdir("/reports/2026")

    def test_mv_same_path_is_noop(self):
        self.fs.pipe("/same.txt", b"data")

        self.fs.mv("/same.txt", "/same.txt")

        assert self.fs.cat("/same.txt") == b"data"

    def test_mv_overwrite_existing_file_when_requested(self):
        self.fs.pipe("/src.txt", b"src")
        self.fs.pipe("/dst.txt", b"dst")

        self.fs.mv("/src.txt", "/dst.txt", overwrite=True)

        assert self.fs.cat("/dst.txt") == b"src"
        assert not self.fs.exists("/src.txt")

    def test_fsspec_and_operations_interop_journey(self):
        self.fs.pipe("/interop/raw/input.txt", b"header\n")
        append_file(1, "/interop/raw/input.txt", b"body\n")
        copy_file(1, "/interop/raw/input.txt", "/interop/archive/input.txt")
        self.fs.mv("/interop/archive/input.txt", "/interop/archive/final.txt")
        move_file(1, "/interop/raw/input.txt", "/interop/raw/source.txt")
        self.fs.rm("/interop/raw/source.txt")

        assert self.fs.cat("/interop/archive/final.txt") == b"header\nbody\n"
        assert not self.fs.exists("/interop/raw/source.txt")
        assert sorted(self.fs.find("/interop")) == ["/interop/archive/final.txt"]

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
        fs0 = DjangoFileSystem(namespace_id=1)
        fs1 = DjangoFileSystem(namespace_id=2)

        with fs0.open("/test.txt", "wb") as f:
            f.write(b"ns0")
        with fs1.open("/test.txt", "wb") as f:
            f.write(b"ns1")

        assert fs0.cat("/test.txt") == b"ns0"
        assert fs1.cat("/test.txt") == b"ns1"

    def test_namespace_id_selects_namespace(self):
        Namespace.objects.create(id=2, name="other")
        fs = DjangoFileSystem(namespace_id=2)

        with fs.open("/test.txt", "wb") as f:
            f.write(b"ns2")

        assert fs.cat("/test.txt") == b"ns2"
        assert self.fs.exists("/test.txt") is False

    def test_namespace_argument_is_rejected(self):
        with pytest.raises(TypeError, match="Use namespace_id"):
            DjangoFileSystem(namespace=1)

    def test_write_missing_namespace_raises_clear_error(self):
        fs = DjangoFileSystem(namespace_id=0)

        with pytest.raises(NamespaceNotFoundError, match="Namespace not found: 0"):
            with fs.open("/test.txt", "wb") as f:
                f.write(b"data")

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
        self.fs = DjangoFileSystem(namespace_id=1)

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

    def test_find_file_path_returns_that_file(self):
        write_file(1, "/find_file.txt", b"data")

        assert self.fs.find("/find_file.txt") == ["/find_file.txt"]
        assert self.fs.find("/find_file.txt", detail=True)["/find_file.txt"][
            "size"
        ] == 4

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

    def test_find_withdirs_maxdepth_includes_immediate_implicit_dirs(self):
        write_file(1, "/finddepth/sub/file.txt", b"data")

        result = self.fs.find("/finddepth", maxdepth=1, withdirs=True)

        assert result == ["/finddepth/sub"]

    def test_find_missing_path_returns_empty_list(self):
        assert self.fs.find("/missing") == []

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

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import fsspec

        fsspec.register_implementation("django", DjangoFileSystem, clobber=True)

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

    def test_fsspec_namespace_argument_is_rejected(self):
        import fsspec

        with pytest.raises(TypeError, match="Use namespace_id"):
            fsspec.filesystem("django", namespace=1, skip_instance_cache=True)


class TestDjangoTransaction(TestCase):
    """Test fsspec transaction backed by Django database transaction."""

    def setUp(self):
        self.fs = DjangoFileSystem(namespace_id=1)

    def test_transaction_commit(self):
        """Files written in a transaction should be visible after commit."""
        with self.fs.transaction:
            self.fs.pipe("/tx/a.txt", b"aaa")
            self.fs.pipe("/tx/b.txt", b"bbb")

        assert self.fs.cat("/tx/a.txt") == b"aaa"
        assert self.fs.cat("/tx/b.txt") == b"bbb"

    def test_transaction_open_handle_commits_on_exit(self):
        """Unclosed write handles should be finalized before transaction commit."""
        with self.fs.transaction:
            f = self.fs.open("/tx/open_commit.txt", "wb")
            f.write(b"committed")

        assert f.closed
        assert self.fs.cat("/tx/open_commit.txt") == b"committed"

    def test_transaction_commit_failure_rolls_back(self):
        """A deferred file commit failure should roll back earlier writes."""

        class FailingFile:
            def commit(self):
                raise RuntimeError("commit failed")

            def discard(self):
                pass

        with pytest.raises(RuntimeError, match="commit failed"):
            with self.fs.transaction as tx:
                self.fs.pipe("/tx/before_failure.txt", b"data")
                tx.files.append(FailingFile())

        assert not self.fs.exists("/tx/before_failure.txt")

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

    def test_transaction_unclosed_open_handle_discarded_on_rollback(self):
        """Late close after rollback must not write outside the transaction."""
        f = None
        with pytest.raises(ValueError, match="Intentional rollback"):
            with self.fs.transaction:
                f = self.fs.open("/tx/open_rollback.txt", "wb")
                f.write(b"must not persist")
                raise ValueError("Intentional rollback")

        f.close()
        assert not self.fs.exists("/tx/open_rollback.txt")

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
