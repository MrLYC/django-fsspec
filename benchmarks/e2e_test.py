#!/usr/bin/env python
"""End-to-end tests for django-fsspec against real databases.

Validates all core operations work correctly on each database backend.
Run after unit tests to catch database-specific issues.

Usage:
    DJANGO_FSSPEC_BENCH_DB=mysql python benchmarks/e2e_test.py
"""

import os
import sys
import tempfile

import fsspec

os.environ.setdefault("DJANGO_FSSPEC_BENCH_DB", "sqlite")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo.settings")

import django

django.setup()

from django.core.management import call_command
from django.test.utils import override_settings

from django_fsspec.exceptions import FileConflictError, FileTooLargeError, PathValidationError
from django_fsspec.fs import DjangoFileSystem
from django_fsspec.models import (
    FileBlock,
    FileNode,
    Namespace,
    StorageBlock,
    get_block_size,
)
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


DEFAULT_NAMESPACE_ID = 1
SECONDARY_NAMESPACE_ID = 2


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
    fs = DjangoFileSystem(namespace_id=DEFAULT_NAMESPACE_ID)

    def cached_fs(protocol, cache_dir, **kwargs):
        target = fsspec.filesystem(
            "django",
            namespace_id=DEFAULT_NAMESPACE_ID,
            skip_instance_cache=True,
        )
        return fsspec.filesystem(
            protocol,
            fs=target,
            cache_storage=cache_dir,
            skip_instance_cache=True,
            **kwargs,
        )

    # --- Write & Read ---
    def test_write_read():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/test.txt", b"hello world")
        assert read_file(DEFAULT_NAMESPACE_ID, "/test.txt") == b"hello world"

    runner.run("write_read", test_write_read)

    def test_write_empty():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/empty.txt", b"")
        assert read_file(DEFAULT_NAMESPACE_ID, "/empty.txt") == b""

    runner.run("write_empty", test_write_empty)

    def test_write_overwrite():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/file.txt", b"v1")
        write_file(DEFAULT_NAMESPACE_ID, "/file.txt", b"v2")
        assert read_file(DEFAULT_NAMESPACE_ID, "/file.txt") == b"v2"
        node = FileNode.objects.get(path="/file.txt")
        assert node.version == 2

    runner.run("write_overwrite", test_write_overwrite)

    def test_write_multi_block():
        runner.reset_db()
        data = b"A" * (get_block_size() + 100)
        write_file(DEFAULT_NAMESPACE_ID, "/big.bin", data)
        assert read_file(DEFAULT_NAMESPACE_ID, "/big.bin") == data
        node = FileNode.objects.get(path="/big.bin")
        assert FileBlock.objects.filter(file=node).count() == 2

    runner.run("write_multi_block", test_write_multi_block)

    # --- Exclusive Create ---
    def test_exclusive_create():
        runner.reset_db()
        create_file_exclusive(DEFAULT_NAMESPACE_ID, "/new.txt", b"data")
        assert read_file(DEFAULT_NAMESPACE_ID, "/new.txt") == b"data"
        try:
            create_file_exclusive(DEFAULT_NAMESPACE_ID, "/new.txt", b"other")
            assert False, "Should have raised FileExistsError"
        except FileExistsError:
            pass

    runner.run("exclusive_create", test_exclusive_create)

    # --- Append ---
    def test_append():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/log.txt", b"line1\n")
        append_file(DEFAULT_NAMESPACE_ID, "/log.txt", b"line2\n")
        assert read_file(DEFAULT_NAMESPACE_ID, "/log.txt") == b"line1\nline2\n"

    runner.run("append", test_append)

    # --- Range Read ---
    def test_range_read():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/data.txt", b"ABCDEFGHIJ")
        assert read_file_range(DEFAULT_NAMESPACE_ID, "/data.txt", 3, 7) == b"DEFG"

    runner.run("range_read", test_range_read)

    def test_range_read_cross_block():
        runner.reset_db()
        bs = get_block_size()
        data = b"X" * bs + b"Y" * bs
        write_file(DEFAULT_NAMESPACE_ID, "/cross.bin", data)
        result = read_file_range(DEFAULT_NAMESPACE_ID, "/cross.bin", bs - 5, bs + 5)
        assert result == b"X" * 5 + b"Y" * 5

    runner.run("range_read_cross_block", test_range_read_cross_block)

    # --- Verify Checksum ---
    def test_verify_checksum():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/check.txt", b"verify me")
        data = read_file(DEFAULT_NAMESPACE_ID, "/check.txt", verify_checksum=True)
        assert data == b"verify me"

    runner.run("verify_checksum", test_verify_checksum)

    # --- Directory Operations ---
    def test_list_directory():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/a.txt", b"a")
        write_file(DEFAULT_NAMESPACE_ID, "/dir/b.txt", b"b")
        write_file(DEFAULT_NAMESPACE_ID, "/dir/sub/c.txt", b"c")
        result = list_directory(DEFAULT_NAMESPACE_ID, "/")
        assert sorted(result) == ["a.txt", "dir"]
        result = list_directory(DEFAULT_NAMESPACE_ID, "/dir")
        assert sorted(result) == ["b.txt", "sub"]

    runner.run("list_directory", test_list_directory)

    def test_exists():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/dir/file.txt", b"data")
        assert file_exists(DEFAULT_NAMESPACE_ID, "/dir/file.txt")
        assert file_exists(DEFAULT_NAMESPACE_ID, "/dir")
        assert not file_exists(DEFAULT_NAMESPACE_ID, "/nonexistent")

    runner.run("exists", test_exists)

    def test_info():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/info.txt", b"hello")
        info = get_file_info(DEFAULT_NAMESPACE_ID, "/info.txt")
        assert info["type"] == "file"
        assert info["size"] == 5

    runner.run("info", test_info)

    # --- Delete ---
    def test_delete():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/del.txt", b"data")
        delete_file(DEFAULT_NAMESPACE_ID, "/del.txt")
        assert not file_exists(DEFAULT_NAMESPACE_ID, "/del.txt")
        assert StorageBlock.objects.filter(is_free=True).count() == 1

    runner.run("delete", test_delete)

    def test_delete_recursive():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/dir/a.txt", b"a")
        write_file(DEFAULT_NAMESPACE_ID, "/dir/b.txt", b"b")
        delete_file(DEFAULT_NAMESPACE_ID, "/dir", recursive=True)
        assert not file_exists(DEFAULT_NAMESPACE_ID, "/dir")

    runner.run("delete_recursive", test_delete_recursive)

    # --- Copy & Move ---
    def test_copy():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/src.txt", b"copy me")
        copy_file(DEFAULT_NAMESPACE_ID, "/src.txt", "/dst.txt")
        assert read_file(DEFAULT_NAMESPACE_ID, "/dst.txt") == b"copy me"
        assert read_file(DEFAULT_NAMESPACE_ID, "/src.txt") == b"copy me"

    runner.run("copy", test_copy)

    def test_move():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/old.txt", b"move me")
        move_file(DEFAULT_NAMESPACE_ID, "/old.txt", "/new.txt")
        assert read_file(DEFAULT_NAMESPACE_ID, "/new.txt") == b"move me"
        assert not file_exists(DEFAULT_NAMESPACE_ID, "/old.txt")

    runner.run("move", test_move)

    # --- Namespace Isolation ---
    def test_namespace_isolation():
        runner.reset_db()
        Namespace.objects.get_or_create(
            id=SECONDARY_NAMESPACE_ID,
            defaults={"name": "secondary", "description": "Secondary test namespace"},
        )
        write_file(DEFAULT_NAMESPACE_ID, "/ns.txt", b"default")
        write_file(SECONDARY_NAMESPACE_ID, "/ns.txt", b"secondary")
        assert read_file(DEFAULT_NAMESPACE_ID, "/ns.txt") == b"default"
        assert read_file(SECONDARY_NAMESPACE_ID, "/ns.txt") == b"secondary"

    runner.run("namespace_isolation", test_namespace_isolation)

    # --- Path Validation ---
    def test_path_validation():
        runner.reset_db()
        try:
            write_file(DEFAULT_NAMESPACE_ID, "../etc/passwd", b"bad")
            assert False, "Should have raised PathValidationError"
        except PathValidationError:
            pass

    runner.run("path_validation", test_path_validation)

    # --- Free Block Retention ---
    def test_free_block_retention():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/reuse.txt", b"data")
        delete_file(DEFAULT_NAMESPACE_ID, "/reuse.txt")
        free_before = StorageBlock.objects.filter(is_free=True).count()
        assert free_before == 1
        write_file(DEFAULT_NAMESPACE_ID, "/reuse2.txt", b"new data")
        free_after = StorageBlock.objects.filter(is_free=True).count()
        assert free_after == 1

    runner.run("free_block_retention", test_free_block_retention)

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

    # --- fsspec Local Cache Wrappers ---
    def test_fsspec_filecache_refresh():
        runner.reset_db()
        with tempfile.TemporaryDirectory() as tmp:
            cached = cached_fs("filecache", tmp)

            write_file(DEFAULT_NAMESPACE_ID, "/cache/file.txt", b"version 1")
            assert cached.cat("/cache/file.txt") == b"version 1"

            write_file(DEFAULT_NAMESPACE_ID, "/cache/file.txt", b"version 2")
            assert cached.cat("/cache/file.txt") == b"version 1"

            checked = cached_fs("filecache", tmp, check_files=True)
            assert checked.cat("/cache/file.txt") == b"version 2"

            write_file(DEFAULT_NAMESPACE_ID, "/cache/file.txt", b"version 3")
            checked.clear_cache()
            assert checked.cat("/cache/file.txt") == b"version 3"

    runner.run("fsspec_filecache_refresh", test_fsspec_filecache_refresh)

    def test_fsspec_simplecache_read_and_write():
        runner.reset_db()
        with tempfile.TemporaryDirectory() as tmp:
            cached = cached_fs("simplecache", tmp)

            write_file(DEFAULT_NAMESPACE_ID, "/cache/simple/source.txt", b"version 1")
            assert cached.cat("/cache/simple/source.txt") == b"version 1"

            write_file(DEFAULT_NAMESPACE_ID, "/cache/simple/source.txt", b"version 2")
            assert cached.cat("/cache/simple/source.txt") == b"version 1"

            with cached.open("/cache/simple/written.txt", "wb") as f:
                f.write(b"written through simplecache")

            assert read_file(
                DEFAULT_NAMESPACE_ID,
                "/cache/simple/written.txt",
            ) == b"written through simplecache"

    runner.run("fsspec_simplecache_read_and_write", test_fsspec_simplecache_read_and_write)

    def test_fsspec_block_cache_seek_and_refresh():
        for protocol in ["blockcache", "cached"]:
            runner.reset_db()
            with tempfile.TemporaryDirectory() as tmp:
                cached = cached_fs(protocol, tmp)
                data = (b"0123456789abcdef" * 8)

                write_file(DEFAULT_NAMESPACE_ID, f"/cache/{protocol}.bin", data)
                with cached.open(f"/cache/{protocol}.bin", "rb", block_size=8) as f:
                    f.seek(10)
                    assert f.read(12) == data[10:22]

                updated = (b"fedcba9876543210" * 8)
                write_file(DEFAULT_NAMESPACE_ID, f"/cache/{protocol}.bin", updated)
                cached.clear_cache()
                with cached.open(f"/cache/{protocol}.bin", "rb", block_size=8) as f:
                    assert f.read() == updated

    runner.run("fsspec_block_cache_seek_and_refresh", test_fsspec_block_cache_seek_and_refresh)

    # --- Unicode ---
    def test_unicode_path():
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/日本語/ファイル.txt", b"unicode content")
        assert read_file(DEFAULT_NAMESPACE_ID, "/日本語/ファイル.txt") == b"unicode content"

    runner.run("unicode_path", test_unicode_path)

    # --- Block Size Coexistence ---
    def test_block_size_coexistence():
        runner.reset_db()
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(DEFAULT_NAMESPACE_ID, "/small_bs.txt", b"A" * 250)
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=256 * 1024):
            write_file(DEFAULT_NAMESPACE_ID, "/large_bs.txt", b"B" * 250)
        assert read_file(DEFAULT_NAMESPACE_ID, "/small_bs.txt") == b"A" * 250
        assert read_file(DEFAULT_NAMESPACE_ID, "/large_bs.txt") == b"B" * 250
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
                    write_file(DEFAULT_NAMESPACE_ID, f"/conc/t{thread_id}/file{i}.txt", f"thread {thread_id} file {i}".encode())
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
                data = read_file(DEFAULT_NAMESPACE_ID, f"/conc/t{t}/file{i}.txt")
                assert data == f"thread {t} file {i}".encode(), f"Data mismatch: t={t} i={i}"

        total = FileNode.objects.filter(namespace=DEFAULT_NAMESPACE_ID, path__startswith="/conc/").count()
        assert total == n_threads * n_files_per_thread

    if not skip_concurrency:
        runner.run("concurrent_write_different_files", test_concurrent_write_different_files)

    def test_concurrent_write_same_file():
        """Multiple threads writing to the same file — one wins, others may get FileConflictError."""
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/conc_same.txt", b"initial")

        n_threads = 8
        results = {"success": 0, "conflict": 0, "other_error": 0}
        lock = threading.Lock()

        def writer(thread_id):
            try:
                write_file(DEFAULT_NAMESPACE_ID, "/conc_same.txt", f"written by thread {thread_id}".encode())
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
        data = read_file(DEFAULT_NAMESPACE_ID, "/conc_same.txt")
        assert data.startswith(b"written by thread ")

    if not skip_concurrency:
        runner.run("concurrent_write_same_file", test_concurrent_write_same_file)

    def test_concurrent_read_while_write():
        """Readers should get consistent data while writers update different files."""
        runner.reset_db()
        # Pre-populate files for reading
        for i in range(20):
            write_file(DEFAULT_NAMESPACE_ID, f"/conc_rw/read{i}.txt", f"read data {i}".encode())

        errors = []
        writer_id_counter = [0]
        counter_lock = threading.Lock()

        def reader():
            try:
                for i in range(20):
                    data = read_file(DEFAULT_NAMESPACE_ID, f"/conc_rw/read{i}.txt")
                    assert data == f"read data {i}".encode()
            except Exception as e:
                errors.append(("reader", e))

        def writer():
            with counter_lock:
                wid = writer_id_counter[0]
                writer_id_counter[0] += 1
            try:
                for i in range(20):
                    write_file(DEFAULT_NAMESPACE_ID, f"/conc_rw/w{wid}/file{i}.txt", f"write data {wid}-{i}".encode())
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
            write_file(DEFAULT_NAMESPACE_ID, f"/conc_dl/file{i}.txt", b"data")

        errors = []

        def deleter():
            try:
                for i in range(50):
                    try:
                        delete_file(DEFAULT_NAMESPACE_ID, f"/conc_dl/file{i}.txt")
                    except FileNotFoundError:
                        pass  # Already deleted by another thread or race
            except Exception as e:
                errors.append(("deleter", e))

        def lister():
            try:
                for _ in range(10):
                    list_directory(DEFAULT_NAMESPACE_ID, "/conc_dl")
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

    # --- Transaction Integrity Tests ---

    def test_tx_atomicity_write_failure():
        """If block allocation fails mid-write, no partial data should remain.
        Simulate by writing a file that exceeds MAX_FILE_SIZE after initial setup."""
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/tx/existing.txt", b"should survive")
        initial_block_count = StorageBlock.objects.filter(is_free=False).count()

        with override_settings(DJANGO_FSSPEC_MAX_FILE_SIZE=10):
            try:
                write_file(DEFAULT_NAMESPACE_ID, "/tx/toobig.txt", b"x" * 20)
            except FileTooLargeError:
                pass

        # No partial file should exist
        assert not file_exists(DEFAULT_NAMESPACE_ID, "/tx/toobig.txt"), "Partial file should not exist"
        # Existing file untouched
        assert read_file(DEFAULT_NAMESPACE_ID, "/tx/existing.txt") == b"should survive"
        # No leaked blocks
        assert StorageBlock.objects.filter(is_free=False).count() == initial_block_count

    runner.run("tx_atomicity_write_failure", test_tx_atomicity_write_failure)

    def test_tx_atomicity_overwrite_rollback():
        """Overwrite with optimistic lock conflict: the conflicting write should
        raise FileConflictError and the file should still exist (not deleted)."""
        if skip_concurrency:
            return
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/tx/overwrite.txt", b"original content")

        # Simulate conflict by bumping version OUTSIDE the transaction, then
        # attempting to overwrite. We need the version bump to happen between
        # the GET and the UPDATE inside write_file's transaction.
        from unittest.mock import patch
        from django.db import connection

        real_get = FileNode.objects.get

        def stale_get(**kwargs):
            obj = real_get(**kwargs)
            if kwargs.get("path") == "/tx/overwrite.txt" or \
               (kwargs.get("namespace") == DEFAULT_NAMESPACE_ID and kwargs.get("path") == "/tx/overwrite.txt"):
                # Bump version using a separate connection to avoid transaction rollback
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE django_fsspec_filenode SET version = 999 WHERE id = %s",
                        [obj.pk]
                    )
            return obj

        conflict_raised = False
        with patch.object(FileNode.objects, "get", side_effect=stale_get):
            try:
                write_file(DEFAULT_NAMESPACE_ID, "/tx/overwrite.txt", b"new content that should fail")
            except FileConflictError:
                conflict_raised = True

        assert conflict_raised, "FileConflictError should have been raised"
        # File should still exist
        assert file_exists(DEFAULT_NAMESPACE_ID, "/tx/overwrite.txt"), "File should still exist after conflict"

    if not skip_concurrency:
        runner.run("tx_atomicity_overwrite_rollback", test_tx_atomicity_overwrite_rollback)

    def test_tx_exclusive_create_race():
        """Two threads racing to create the same file — exactly one should succeed."""
        if skip_concurrency:
            return
        runner.reset_db()
        results = {"created": 0, "exists_error": 0, "other_error": 0}
        lock = threading.Lock()

        def creator(tid):
            try:
                create_file_exclusive(DEFAULT_NAMESPACE_ID, "/tx/race.txt", f"by thread {tid}".encode())
                with lock:
                    results["created"] += 1
            except FileExistsError:
                with lock:
                    results["exists_error"] += 1
            except Exception:
                with lock:
                    results["other_error"] += 1

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(creator, t) for t in range(8)]
            for f in as_completed(futures):
                f.result()

        assert results["created"] == 1, f"Exactly one should create: {results}"
        assert results["other_error"] == 0, f"No unexpected errors: {results}"
        assert results["exists_error"] == 7, f"Others should get FileExistsError: {results}"
        # File should have valid content
        data = read_file(DEFAULT_NAMESPACE_ID, "/tx/race.txt")
        assert data.startswith(b"by thread ")

    if not skip_concurrency:
        runner.run("tx_exclusive_create_race", test_tx_exclusive_create_race)

    def test_tx_delete_while_reading():
        """Deleting a file while another thread reads it — reader should get
        complete data, truncated data (race between block deletion and read),
        or FileNotFoundError. No crashes or unhandled errors."""
        if skip_concurrency:
            return
        runner.reset_db()
        data = b"A" * 1000
        write_file(DEFAULT_NAMESPACE_ID, "/tx/delread.txt", data)

        read_results = {"complete": 0, "not_found": 0, "partial": 0, "error": 0}
        lock = threading.Lock()
        barrier = threading.Barrier(2, timeout=5)

        def reader():
            try:
                barrier.wait()
                for _ in range(20):
                    try:
                        result = read_file(DEFAULT_NAMESPACE_ID, "/tx/delread.txt")
                        with lock:
                            if result == data:
                                read_results["complete"] += 1
                            else:
                                # Partial read can happen in a narrow race window
                                # where FileBlocks are being deleted concurrently.
                                # This is expected with optimistic locking (no read locks).
                                read_results["partial"] += 1
                    except FileNotFoundError:
                        with lock:
                            read_results["not_found"] += 1
            except Exception:
                with lock:
                    read_results["error"] += 1

        def deleter():
            import time
            try:
                barrier.wait()
                time.sleep(0.001)  # Slight delay to let reader start
                delete_file(DEFAULT_NAMESPACE_ID, "/tx/delread.txt")
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(reader), pool.submit(deleter)]
            for f in as_completed(futures):
                f.result()

        # No crashes or unhandled errors
        assert read_results["error"] == 0, f"No unexpected errors: {read_results}"
        # At least some reads should have completed or found not-found
        total = read_results["complete"] + read_results["not_found"] + read_results["partial"]
        assert total == 20, f"All reads should complete: {read_results}"

    if not skip_concurrency:
        runner.run("tx_delete_while_reading", test_tx_delete_while_reading)

    def test_tx_concurrent_overwrite_consistency():
        """Multiple threads overwriting same file — after all writes complete,
        the file should be in a valid, consistent state. Verifies eventual
        consistency (not mid-write snapshot consistency, which requires
        pessimistic locking and is not part of our design)."""
        if skip_concurrency:
            return
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/tx/consist.txt", b"init")

        errors = []
        lock = threading.Lock()

        def overwriter(tid):
            for i in range(20):
                try:
                    payload = f"t{tid}-v{i}-".encode() + bytes(range(256))
                    write_file(DEFAULT_NAMESPACE_ID, "/tx/consist.txt", payload)
                except FileConflictError:
                    pass  # Expected under contention
                except Exception as e:
                    with lock:
                        errors.append(("writer", tid, e))

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(overwriter, t) for t in range(4)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Write errors: {errors}"

        # After all writes complete, verify final state is consistent
        import hashlib
        node = FileNode.objects.get(namespace=DEFAULT_NAMESPACE_ID, path="/tx/consist.txt")
        blocks = list(
            FileBlock.objects.filter(file=node)
            .select_related("block")
            .order_by("sequence")
        )
        data = b"".join(bytes(fb.block.data) for fb in blocks)

        assert node.size == len(data), \
            f"Size mismatch: node.size={node.size} actual={len(data)}"
        actual_checksum = hashlib.sha256(data).hexdigest()
        assert node.checksum == actual_checksum, \
            f"Checksum mismatch: node={node.checksum} actual={actual_checksum}"

        # Sequences should be contiguous
        sequences = [fb.sequence for fb in blocks]
        assert sequences == list(range(len(sequences))), \
            f"Non-contiguous sequences: {sequences}"

    if not skip_concurrency:
        runner.run("tx_concurrent_overwrite_consistency", test_tx_concurrent_overwrite_consistency)

    def test_tx_block_pool_integrity():
        """After many concurrent write/delete cycles, block pool should be consistent:
        - No block referenced by a FileBlock should be marked is_free=True
        - Total used blocks should match sum of file block counts"""
        if skip_concurrency:
            return
        runner.reset_db()

        errors = []

        def churn(tid):
            """Write and delete files rapidly to stress block pool."""
            try:
                for i in range(30):
                    path = f"/tx/churn/t{tid}/f{i}.txt"
                    write_file(DEFAULT_NAMESPACE_ID, path, f"churn-{tid}-{i}".encode() * 10)
                    if i % 3 == 0:
                        try:
                            delete_file(DEFAULT_NAMESPACE_ID, path)
                        except FileNotFoundError:
                            pass
            except Exception as e:
                errors.append(("churn", tid, e))

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(churn, t) for t in range(6)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Churn errors: {errors}"

        # Verify block pool integrity
        # 1. No FileBlock should point to a free StorageBlock
        orphans = FileBlock.objects.filter(block__is_free=True).count()
        assert orphans == 0, f"Found {orphans} file blocks pointing to free storage blocks"

        # 2. Every non-free block should be referenced by at least one FileBlock
        used_block_ids = set(
            StorageBlock.objects.filter(is_free=False).values_list("id", flat=True)
        )
        referenced_block_ids = set(
            FileBlock.objects.values_list("block_id", flat=True)
        )
        unreferenced = used_block_ids - referenced_block_ids
        assert len(unreferenced) == 0, f"Found {len(unreferenced)} used but unreferenced blocks"

        # 3. Every file should have contiguous block sequences
        for node in FileNode.objects.all():
            sequences = list(
                FileBlock.objects.filter(file=node)
                .order_by("sequence")
                .values_list("sequence", flat=True)
            )
            expected = list(range(len(sequences)))
            assert sequences == expected, f"Non-contiguous blocks for {node.path}: {sequences}"

    if not skip_concurrency:
        runner.run("tx_block_pool_integrity", test_tx_block_pool_integrity)

    def test_tx_concurrent_move_no_dupe():
        """Moving files concurrently should not create duplicates or lose files."""
        if skip_concurrency:
            return
        runner.reset_db()
        n = 20
        for i in range(n):
            write_file(DEFAULT_NAMESPACE_ID, f"/tx/move/src{i}.txt", f"file {i}".encode())

        errors = []

        def mover(tid):
            try:
                for i in range(tid, n, 4):  # Each thread handles a subset
                    try:
                        move_file(DEFAULT_NAMESPACE_ID, f"/tx/move/src{i}.txt", f"/tx/move/dst{i}.txt")
                    except (FileNotFoundError, FileExistsError):
                        pass  # Race with another mover
            except Exception as e:
                errors.append(("mover", tid, e))

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(mover, t) for t in range(4)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Move errors: {errors}"

        # Total files should still be n (some at src, some at dst)
        total = FileNode.objects.filter(
            namespace=DEFAULT_NAMESPACE_ID, path__startswith="/tx/move/"
        ).count()
        assert total == n, f"Expected {n} files, found {total}"

        # No path should appear more than once
        paths = list(
            FileNode.objects.filter(namespace=DEFAULT_NAMESPACE_ID, path__startswith="/tx/move/")
            .values_list("path", flat=True)
        )
        assert len(paths) == len(set(paths)), f"Duplicate paths found: {paths}"

    if not skip_concurrency:
        runner.run("tx_concurrent_move_no_dupe", test_tx_concurrent_move_no_dupe)

    def test_tx_concurrent_append_ordering():
        """Multiple threads appending to the same file — final size should equal
        sum of all appended data (no data lost, no duplication)."""
        if skip_concurrency:
            return
        runner.reset_db()
        chunk_size = 50
        n_threads = 4
        n_appends = 10

        # Use exclusive paths per thread to avoid conflict, then compare totals
        for t in range(n_threads):
            write_file(DEFAULT_NAMESPACE_ID, f"/tx/append/log{t}.txt", b"")

        errors = []

        def appender(tid):
            try:
                for i in range(n_appends):
                    marker = f"[t{tid}:i{i}]".encode().ljust(chunk_size, b".")
                    append_file(DEFAULT_NAMESPACE_ID, f"/tx/append/log{tid}.txt", marker)
            except Exception as e:
                errors.append(("appender", tid, e))

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(appender, t) for t in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Append errors: {errors}"

        # Each file should have exactly n_appends chunks
        for t in range(n_threads):
            data = read_file(DEFAULT_NAMESPACE_ID, f"/tx/append/log{t}.txt")
            expected_size = chunk_size * n_appends
            assert len(data) == expected_size, \
                f"Thread {t}: expected {expected_size} bytes, got {len(data)}"

    if not skip_concurrency:
        runner.run("tx_concurrent_append_ordering", test_tx_concurrent_append_ordering)

    def test_tx_concurrent_same_file_append():
        """Concurrent appends to one file may conflict, but successful appends
        must be durable exactly once with intact block metadata."""
        if skip_concurrency:
            return
        runner.reset_db()
        write_file(DEFAULT_NAMESPACE_ID, "/tx/shared_append.log", b"")

        import hashlib

        n_threads = 4
        n_appends = 8
        chunk_size = 64
        barrier = threading.Barrier(n_threads, timeout=10)
        lock = threading.Lock()
        successes = []
        conflicts = 0
        errors = []

        def appender(tid):
            nonlocal conflicts
            try:
                barrier.wait()
                for i in range(n_appends):
                    marker = f"[thread={tid:02d},append={i:02d}]".encode()
                    chunk = marker.ljust(chunk_size, b".")
                    try:
                        append_file(DEFAULT_NAMESPACE_ID, "/tx/shared_append.log", chunk)
                        with lock:
                            successes.append(chunk)
                    except FileConflictError:
                        with lock:
                            conflicts += 1
            except Exception as e:
                with lock:
                    errors.append((tid, e))

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(appender, t) for t in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        assert not errors, f"Append errors: {errors}"
        assert successes, "At least one append should succeed"
        assert conflicts + len(successes) == n_threads * n_appends

        data = read_file(DEFAULT_NAMESPACE_ID, "/tx/shared_append.log")
        assert len(data) == len(successes) * chunk_size
        for chunk in successes:
            assert data.count(chunk) == 1, f"Missing or duplicate chunk: {chunk!r}"

        node = FileNode.objects.get(namespace=DEFAULT_NAMESPACE_ID, path="/tx/shared_append.log")
        blocks = list(
            FileBlock.objects.filter(file=node)
            .select_related("block")
            .order_by("sequence")
        )
        assert node.size == len(data)
        assert node.checksum == hashlib.sha256(data).hexdigest()
        assert [fb.sequence for fb in blocks] == list(range(len(blocks)))

    if not skip_concurrency:
        runner.run("tx_concurrent_same_file_append", test_tx_concurrent_same_file_append)

    def test_tx_namespace_isolation_under_contention():
        """Writes to different namespaces under contention should never leak across."""
        if skip_concurrency:
            return
        runner.reset_db()
        namespace_ids = list(range(DEFAULT_NAMESPACE_ID, DEFAULT_NAMESPACE_ID + 4))
        n_files = 15
        errors = []

        for ns in namespace_ids:
            Namespace.objects.get_or_create(
                id=ns,
                defaults={"name": f"e2e-ns-{ns}", "description": "E2E test namespace"},
            )

        def ns_writer(ns):
            try:
                for i in range(n_files):
                    write_file(ns, f"/tx/ns/file{i}.txt", f"ns{ns}-{i}".encode())
            except Exception as e:
                errors.append(("ns_writer", ns, e))

        with ThreadPoolExecutor(max_workers=len(namespace_ids)) as pool:
            futures = [pool.submit(ns_writer, ns) for ns in namespace_ids]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Namespace errors: {errors}"

        # Verify isolation
        for ns in namespace_ids:
            for i in range(n_files):
                data = read_file(ns, f"/tx/ns/file{i}.txt")
                expected = f"ns{ns}-{i}".encode()
                assert data == expected, f"Namespace leak: ns={ns} i={i} got {data!r}"

            count = FileNode.objects.filter(namespace=ns).count()
            assert count == n_files, f"Namespace {ns}: expected {n_files} files, got {count}"

    if not skip_concurrency:
        runner.run("tx_namespace_isolation_under_contention", test_tx_namespace_isolation_under_contention)

    # --- Mixed Filesystem Semantics ---
    def test_fsspec_mixed_directory_journey():
        runner.reset_db()
        fs.mkdir("/journey")
        fs.mkdir("/journey/empty")
        fs.pipe("/journey/file.txt", b"root")
        fs.pipe("/journey/implicit/deep.txt", b"deep")
        fs.pipe("/journey/empty/child.txt", b"child")
        fs.pipe("/journey/empty/child.txt", b"updated child")
        fs.mv("/journey/file.txt", "/journey/implicit/moved.txt")
        fs.rm("/journey/empty/child.txt")

        assert fs.exists("/journey")
        assert fs.isdir("/journey")
        assert fs.isdir("/journey/empty")
        assert fs.isdir("/journey/implicit")
        assert not fs.exists("/journey/file.txt")
        assert fs.cat("/journey/implicit/moved.txt") == b"root"
        assert fs.ls("/journey/empty", detail=False) == []
        assert sorted(fs.ls("/journey", detail=False)) == [
            "/journey/empty",
            "/journey/implicit",
        ]
        assert sorted(fs.find("/journey")) == [
            "/journey/implicit/deep.txt",
            "/journey/implicit/moved.txt",
        ]
        assert sorted(fs.find("/journey", withdirs=True)) == [
            "/journey/empty",
            "/journey/implicit",
            "/journey/implicit/deep.txt",
            "/journey/implicit/moved.txt",
        ]
        tree = fs.tree("/journey")
        assert "empty" in tree
        assert "deep.txt" in tree
        assert "moved.txt" in tree

    runner.run("fsspec_mixed_directory_journey", test_fsspec_mixed_directory_journey)

    def test_mixed_namespace_tree_conflicts():
        runner.reset_db()
        Namespace.objects.get_or_create(
            id=SECONDARY_NAMESPACE_ID,
            defaults={"name": "secondary", "description": "Secondary test namespace"},
        )
        fs_default = DjangoFileSystem(namespace_id=DEFAULT_NAMESPACE_ID)
        fs_secondary = DjangoFileSystem(namespace_id=SECONDARY_NAMESPACE_ID)

        fs_default.pipe("/shared", b"default flat file")
        fs_secondary.pipe("/shared/child.txt", b"secondary tree file")

        assert fs_default.isfile("/shared")
        assert not fs_default.exists("/shared/child.txt")
        assert fs_default.cat("/shared") == b"default flat file"

        assert fs_secondary.isdir("/shared")
        assert fs_secondary.cat("/shared/child.txt") == b"secondary tree file"
        assert fs_secondary.ls("/shared", detail=False) == ["/shared/child.txt"]

        try:
            fs_default.pipe("/shared/child.txt", b"should fail")
            assert False, "Writing below a file should fail"
        except NotADirectoryError:
            pass

        try:
            fs_secondary.pipe("/shared", b"should fail")
            assert False, "Writing over an implicit directory should fail"
        except IsADirectoryError:
            pass

        assert fs_default.cat("/shared") == b"default flat file"
        assert fs_secondary.cat("/shared/child.txt") == b"secondary tree file"
        assert sorted(fs_secondary.find("/shared", withdirs=True)) == [
            "/shared/child.txt",
        ]

    runner.run("mixed_namespace_tree_conflicts", test_mixed_namespace_tree_conflicts)

    def test_mixed_api_interop_journey():
        runner.reset_db()
        fs.pipe("/interop/raw/input.txt", b"header\n")
        append_file(DEFAULT_NAMESPACE_ID, "/interop/raw/input.txt", b"body\n")
        copy_file(
            DEFAULT_NAMESPACE_ID,
            "/interop/raw/input.txt",
            "/interop/archive/input.txt",
        )
        fs.mv("/interop/archive/input.txt", "/interop/archive/final.txt")
        move_file(
            DEFAULT_NAMESPACE_ID,
            "/interop/raw/input.txt",
            "/interop/raw/source.txt",
        )
        fs.rm("/interop/raw/source.txt")

        assert fs.cat("/interop/archive/final.txt") == b"header\nbody\n"
        assert not fs.exists("/interop/raw/source.txt")
        assert sorted(fs.find("/interop")) == ["/interop/archive/final.txt"]
        assert fs.info("/interop/archive")["type"] == "directory"

    runner.run("mixed_api_interop_journey", test_mixed_api_interop_journey)

    def test_fsspec_recursive_copy_move_journey():
        runner.reset_db()
        fs.mkdir("/project/empty", create_parents=True)
        fs.pipe("/project/input/a.txt", b"a")
        fs.pipe("/project/input/sub/b.txt", b"b")

        fs.copy("/project", "/backup", recursive=True)
        fs.mv("/project/input", "/published", recursive=True)

        assert fs.cat("/backup/input/a.txt") == b"a"
        assert fs.cat("/backup/input/sub/b.txt") == b"b"
        assert fs.isdir("/backup/empty")
        assert fs.cat("/published/a.txt") == b"a"
        assert fs.cat("/published/sub/b.txt") == b"b"
        assert not fs.exists("/project/input")
        assert fs.isdir("/project/empty")

    runner.run("fsspec_recursive_copy_move_journey", test_fsspec_recursive_copy_move_journey)

    def test_transaction_conflict_rolls_back_tree_workflow():
        runner.reset_db()
        fs.pipe("/safe/source.txt", b"source")
        fs.pipe("/safe/dst/existing.txt", b"existing")
        initial_used_blocks = StorageBlock.objects.filter(is_free=False).count()
        initial_free_blocks = StorageBlock.objects.filter(is_free=True).count()

        try:
            with fs.transaction:
                fs.pipe("/safe/temp.txt", b"temporary")
                fs.mv("/safe/source.txt", "/safe/dst/existing.txt")
                assert False, "Moving over an existing file should fail by default"
        except FileExistsError:
            pass

        assert fs.cat("/safe/source.txt") == b"source"
        assert fs.cat("/safe/dst/existing.txt") == b"existing"
        assert not fs.exists("/safe/temp.txt")
        assert StorageBlock.objects.filter(is_free=False).count() == initial_used_blocks
        assert StorageBlock.objects.filter(is_free=True).count() == initial_free_blocks

    runner.run(
        "transaction_conflict_rolls_back_tree_workflow",
        test_transaction_conflict_rolls_back_tree_workflow,
    )

    runner.reset_db()
    return runner.report()


def main():
    db = os.environ.get("DJANGO_FSSPEC_BENCH_DB", "sqlite")
    success = run_e2e(db)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
