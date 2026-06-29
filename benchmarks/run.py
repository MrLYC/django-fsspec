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


DEFAULT_NAMESPACE_ID = 1


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


def scenario_concurrent_write(fs, n_threads=8, n_files=100):
    """Concurrent writes to different files using thread pool."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    data = b"x" * 200
    # Pre-generate paths
    paths = [f"/bench/conc_write/{i}.txt" for i in range(n_files)]

    def write_batch(thread_id, batch):
        for path in batch:
            fs.pipe(path, data)

    # Split files across threads
    batch_size = n_files // n_threads
    batches = [paths[i * batch_size:(i + 1) * batch_size] for i in range(n_threads)]

    _, total_time = timed(lambda: _run_concurrent(write_batch, batches))

    return {
        "op": f"concurrent_write_{n_threads}t",
        "count": n_files,
        "threads": n_threads,
        "file_size": 200,
        "times": [total_time / n_files] * n_files,  # Distribute total time
        "total_wall_s": total_time,
    }


def scenario_concurrent_read(fs, n_threads=8, n_files=100):
    """Concurrent reads from different files using thread pool."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Setup: write files
    data = b"x" * 200
    for i in range(n_files):
        fs.pipe(f"/bench/conc_read/{i}.txt", data)

    paths = [f"/bench/conc_read/{i}.txt" for i in range(n_files)]

    def read_batch(thread_id, batch):
        for path in batch:
            fs.cat(path)

    batch_size = n_files // n_threads
    batches = [paths[i * batch_size:(i + 1) * batch_size] for i in range(n_threads)]

    _, total_time = timed(lambda: _run_concurrent(read_batch, batches))

    return {
        "op": f"concurrent_read_{n_threads}t",
        "count": n_files,
        "threads": n_threads,
        "file_size": 200,
        "times": [total_time / n_files] * n_files,
        "total_wall_s": total_time,
    }


def scenario_concurrent_mixed(fs, n_threads=8, n_ops=200):
    """Mixed concurrent reads and writes (50/50 split)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    data = b"y" * 200
    # Pre-populate read targets
    for i in range(n_ops // 2):
        fs.pipe(f"/bench/conc_mixed/read{i}.txt", data)

    def mixed_batch(thread_id, batch):
        for i, op_type in batch:
            if op_type == "write":
                fs.pipe(f"/bench/conc_mixed/write_t{thread_id}_{i}.txt", data)
            else:
                fs.cat(f"/bench/conc_mixed/read{i}.txt")

    # Build mixed workload: alternate read/write
    all_ops = []
    for i in range(n_ops):
        if i % 2 == 0:
            all_ops.append((i // 2, "read"))
        else:
            all_ops.append((i // 2, "write"))

    batch_size = n_ops // n_threads
    batches = [all_ops[i * batch_size:(i + 1) * batch_size] for i in range(n_threads)]

    _, total_time = timed(lambda: _run_concurrent(mixed_batch, batches))

    return {
        "op": f"concurrent_mixed_{n_threads}t",
        "count": n_ops,
        "threads": n_threads,
        "times": [total_time / n_ops] * n_ops,
        "total_wall_s": total_time,
    }


def _run_concurrent(func, batches):
    """Run func(thread_id, batch) concurrently for each batch."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from django.db import close_old_connections

    def wrapped(thread_id, batch):
        close_old_connections()
        try:
            return func(thread_id, batch)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        futures = [pool.submit(wrapped, i, batch) for i, batch in enumerate(batches)]
        for f in as_completed(futures):
            f.result()  # Raise on error


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
    "concurrent_write": scenario_concurrent_write,
    "concurrent_read": scenario_concurrent_read,
    "concurrent_mixed": scenario_concurrent_mixed,
}


def summarize(result):
    """Add summary statistics to a result dict."""
    times = result["times"]
    result["total_s"] = sum(times)
    result["avg_ms"] = statistics.mean(times) * 1000
    result["p50_ms"] = statistics.median(times) * 1000
    result["p95_ms"] = sorted(times)[int(len(times) * 0.95)] * 1000
    result["p99_ms"] = sorted(times)[int(len(times) * 0.99)] * 1000

    # For concurrent scenarios, use wall time for ops/sec calculation
    if "total_wall_s" in result:
        wall = result["total_wall_s"]
        result["ops_per_sec"] = len(times) / wall if wall > 0 else 0
    else:
        result["ops_per_sec"] = len(times) / sum(times) if sum(times) > 0 else 0

    del result["times"]  # Don't print raw times
    return result


def run_benchmark(db_name, scenarios):
    """Run benchmark scenarios on a specific database."""
    print(f"\n{'=' * 60}")
    print(f"  Database: {db_name}")
    print(f"{'=' * 60}")

    call_command("migrate", verbosity=0)

    fs = DjangoFileSystem(namespace=DEFAULT_NAMESPACE_ID)
    results = []

    for name, func in scenarios.items():
        reset_db()
        print(f"\n  Running: {name} ...", end=" ", flush=True)
        try:
            result = func(fs)
            result = summarize(result)
            result["db"] = db_name
            results.append(result)
            extra = ""
            if "total_wall_s" in result:
                extra = f"  wall={result['total_wall_s']:.2f}s  threads={result.get('threads', '?')}"
            print(
                f"done  "
                f"avg={result['avg_ms']:.2f}ms  "
                f"p95={result['p95_ms']:.2f}ms  "
                f"ops/s={result['ops_per_sec']:.0f}"
                f"{extra}"
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

    # When --db is specified, use the current process (env var already set at startup).
    # When running multiple DBs, spawn subprocesses to avoid Django settings conflicts.
    db_list = [args.db] if args.db else ["sqlite", "mysql", "postgres"]
    all_results = []

    if args.db:
        # Single DB mode — current process, env var DJANGO_FSSPEC_BENCH_DB already set
        results = run_benchmark(args.db, scenarios)
        all_results.extend(results)
    else:
        # Multi-DB mode — run each as subprocess to get clean Django init
        import subprocess
        scenario_arg = ["--scenario", args.scenario] if args.scenario else []
        json_files = []
        for db in db_list:
            json_file = f"/tmp/bench_{db}.json"
            json_files.append((db, json_file))
            env = os.environ.copy()
            env["DJANGO_FSSPEC_BENCH_DB"] = db
            result = subprocess.run(
                [sys.executable, __file__, "--db", db, "--json", json_file] + scenario_arg,
                env=env, capture_output=True, text=True,
            )
            print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)

        # Collect results
        for db, json_file in json_files:
            try:
                with open(json_file) as f:
                    all_results.extend(json.load(f))
            except FileNotFoundError:
                pass

    print_summary_table(all_results)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(all_results, f, indent=2, default=str)
        print(f"\nResults saved to {args.json}")


if __name__ == "__main__":
    main()
