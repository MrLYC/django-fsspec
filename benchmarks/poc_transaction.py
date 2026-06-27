#!/usr/bin/env python
"""PoC: Verify set_rollback(True) + atomic().__exit__(None,None,None) behavior
across all database backends.

Tests:
1. atomic() in autocommit mode — commit path
2. atomic() in autocommit mode — rollback via set_rollback
3. atomic() in autocommit mode — rollback via exception
4. atomic() nested inside another atomic() — commit
5. atomic() nested inside another atomic() — rollback via set_rollback
6. atomic() nested inside another atomic() — inner rollback, outer continues
7. After rollback, can we start a new atomic() and succeed?

Usage:
    DJANGO_FSSPEC_BENCH_DB=sqlite python benchmarks/poc_transaction.py
    DJANGO_FSSPEC_BENCH_DB=mysql MYSQL_PORT=13306 python benchmarks/poc_transaction.py
    DJANGO_FSSPEC_BENCH_DB=postgres POSTGRES_PORT=15432 python benchmarks/poc_transaction.py
"""

import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "benchmarks.settings")

import django
django.setup()

from django.core.management import call_command
from django.db import connection, transaction

from django_fsspec.models import FileNode

call_command("migrate", verbosity=0)

db = os.environ.get("DJANGO_FSSPEC_BENCH_DB", "sqlite")
print(f"\n{'='*60}")
print(f"  PoC Transaction Tests: {db}")
print(f"  Autocommit: {connection.get_autocommit()}")
print(f"  In atomic block: {connection.in_atomic_block}")
print(f"{'='*60}")

passed = 0
failed = 0


def reset():
    FileNode.objects.all().delete()


def test(name, func):
    global passed, failed
    reset()
    print(f"\n  {name} ...", end=" ", flush=True)
    try:
        func()
        passed += 1
        print("OK")
    except Exception as e:
        failed += 1
        print(f"FAIL: {e}")


# ---- Test 1: atomic() commit in autocommit mode ----
def test_atomic_commit():
    assert connection.get_autocommit(), "Should be in autocommit mode"
    atomic = transaction.atomic()
    atomic.__enter__()
    FileNode.objects.create(namespace=99, path="/poc/t1.txt", size=0, block_size=256, version=1)
    assert FileNode.objects.filter(path="/poc/t1.txt").exists(), "Should exist inside atomic"
    atomic.__exit__(None, None, None)
    assert FileNode.objects.filter(path="/poc/t1.txt").exists(), "Should exist after commit"

test("1. atomic() commit in autocommit mode", test_atomic_commit)


# ---- Test 2: atomic() rollback via set_rollback in autocommit mode ----
def test_atomic_set_rollback():
    assert connection.get_autocommit(), "Should be in autocommit mode"
    atomic = transaction.atomic()
    atomic.__enter__()
    FileNode.objects.create(namespace=99, path="/poc/t2.txt", size=0, block_size=256, version=1)
    assert FileNode.objects.filter(path="/poc/t2.txt").exists(), "Should exist inside atomic"
    transaction.set_rollback(True)
    atomic.__exit__(None, None, None)
    exists = FileNode.objects.filter(path="/poc/t2.txt").exists()
    assert not exists, f"Should NOT exist after set_rollback+exit, but exists={exists}"

test("2. atomic() rollback via set_rollback", test_atomic_set_rollback)


# ---- Test 3: atomic() rollback via exception ----
def test_atomic_exception_rollback():
    assert connection.get_autocommit(), "Should be in autocommit mode"
    atomic = transaction.atomic()
    atomic.__enter__()
    FileNode.objects.create(namespace=99, path="/poc/t3.txt", size=0, block_size=256, version=1)
    try:
        atomic.__exit__(ValueError, ValueError("test"), None)
    except ValueError:
        pass
    exists = FileNode.objects.filter(path="/poc/t3.txt").exists()
    assert not exists, f"Should NOT exist after exception rollback, but exists={exists}"

test("3. atomic() rollback via exception", test_atomic_exception_rollback)


# ---- Test 4: nested atomic() commit ----
def test_nested_commit():
    with transaction.atomic():  # outer
        FileNode.objects.create(namespace=99, path="/poc/t4_outer.txt", size=0, block_size=256, version=1)
        atomic_inner = transaction.atomic()
        atomic_inner.__enter__()
        FileNode.objects.create(namespace=99, path="/poc/t4_inner.txt", size=0, block_size=256, version=1)
        atomic_inner.__exit__(None, None, None)
    assert FileNode.objects.filter(path="/poc/t4_outer.txt").exists()
    assert FileNode.objects.filter(path="/poc/t4_inner.txt").exists()

test("4. nested atomic() commit", test_nested_commit)


# ---- Test 5: nested atomic() rollback via set_rollback ----
def test_nested_set_rollback():
    with transaction.atomic():  # outer
        FileNode.objects.create(namespace=99, path="/poc/t5_outer.txt", size=0, block_size=256, version=1)
        atomic_inner = transaction.atomic()
        atomic_inner.__enter__()
        FileNode.objects.create(namespace=99, path="/poc/t5_inner.txt", size=0, block_size=256, version=1)
        transaction.set_rollback(True)
        atomic_inner.__exit__(None, None, None)
        # After inner rollback, can outer continue?
        # Django docs say: after set_rollback in a savepoint, it only affects that savepoint
        outer_ok = FileNode.objects.filter(path="/poc/t5_outer.txt").exists()
        inner_gone = not FileNode.objects.filter(path="/poc/t5_inner.txt").exists()
    assert outer_ok, "Outer data should survive inner rollback"
    assert inner_gone, "Inner data should be rolled back"

test("5. nested atomic() rollback inner via set_rollback", test_nested_set_rollback)


# ---- Test 6: nested atomic() inner exception, outer continues ----
def test_nested_inner_exception_outer_continues():
    with transaction.atomic():  # outer
        FileNode.objects.create(namespace=99, path="/poc/t6_outer.txt", size=0, block_size=256, version=1)
        try:
            with transaction.atomic():  # inner savepoint
                FileNode.objects.create(namespace=99, path="/poc/t6_inner.txt", size=0, block_size=256, version=1)
                raise ValueError("inner error")
        except ValueError:
            pass
        # Can outer continue after inner exception?
        FileNode.objects.create(namespace=99, path="/poc/t6_after.txt", size=0, block_size=256, version=1)
    assert FileNode.objects.filter(path="/poc/t6_outer.txt").exists()
    assert not FileNode.objects.filter(path="/poc/t6_inner.txt").exists()
    assert FileNode.objects.filter(path="/poc/t6_after.txt").exists()

test("6. nested inner exception, outer continues", test_nested_inner_exception_outer_continues)


# ---- Test 7: after rollback, new atomic() works ----
def test_new_atomic_after_rollback():
    atomic1 = transaction.atomic()
    atomic1.__enter__()
    FileNode.objects.create(namespace=99, path="/poc/t7_rolled.txt", size=0, block_size=256, version=1)
    transaction.set_rollback(True)
    atomic1.__exit__(None, None, None)

    # Start a new atomic
    atomic2 = transaction.atomic()
    atomic2.__enter__()
    FileNode.objects.create(namespace=99, path="/poc/t7_new.txt", size=0, block_size=256, version=1)
    atomic2.__exit__(None, None, None)

    assert not FileNode.objects.filter(path="/poc/t7_rolled.txt").exists()
    assert FileNode.objects.filter(path="/poc/t7_new.txt").exists()

test("7. new atomic() after rollback", test_new_atomic_after_rollback)


# ---- Test 8: set_rollback inside nested, then outer commits ----
def test_set_rollback_nested_outer_commits():
    atomic_outer = transaction.atomic()
    atomic_outer.__enter__()
    FileNode.objects.create(namespace=99, path="/poc/t8_outer.txt", size=0, block_size=256, version=1)

    atomic_inner = transaction.atomic()
    atomic_inner.__enter__()
    FileNode.objects.create(namespace=99, path="/poc/t8_inner.txt", size=0, block_size=256, version=1)
    transaction.set_rollback(True)
    atomic_inner.__exit__(None, None, None)

    # Can we still create and commit in outer?
    try:
        FileNode.objects.create(namespace=99, path="/poc/t8_after.txt", size=0, block_size=256, version=1)
        atomic_outer.__exit__(None, None, None)
        outer_committed = True
    except Exception as e:
        atomic_outer.__exit__(type(e), e, None)
        outer_committed = False

    if outer_committed:
        assert FileNode.objects.filter(path="/poc/t8_outer.txt").exists()
        assert not FileNode.objects.filter(path="/poc/t8_inner.txt").exists()
        assert FileNode.objects.filter(path="/poc/t8_after.txt").exists()
        print(f"    (outer committed successfully after inner rollback)")
    else:
        print(f"    (outer FAILED after inner set_rollback — this DB marks tx broken)")

test("8. set_rollback nested, outer continues", test_set_rollback_nested_outer_commits)


# ---- Summary ----
reset()
print(f"\n{'='*60}")
print(f"  Results: {passed} passed, {failed} failed")
print(f"{'='*60}")
sys.exit(0 if failed == 0 else 1)
