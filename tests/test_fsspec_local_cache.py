import tempfile
from pathlib import Path

import fsspec
import pytest
from django.test import TestCase
from fsspec.exceptions import BlocksizeMismatchError

from django_fsspec.fs import DjangoFileSystem
from django_fsspec.models import FileBlock, FileNode, StorageBlock
from django_fsspec.operations import read_file, write_file


def _reset_storage():
    FileBlock.objects.all().delete()
    StorageBlock.objects.all().delete()
    FileNode.objects.all().delete()


def _cached_fs(protocol, cache_dir, **kwargs):
    target = fsspec.filesystem(
        "django",
        namespace_id=1,
        skip_instance_cache=True,
    )
    return fsspec.filesystem(
        protocol,
        fs=target,
        cache_storage=str(cache_dir),
        skip_instance_cache=True,
        **kwargs,
    )


class TestFsspecLocalCacheIntegration(TestCase):
    def test_root_mkdir_calls_are_noops_for_fsspec_put_compatibility(self):
        fs = DjangoFileSystem(namespace_id=1)

        fs.mkdir("/")
        fs.makedirs("")

        assert fs.exists("/")

    def test_filecache_reads_cached_copy_until_cache_is_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            fs = _cached_fs("filecache", cache_dir)

            write_file(1, "/source.txt", b"version 1")
            assert fs.cat("/source.txt") == b"version 1"

            write_file(1, "/source.txt", b"version 2")
            assert fs.cat("/source.txt") == b"version 1"

            checked = _cached_fs("filecache", cache_dir, check_files=True)
            assert checked.cat("/source.txt") == b"version 2"

            write_file(1, "/source.txt", b"version 3")
            checked.clear_cache()
            assert checked.cat("/source.txt") == b"version 3"

    def test_simplecache_has_no_metadata_and_writes_back_to_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            fs = _cached_fs("simplecache", cache_dir)

            write_file(1, "/source.txt", b"version 1")
            assert fs.cat("/source.txt") == b"version 1"

            write_file(1, "/source.txt", b"version 2")
            assert fs.cat("/source.txt") == b"version 1"
            assert not (cache_dir / "cache").exists()

            with fs.open("/written.txt", "wb") as f:
                f.write(b"root write")
            with fs.open("/dir/written.txt", "wb") as f:
                f.write(b"nested write")

            assert read_file(1, "/written.txt") == b"root write"
            assert read_file(1, "/dir/written.txt") == b"nested write"

    def test_block_cache_protocols_support_seek_reads(self):
        for protocol in ["blockcache", "cached"]:
            with self.subTest(protocol=protocol):
                _reset_storage()
                with tempfile.TemporaryDirectory() as tmp:
                    cache_dir = Path(tmp)
                    fs = _cached_fs(protocol, cache_dir)
                    data = b"0123456789abcdef"

                    write_file(1, "/source.bin", data)

                    with fs.open("/source.bin", "rb", block_size=4) as f:
                        f.seek(6)
                        assert f.read(4) == b"6789"

                    with pytest.raises(BlocksizeMismatchError):
                        with fs.open("/source.bin", "rb", block_size=8) as f:
                            f.read(1)

    def test_block_cache_protocols_can_clear_remote_updates(self):
        for protocol in ["blockcache", "cached"]:
            with self.subTest(protocol=protocol):
                _reset_storage()
                with tempfile.TemporaryDirectory() as tmp:
                    cache_dir = Path(tmp)
                    fs = _cached_fs(protocol, cache_dir)

                    write_file(1, "/source.bin", b"version 1")
                    with fs.open("/source.bin", "rb", block_size=4) as f:
                        assert f.read(4) == b"vers"

                    write_file(1, "/source.bin", b"version 2")

                    fs.clear_cache()
                    with fs.open("/source.bin", "rb", block_size=4) as f:
                        assert f.read() == b"version 2"

    def tearDown(self):
        _reset_storage()
