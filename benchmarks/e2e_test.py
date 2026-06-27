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

    # --- Concurrency Tests (skipped on SQLite — no concurrent write support) ---
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    skip_concurrency = db_name == "sqlite"
    if skip_concurrency:
        print("  (skipping concurrency tests — SQLite does not support concurrent writes)")

    def test_concurrent_write_different_files():
        """Multiple threads writing to different files simultaneously."""
        runner.reset_db()
        n_threads = 8
        n_files_per_thread = 20
        errors = []

        def writer(thread_id):
            try:
                for i in range(n_files_per_thread):
                    write_file(0, f"/conc/t{thread_id}/file{i}.txt", f"thread {thread_id} file {i}".encode())
            except Exception as e:
                errors.append((thread_id, e))

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(writer, t) for t in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Errors: {errors}"

        # Verify all files exist and have correct content
        for t in range(n_threads):
            for i in range(n_files_per_thread):
                data = read_file(0, f"/conc/t{t}/file{i}.txt")
                assert data == f"thread {t} file {i}".encode(), f"Data mismatch: t={t} i={i}"

        total = FileNode.objects.filter(namespace=0, path__startswith="/conc/").count()
        assert total == n_threads * n_files_per_thread

    if not skip_concurrency:
        runner.run("concurrent_write_different_files", test_concurrent_write_different_files)

    def test_concurrent_write_same_file():
        """Multiple threads writing to the same file — one wins, others may get FileConflictError."""
        runner.reset_db()
        write_file(0, "/conc_same.txt", b"initial")

        n_threads = 8
        results = {"success": 0, "conflict": 0, "other_error": 0}
        lock = threading.Lock()

        def writer(thread_id):
            try:
                write_file(0, "/conc_same.txt", f"written by thread {thread_id}".encode())
                with lock:
                    results["success"] += 1
            except FileConflictError:
                with lock:
                    results["conflict"] += 1
            except Exception:
                with lock:
                    results["other_error"] += 1

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(writer, t) for t in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        # At least one should succeed
        assert results["success"] >= 1, f"No successful writes: {results}"
        assert results["other_error"] == 0, f"Unexpected errors: {results}"
        # File should exist with valid content
        data = read_file(0, "/conc_same.txt")
        assert data.startswith(b"written by thread ")

    if not skip_concurrency:
        runner.run("concurrent_write_same_file", test_concurrent_write_same_file)

    def test_concurrent_read_while_write():
        """Readers should get consistent data while writers update different files."""
        runner.reset_db()
        # Pre-populate files for reading
        for i in range(20):
            write_file(0, f"/conc_rw/read{i}.txt", f"read data {i}".encode())

        errors = []
        writer_id_counter = [0]
        counter_lock = threading.Lock()

        def reader():
            try:
                for i in range(20):
                    data = read_file(0, f"/conc_rw/read{i}.txt")
                    assert data == f"read data {i}".encode()
            except Exception as e:
                errors.append(("reader", e))

        def writer():
            with counter_lock:
                wid = writer_id_counter[0]
                writer_id_counter[0] += 1
            try:
                for i in range(20):
                    write_file(0, f"/conc_rw/w{wid}/file{i}.txt", f"write data {wid}-{i}".encode())
            except Exception as e:
                errors.append(("writer", e))

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = []
            # 3 readers + 3 writers (each writing to unique paths)
            for _ in range(3):
                futures.append(pool.submit(reader))
                futures.append(pool.submit(writer))
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Errors: {errors}"

    if not skip_concurrency:
        runner.run("concurrent_read_while_write", test_concurrent_read_while_write)

    def test_concurrent_delete_and_list():
        """Delete and list operations running concurrently."""
        runner.reset_db()
        for i in range(50):
            write_file(0, f"/conc_dl/file{i}.txt", b"data")

        errors = []

        def deleter():
            try:
                for i in range(50):
                    try:
                        delete_file(0, f"/conc_dl/file{i}.txt")
                    except FileNotFoundError:
                        pass  # Already deleted by another thread or race
            except Exception as e:
                errors.append(("deleter", e))

        def lister():
            try:
                for _ in range(10):
                    list_directory(0, "/conc_dl")
            except Exception as e:
                errors.append(("lister", e))

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(deleter), pool.submit(deleter),
                       pool.submit(lister), pool.submit(lister)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Errors: {errors}"

    if not skip_concurrency:
        runner.run("concurrent_delete_and_list", test_concurrent_delete_and_list)

    runner.reset_db()
    return runner.report()


def main():
    db = os.environ.get("DJANGO_FSSPEC_BENCH_DB", "sqlite")
    success = run_e2e(db)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
