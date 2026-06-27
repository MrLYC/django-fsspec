#!/usr/bin/env python
"""Multi-scenario, multi-database benchmark for django-fsspec.

Usage:
    # Run all databases
    python benchmarks/run.py

    # Run specific database
    DJANGO_FSSPEC_BENCH_DB=mysql python benchmarks/run.py

    # Run specific scenario
    python benchmarks/run.py --scenario write_small
"""

import argparse
import json
import os
import statistics
import sys
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "benchmarks.settings")

import django

django.setup()

from django.core.management import call_command

from django_fsspec.fs import DjangoFileSystem
from django_fsspec.models import FileBlock, FileNode, StorageBlock


def reset_db():
    """Clear all data for a clean benchmark run."""
    FileBlock.objects.all().delete()
    StorageBlock.objects.all().delete()
    FileNode.objects.all().delete()


def timed(func, *args, **kwargs):
    """Run func and return (result, elapsed_seconds)."""
    start = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed = time.perf_counter() - start
    return result, elapsed


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def scenario_write_small(fs, n=1000):
    """Write N small files (100 bytes each)."""
    data = b"x" * 100
    times = []
    for i in range(n):
        _, t = timed(lambda: fs.pipe(f"/bench/small/{i}.txt", data))
        times.append(t)
    return {"op": "write_small", "count": n, "file_size": 100, "times": times}


def scenario_write_medium(fs, n=200):
    """Write N medium files (10KB each)."""
    data = b"y" * (10 * 1024)
    times = []
    for i in range(n):
        _, t = timed(lambda: fs.pipe(f"/bench/medium/{i}.txt", data))
        times.append(t)
    return {"op": "write_medium", "count": n, "file_size": 10240, "times": times}


def scenario_write_large(fs, n=50):
    """Write N large files (1MB each)."""
    data = b"z" * (1024 * 1024)
    times = []
    for i in range(n):
        _, t = timed(lambda: fs.pipe(f"/bench/large/{i}.bin", data))
        times.append(t)
    return {"op": "write_large", "count": n, "file_size": 1048576, "times": times}


def scenario_read_small(fs, n=1000):
    """Read N small files."""
    # Setup: write files first
    data = b"x" * 100
    for i in range(n):
        fs.pipe(f"/bench/read_small/{i}.txt", data)

    times = []
    for i in range(n):
        _, t = timed(lambda: fs.cat(f"/bench/read_small/{i}.txt"))
        times.append(t)
    return {"op": "read_small", "count": n, "file_size": 100, "times": times}


def scenario_read_large(fs, n=50):
    """Read N large files."""
    data = b"z" * (1024 * 1024)
    for i in range(n):
        fs.pipe(f"/bench/read_large/{i}.bin", data)

    times = []
    for i in range(n):
        _, t = timed(lambda: fs.cat(f"/bench/read_large/{i}.bin"))
        times.append(t)
    return {"op": "read_large", "count": n, "file_size": 1048576, "times": times}


def scenario_overwrite(fs, n=500):
    """Overwrite the same file N times."""
    path = "/bench/overwrite/target.txt"
    times = []
    for i in range(n):
        data = f"version {i}".encode()
        _, t = timed(lambda: fs.pipe(path, data))
        times.append(t)
    return {"op": "overwrite", "count": n, "file_size": 10, "times": times}


def scenario_ls_flat(fs):
    """List a directory with 1000 files."""
    data = b"x" * 50
    for i in range(1000):
        fs.pipe(f"/bench/ls_flat/{i}.txt", data)

    times = []
    for _ in range(100):
        _, t = timed(lambda: fs.ls("/bench/ls_flat", detail=False))
        times.append(t)
    return {"op": "ls_flat_1000", "count": 100, "dir_size": 1000, "times": times}


def scenario_ls_nested(fs):
    """List a directory with nested subdirectories."""
    data = b"x" * 50
    for i in range(100):
        for j in range(10):
            fs.pipe(f"/bench/ls_nested/dir{i}/file{j}.txt", data)

    times = []
    for _ in range(100):
        _, t = timed(lambda: fs.ls("/bench/ls_nested", detail=False))
        times.append(t)
    return {"op": "ls_nested_100dirs", "count": 100, "dir_size": 100, "times": times}


def scenario_delete(fs, n=500):
    """Delete N files."""
    data = b"x" * 100
    for i in range(n):
        fs.pipe(f"/bench/delete/{i}.txt", data)

    times = []
    for i in range(n):
        _, t = timed(lambda: fs.rm(f"/bench/delete/{i}.txt"))
        times.append(t)
    return {"op": "delete", "count": n, "times": times}


def scenario_seek_read(fs, n=100):
    """Random seek + read on a large file."""
    import random

    data = bytes(random.getrandbits(8) for _ in range(1024 * 1024))
    fs.pipe("/bench/seek/big.bin", data)

    times = []
    for _ in range(n):
        offset = random.randint(0, len(data) - 1024)

        def do_seek_read():
            with fs.open("/bench/seek/big.bin", "rb") as f:
                f.seek(offset)
                return f.read(1024)

        _, t = timed(do_seek_read)
        times.append(t)
    return {"op": "seek_read", "count": n, "file_size": 1048576, "times": times}


ALL_SCENARIOS = {
    "write_small": scenario_write_small,
    "write_medium": scenario_write_medium,
    "write_large": scenario_write_large,
    "read_small": scenario_read_small,
    "read_large": scenario_read_large,
    "overwrite": scenario_overwrite,
    "ls_flat": scenario_ls_flat,
    "ls_nested": scenario_ls_nested,
    "delete": scenario_delete,
    "seek_read": scenario_seek_read,
}


def summarize(result):
    """Add summary statistics to a result dict."""
    times = result["times"]
    result["total_s"] = sum(times)
    result["avg_ms"] = statistics.mean(times) * 1000
    result["p50_ms"] = statistics.median(times) * 1000
    result["p95_ms"] = sorted(times)[int(len(times) * 0.95)] * 1000
    result["p99_ms"] = sorted(times)[int(len(times) * 0.99)] * 1000
    result["ops_per_sec"] = len(times) / sum(times) if sum(times) > 0 else 0
    del result["times"]  # Don't print raw times
    return result


def run_benchmark(db_name, scenarios):
    """Run benchmark scenarios on a specific database."""
    print(f"\n{'=' * 60}")
    print(f"  Database: {db_name}")
    print(f"{'=' * 60}")

    call_command("migrate", verbosity=0)

    fs = DjangoFileSystem(namespace=0)
    results = []

    for name, func in scenarios.items():
        reset_db()
        print(f"\n  Running: {name} ...", end=" ", flush=True)
        try:
            result = func(fs)
            result = summarize(result)
            result["db"] = db_name
            results.append(result)
            print(
                f"done  "
                f"avg={result['avg_ms']:.2f}ms  "
                f"p95={result['p95_ms']:.2f}ms  "
                f"ops/s={result['ops_per_sec']:.0f}"
            )
        except Exception as e:
            print(f"FAILED: {e}")
            results.append({"op": name, "db": db_name, "error": str(e)})

    reset_db()
    return results


def print_summary_table(all_results):
    """Print a comparison table across databases."""
    print(f"\n\n{'=' * 80}")
    print("  SUMMARY")
    print(f"{'=' * 80}")

    # Group by operation
    ops = {}
    for r in all_results:
        op = r.get("op", "unknown")
        if op not in ops:
            ops[op] = {}
        ops[op][r.get("db", "?")] = r

    header = f"{'Operation':<22} {'Database':<10} {'Avg(ms)':>8} {'P95(ms)':>8} {'Ops/s':>8}"
    print(f"\n{header}")
    print("-" * len(header))

    for op, dbs in ops.items():
        for db, r in sorted(dbs.items()):
            if "error" in r:
                print(f"{op:<22} {db:<10} {'ERROR':>8}")
            else:
                print(
                    f"{op:<22} {db:<10} "
                    f"{r['avg_ms']:>8.2f} {r['p95_ms']:>8.2f} "
                    f"{r['ops_per_sec']:>8.0f}"
                )
        print()


def main():
    parser = argparse.ArgumentParser(description="django-fsspec benchmarks")
    parser.add_argument(
        "--scenario", type=str, default=None,
        help=f"Run specific scenario: {', '.join(ALL_SCENARIOS.keys())}",
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help="Run specific database: sqlite, mysql, postgres, oracle (default: all except oracle)",
    )
    parser.add_argument(
        "--json", type=str, default=None,
        help="Save results to JSON file",
    )
    args = parser.parse_args()

    if args.scenario:
        scenarios = {args.scenario: ALL_SCENARIOS[args.scenario]}
    else:
        scenarios = ALL_SCENARIOS

    db_list = [args.db] if args.db else ["sqlite", "mysql", "postgres"]
    all_results = []

    db_configs = {
        "sqlite": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.path.join(os.path.dirname(__file__), "bench.sqlite3"),
        },
        "mysql": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("MYSQL_DATABASE", "fsspec_test"),
            "USER": os.environ.get("MYSQL_USER", "fsspec"),
            "PASSWORD": os.environ.get("MYSQL_PASSWORD", "fsspec_test"),
            "HOST": os.environ.get("MYSQL_HOST", "127.0.0.1"),
            "PORT": os.environ.get("MYSQL_PORT", "13306"),
            "OPTIONS": {"charset": "utf8mb4"},
        },
        "postgres": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "fsspec_test"),
            "USER": os.environ.get("POSTGRES_USER", "fsspec"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "fsspec_test"),
            "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.environ.get("POSTGRES_PORT", "15432"),
        },
        "oracle": {
            "ENGINE": "django.db.backends.oracle",
            "NAME": os.environ.get("ORACLE_DSN", "127.0.0.1:1521/FREEPDB1"),
            "USER": os.environ.get("ORACLE_USER", "fsspec"),
            "PASSWORD": os.environ.get("ORACLE_PASSWORD", "fsspec_test"),
        },
    }

    for db in db_list:
        os.environ["DJANGO_FSSPEC_BENCH_DB"] = db

        from django.conf import settings
        settings.DATABASES["default"] = db_configs[db]

        from django.db import connections
        for conn in connections.all():
            conn.close()

        results = run_benchmark(db, scenarios)
        all_results.extend(results)

    print_summary_table(all_results)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(all_results, f, indent=2, default=str)
        print(f"\nResults saved to {args.json}")


if __name__ == "__main__":
    main()
