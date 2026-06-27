#!/usr/bin/env python
"""End-to-end tests for django-fsspec against real databases.

Validates all core operations work correctly on each database backend.
Run after unit tests to catch database-specific issues.

Usage:
    DJANGO_FSSPEC_BENCH_DB=mysql python benchmarks/e2e_test.py
"""

import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "benchmarks.settings")

import django

django.setup()

from django.core.management import call_command
from django.test.utils import override_settings

from django_fsspec.exceptions import FileConflictError, FileTooLargeError, PathValidationError
from django_fsspec.fs import DjangoFileSystem
from django_fsspec.models import FileBlock, FileNode, StorageBlock
from django_fsspec.operations import (
    read_file,
    read_file_range,
    write_file,
    delete_file,
    list_directory,
    copy_file,
    move_file,
    file_exists,
    get_file_info,
    create_file_exclusive,
    append_file,
)


class E2ETestRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def run(self, name, func):
        print(f"  {name} ...", end=" ", flush=True)
        try:
            func()
            self.passed += 1
            print("OK")
        except Exception as e:
            self.failed += 1
            self.errors.append((name, e))
            print(f"FAIL: {e}")

    def reset_db(self):
        FileBlock.objects.all().delete()
        StorageBlock.objects.all().delete()
        FileNode.objects.all().delete()

    def report(self):
        total = self.passed + self.failed
        print(f"\n  {self.passed}/{total} passed", end="")
        if self.failed:
            print(f", {self.failed} FAILED:")
            for name, err in self.errors:
                print(f"    - {name}: {err}")
        else:
            print()
        return self.failed == 0


def run_e2e(db_name):
    print(f"\n{'=' * 60}")
    print(f"  E2E Tests: {db_name}")
    print(f"{'=' * 60}")

    call_command("migrate", verbosity=0)

    runner = E2ETestRunner()
    fs = DjangoFileSystem(namespace=0)

    # --- Write & Read ---
    def test_write_read():
        runner.reset_db()
        write_file(0, "/test.txt", b"hello world")
        assert read_file(0, "/test.txt") == b"hello world"

    runner.run("write_read", test_write_read)

    def test_write_empty():
        runner.reset_db()
        write_file(0, "/empty.txt", b"")
        assert read_file(0, "/empty.txt") == b""

    runner.run("write_empty", test_write_empty)

    def test_write_overwrite():
        runner.reset_db()
        write_file(0, "/file.txt", b"v1")
        write_file(0, "/file.txt", b"v2")
        assert read_file(0, "/file.txt") == b"v2"
        node = FileNode.objects.get(path="/file.txt")
        assert node.version == 2

    runner.run("write_overwrite", test_write_overwrite)

    def test_write_multi_block():
        runner.reset_db()
        data = b"A" * (256 * 1024 + 100)
        write_file(0, "/big.bin", data)
        assert read_file(0, "/big.bin") == data
        node = FileNode.objects.get(path="/big.bin")
        assert FileBlock.objects.filter(file=node).count() == 2

    runner.run("write_multi_block", test_write_multi_block)

    # --- Exclusive Create ---
    def test_exclusive_create():
        runner.reset_db()
        create_file_exclusive(0, "/new.txt", b"data")
        assert read_file(0, "/new.txt") == b"data"
        try:
            create_file_exclusive(0, "/new.txt", b"other")
            assert False, "Should have raised FileExistsError"
        except FileExistsError:
            pass

    runner.run("exclusive_create", test_exclusive_create)

    # --- Append ---
    def test_append():
        runner.reset_db()
        write_file(0, "/log.txt", b"line1\n")
        append_file(0, "/log.txt", b"line2\n")
        assert read_file(0, "/log.txt") == b"line1\nline2\n"

    runner.run("append", test_append)

    # --- Range Read ---
    def test_range_read():
        runner.reset_db()
        write_file(0, "/data.txt", b"ABCDEFGHIJ")
        assert read_file_range(0, "/data.txt", 3, 7) == b"DEFG"

    runner.run("range_read", test_range_read)

    def test_range_read_cross_block():
        runner.reset_db()
        bs = 256 * 1024
        data = b"X" * bs + b"Y" * bs
        write_file(0, "/cross.bin", data)
        result = read_file_range(0, "/cross.bin", bs - 5, bs + 5)
        assert result == b"X" * 5 + b"Y" * 5

    runner.run("range_read_cross_block", test_range_read_cross_block)

    # --- Verify Checksum ---
    def test_verify_checksum():
        runner.reset_db()
        write_file(0, "/check.txt", b"verify me")
        data = read_file(0, "/check.txt", verify_checksum=True)
        assert data == b"verify me"

    runner.run("verify_checksum", test_verify_checksum)

    # --- Directory Operations ---
    def test_list_directory():
        runner.reset_db()
        write_file(0, "/a.txt", b"a")
        write_file(0, "/dir/b.txt", b"b")
        write_file(0, "/dir/sub/c.txt", b"c")
        result = list_directory(0, "/")
        assert sorted(result) == ["a.txt", "dir"]
        result = list_directory(0, "/dir")
        assert sorted(result) == ["b.txt", "sub"]

    runner.run("list_directory", test_list_directory)

    def test_exists():
        runner.reset_db()
        write_file(0, "/dir/file.txt", b"data")
        assert file_exists(0, "/dir/file.txt")
        assert file_exists(0, "/dir")
        assert not file_exists(0, "/nonexistent")

    runner.run("exists", test_exists)

    def test_info():
        runner.reset_db()
        write_file(0, "/info.txt", b"hello")
        info = get_file_info(0, "/info.txt")
        assert info["type"] == "file"
        assert info["size"] == 5

    runner.run("info", test_info)

    # --- Delete ---
    def test_delete():
        runner.reset_db()
        write_file(0, "/del.txt", b"data")
        delete_file(0, "/del.txt")
        assert not file_exists(0, "/del.txt")
        assert StorageBlock.objects.filter(is_free=True).count() == 1

    runner.run("delete", test_delete)

    def test_delete_recursive():
        runner.reset_db()
        write_file(0, "/dir/a.txt", b"a")
        write_file(0, "/dir/b.txt", b"b")
        delete_file(0, "/dir", recursive=True)
        assert not file_exists(0, "/dir")

    runner.run("delete_recursive", test_delete_recursive)

    # --- Copy & Move ---
    def test_copy():
        runner.reset_db()
        write_file(0, "/src.txt", b"copy me")
        copy_file(0, "/src.txt", "/dst.txt")
        assert read_file(0, "/dst.txt") == b"copy me"
        assert read_file(0, "/src.txt") == b"copy me"

    runner.run("copy", test_copy)

    def test_move():
        runner.reset_db()
        write_file(0, "/old.txt", b"move me")
        move_file(0, "/old.txt", "/new.txt")
        assert read_file(0, "/new.txt") == b"move me"
        assert not file_exists(0, "/old.txt")

    runner.run("move", test_move)

    # --- Namespace Isolation ---
    def test_namespace_isolation():
        runner.reset_db()
        write_file(0, "/ns.txt", b"ns0")
        write_file(1, "/ns.txt", b"ns1")
        assert read_file(0, "/ns.txt") == b"ns0"
        assert read_file(1, "/ns.txt") == b"ns1"

    runner.run("namespace_isolation", test_namespace_isolation)

    # --- Path Validation ---
    def test_path_validation():
        runner.reset_db()
        try:
            write_file(0, "../etc/passwd", b"bad")
            assert False, "Should have raised PathValidationError"
        except PathValidationError:
            pass

    runner.run("path_validation", test_path_validation)

    # --- Block Pool Reuse ---
    def test_block_reuse():
        runner.reset_db()
        write_file(0, "/reuse.txt", b"data")
        delete_file(0, "/reuse.txt")
        free_before = StorageBlock.objects.filter(is_free=True).count()
        assert free_before == 1
        write_file(0, "/reuse2.txt", b"new data")
        free_after = StorageBlock.objects.filter(is_free=True).count()
        assert free_after == 0

    runner.run("block_reuse", test_block_reuse)

    # --- fsspec Interface ---
    def test_fsspec_roundtrip():
        runner.reset_db()
        fs.pipe("/fsspec.txt", b"fsspec data")
        assert fs.cat("/fsspec.txt") == b"fsspec data"
        assert fs.exists("/fsspec.txt")
        info = fs.info("/fsspec.txt")
        assert info["type"] == "file"
        result = fs.ls("/", detail=False)
        assert "/fsspec.txt" in result
        fs.rm("/fsspec.txt")
        assert not fs.exists("/fsspec.txt")

    runner.run("fsspec_roundtrip", test_fsspec_roundtrip)

    def test_fsspec_seek():
        runner.reset_db()
        fs.pipe("/seek.txt", b"hello world")
        with fs.open("/seek.txt", "rb") as f:
            f.seek(6)
            assert f.read(5) == b"world"

    runner.run("fsspec_seek", test_fsspec_seek)

    # --- Unicode ---
    def test_unicode_path():
        runner.reset_db()
        write_file(0, "/日本語/ファイル.txt", b"unicode content")
        assert read_file(0, "/日本語/ファイル.txt") == b"unicode content"

    runner.run("unicode_path", test_unicode_path)

    # --- Block Size Coexistence ---
    def test_block_size_coexistence():
        runner.reset_db()
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(0, "/small_bs.txt", b"A" * 250)
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=256 * 1024):
            write_file(0, "/large_bs.txt", b"B" * 250)
        assert read_file(0, "/small_bs.txt") == b"A" * 250
        assert read_file(0, "/large_bs.txt") == b"B" * 250
        small = FileNode.objects.get(path="/small_bs.txt")
        large = FileNode.objects.get(path="/large_bs.txt")
        assert small.block_size == 100
        assert large.block_size == 256 * 1024

    runner.run("block_size_coexistence", test_block_size_coexistence)

    runner.reset_db()
    return runner.report()


def main():
    db = os.environ.get("DJANGO_FSSPEC_BENCH_DB", "sqlite")
    success = run_e2e(db)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
