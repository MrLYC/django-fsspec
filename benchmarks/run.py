#!/usr/bin/env python
"""Multi-scenario, multi-database benchmark for django-fsspec.

Usage:
    # Run all databases
    python benchmarks/run.py

    # Run specific database
    DJANGO_FSSPEC_BENCH_DB=mysql python benchmarks/run.py --db mysql

    # Run specific scenario
    python benchmarks/run.py --scenario write_small

    # Run scale-based seeded scenarios
    python benchmarks/run.py --scale medium --scenario seeded_exists
"""

import argparse
import json
import os
import random
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
SEEDED_ROOT = "/bench/seeded"

SCALES = {
    "ci": {
        "write_small_n": 1000,
        "write_medium_n": 200,
        "write_large_n": 50,
        "read_small_n": 1000,
        "read_large_n": 50,
        "overwrite_n": 500,
        "delete_n": 500,
        "seek_read_n": 100,
        "concurrent_threads": 8,
        "concurrent_files": 100,
        "concurrent_ops": 200,
        "ls_flat_files": 1000,
        "ls_flat_repeats": 100,
        "ls_nested_dirs": 100,
        "ls_nested_files_per_dir": 10,
        "ls_nested_repeats": 100,
        "seeded_files": 100,
        "seeded_dirs": 10,
        "seeded_repeats": 25,
        "seeded_find_repeats": 1,
    },
    "medium": {
        "write_small_n": 1000,
        "write_medium_n": 200,
        "write_large_n": 50,
        "read_small_n": 1000,
        "read_large_n": 50,
        "overwrite_n": 500,
        "delete_n": 500,
        "seek_read_n": 100,
        "concurrent_threads": 8,
        "concurrent_files": 100,
        "concurrent_ops": 200,
        "ls_flat_files": 1000,
        "ls_flat_repeats": 100,
        "ls_nested_dirs": 100,
        "ls_nested_files_per_dir": 10,
        "ls_nested_repeats": 100,
        "seeded_files": 10_000,
        "seeded_dirs": 100,
        "seeded_repeats": 250,
        "seeded_find_repeats": 5,
    },
    "large": {
        "write_small_n": 1000,
        "write_medium_n": 200,
        "write_large_n": 50,
        "read_small_n": 1000,
        "read_large_n": 50,
        "overwrite_n": 500,
        "delete_n": 500,
        "seek_read_n": 100,
        "concurrent_threads": 8,
        "concurrent_files": 100,
        "concurrent_ops": 200,
        "ls_flat_files": 1000,
        "ls_flat_repeats": 100,
        "ls_nested_dirs": 100,
        "ls_nested_files_per_dir": 10,
        "ls_nested_repeats": 100,
        "seeded_files": 50_000,
        "seeded_dirs": 500,
        "seeded_repeats": 500,
        "seeded_find_repeats": 3,
    },
}

DEFAULT_CI_SCENARIOS = [
    "write_small",
    "write_medium",
    "write_large",
    "read_small",
    "read_large",
    "overwrite",
    "ls_flat",
    "ls_nested",
    "delete",
    "seek_read",
    "concurrent_write",
    "concurrent_read",
    "concurrent_mixed",
]

SEEDED_SCENARIOS = [
    "seeded_ls_root",
    "seeded_ls_deep",
    "seeded_exists",
    "seeded_info",
    "seeded_find",
]


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


def require_positive(name, value):
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def get_count(ctx, name):
    return require_positive(name, ctx["scale_config"][name])


def split_evenly(items, n_batches):
    """Split items into n batches, preserving all items including remainders."""
    require_positive("n_batches", n_batches)
    batches = [[] for _ in range(n_batches)]
    for index, item in enumerate(items):
        batches[index % n_batches].append(item)
    return batches


def make_context(scale, seed):
    return {
        "scale": scale,
        "seed": seed,
        "scale_config": SCALES[scale],
        "backend": os.environ.get("DJANGO_FSSPEC_BENCH_DB", "sqlite"),
    }


def add_result_metadata(result, db_name, ctx):
    result["db"] = db_name
    result["backend"] = ctx["backend"]
    result["scale"] = ctx["scale"]
    result["seed"] = ctx["seed"]
    return result


def build_seeded_paths(seed, file_count, dir_count, root=SEEDED_ROOT):
    file_count = require_positive("file_count", file_count)
    dir_count = require_positive("dir_count", dir_count)
    rng = random.Random(seed)
    dir_indexes = list(range(dir_count))
    rng.shuffle(dir_indexes)

    paths = []
    for i in range(file_count):
        dir_index = dir_indexes[i % dir_count]
        shard = dir_index // 100
        paths.append(f"{root}/shard-{shard:03d}/dir-{dir_index:05d}/file-{i:08d}.dat")
    return paths


def seed_dataset(fs, ctx):
    config = ctx["scale_config"]
    file_count = get_count(ctx, "seeded_files")
    dir_count = get_count(ctx, "seeded_dirs")
    paths = build_seeded_paths(ctx["seed"], file_count, dir_count)
    data = b"seeded benchmark payload\n"

    for path in paths:
        fs.pipe(path, data)

    dirs = sorted({path.rsplit("/", 1)[0] for path in paths})
    return {
        "seed_root": SEEDED_ROOT,
        "seeded_files": file_count,
        "seeded_dirs": dir_count,
        "seeded_repeats": config["seeded_repeats"],
        "seeded_paths": paths,
        "seeded_directories": dirs,
    }


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def scenario_write_small(fs, ctx):
    """Write N small files (100 bytes each)."""
    n = get_count(ctx, "write_small_n")
    data = b"x" * 100
    times = []
    for i in range(n):
        _, t = timed(lambda: fs.pipe(f"/bench/small/{i}.txt", data))
        times.append(t)
    return {"op": "write_small", "count": n, "file_size": 100, "times": times}


def scenario_write_medium(fs, ctx):
    """Write N medium files (10KB each)."""
    n = get_count(ctx, "write_medium_n")
    data = b"y" * (10 * 1024)
    times = []
    for i in range(n):
        _, t = timed(lambda: fs.pipe(f"/bench/medium/{i}.txt", data))
        times.append(t)
    return {"op": "write_medium", "count": n, "file_size": 10240, "times": times}


def scenario_write_large(fs, ctx):
    """Write N large files (1MB each)."""
    n = get_count(ctx, "write_large_n")
    data = b"z" * (1024 * 1024)
    times = []
    for i in range(n):
        _, t = timed(lambda: fs.pipe(f"/bench/large/{i}.bin", data))
        times.append(t)
    return {"op": "write_large", "count": n, "file_size": 1048576, "times": times}


def scenario_read_small(fs, ctx):
    """Read N small files."""
    n = get_count(ctx, "read_small_n")
    data = b"x" * 100
    for i in range(n):
        fs.pipe(f"/bench/read_small/{i}.txt", data)

    times = []
    for i in range(n):
        _, t = timed(lambda: fs.cat(f"/bench/read_small/{i}.txt"))
        times.append(t)
    return {"op": "read_small", "count": n, "file_size": 100, "times": times}


def scenario_read_large(fs, ctx):
    """Read N large files."""
    n = get_count(ctx, "read_large_n")
    data = b"z" * (1024 * 1024)
    for i in range(n):
        fs.pipe(f"/bench/read_large/{i}.bin", data)

    times = []
    for i in range(n):
        _, t = timed(lambda: fs.cat(f"/bench/read_large/{i}.bin"))
        times.append(t)
    return {"op": "read_large", "count": n, "file_size": 1048576, "times": times}


def scenario_overwrite(fs, ctx):
    """Overwrite the same file N times."""
    n = get_count(ctx, "overwrite_n")
    path = "/bench/overwrite/target.txt"
    times = []
    for i in range(n):
        data = f"version {i}".encode()
        _, t = timed(lambda: fs.pipe(path, data))
        times.append(t)
    return {"op": "overwrite", "count": n, "file_size": 10, "times": times}


def scenario_ls_flat(fs, ctx):
    """List a directory with a flat file set."""
    file_count = get_count(ctx, "ls_flat_files")
    repeats = get_count(ctx, "ls_flat_repeats")
    data = b"x" * 50
    for i in range(file_count):
        fs.pipe(f"/bench/ls_flat/{i}.txt", data)

    times = []
    for _ in range(repeats):
        _, t = timed(lambda: fs.ls("/bench/ls_flat", detail=False))
        times.append(t)
    return {
        "op": f"ls_flat_{file_count}",
        "count": repeats,
        "dir_size": file_count,
        "times": times,
    }


def scenario_ls_nested(fs, ctx):
    """List a directory with nested subdirectories."""
    dir_count = get_count(ctx, "ls_nested_dirs")
    files_per_dir = get_count(ctx, "ls_nested_files_per_dir")
    repeats = get_count(ctx, "ls_nested_repeats")
    data = b"x" * 50
    for i in range(dir_count):
        for j in range(files_per_dir):
            fs.pipe(f"/bench/ls_nested/dir{i}/file{j}.txt", data)

    times = []
    for _ in range(repeats):
        _, t = timed(lambda: fs.ls("/bench/ls_nested", detail=False))
        times.append(t)
    return {
        "op": f"ls_nested_{dir_count}dirs",
        "count": repeats,
        "dir_size": dir_count,
        "files_per_dir": files_per_dir,
        "times": times,
    }


def scenario_delete(fs, ctx):
    """Delete N files."""
    n = get_count(ctx, "delete_n")
    data = b"x" * 100
    for i in range(n):
        fs.pipe(f"/bench/delete/{i}.txt", data)

    times = []
    for i in range(n):
        _, t = timed(lambda: fs.rm(f"/bench/delete/{i}.txt"))
        times.append(t)
    return {"op": "delete", "count": n, "times": times}


def scenario_seek_read(fs, ctx):
    """Random seek + read on a large file."""
    n = get_count(ctx, "seek_read_n")
    rng = random.Random(ctx["seed"])
    data = bytes(rng.getrandbits(8) for _ in range(1024 * 1024))
    fs.pipe("/bench/seek/big.bin", data)

    times = []
    for _ in range(n):
        offset = rng.randint(0, len(data) - 1024)

        def do_seek_read():
            with fs.open("/bench/seek/big.bin", "rb") as f:
                f.seek(offset)
                return f.read(1024)

        _, t = timed(do_seek_read)
        times.append(t)
    return {"op": "seek_read", "count": n, "file_size": 1048576, "times": times}


def scenario_concurrent_write(fs, ctx):
    """Concurrent writes to different files using thread pool."""
    n_threads = get_count(ctx, "concurrent_threads")
    n_files = get_count(ctx, "concurrent_files")
    data = b"x" * 200
    paths = [f"/bench/conc_write/{i}.txt" for i in range(n_files)]

    def write_batch(thread_id, batch):
        for path in batch:
            fs.pipe(path, data)

    batches = split_evenly(paths, n_threads)
    _, total_time = timed(lambda: _run_concurrent(write_batch, batches))

    return {
        "op": f"concurrent_write_{n_threads}t",
        "count": n_files,
        "threads": n_threads,
        "file_size": 200,
        "times": [total_time / n_files] * n_files,
        "total_wall_s": total_time,
    }


def scenario_concurrent_read(fs, ctx):
    """Concurrent reads from different files using thread pool."""
    n_threads = get_count(ctx, "concurrent_threads")
    n_files = get_count(ctx, "concurrent_files")
    data = b"x" * 200
    for i in range(n_files):
        fs.pipe(f"/bench/conc_read/{i}.txt", data)

    paths = [f"/bench/conc_read/{i}.txt" for i in range(n_files)]

    def read_batch(thread_id, batch):
        for path in batch:
            fs.cat(path)

    batches = split_evenly(paths, n_threads)
    _, total_time = timed(lambda: _run_concurrent(read_batch, batches))

    return {
        "op": f"concurrent_read_{n_threads}t",
        "count": n_files,
        "threads": n_threads,
        "file_size": 200,
        "times": [total_time / n_files] * n_files,
        "total_wall_s": total_time,
    }


def scenario_concurrent_mixed(fs, ctx):
    """Mixed concurrent reads and writes (50/50 split)."""
    n_threads = get_count(ctx, "concurrent_threads")
    n_ops = get_count(ctx, "concurrent_ops")
    data = b"y" * 200
    for i in range(n_ops // 2 + n_ops % 2):
        fs.pipe(f"/bench/conc_mixed/read{i}.txt", data)

    def mixed_batch(thread_id, batch):
        for i, op_type in batch:
            if op_type == "write":
                fs.pipe(f"/bench/conc_mixed/write_t{thread_id}_{i}.txt", data)
            else:
                fs.cat(f"/bench/conc_mixed/read{i}.txt")

    all_ops = []
    for i in range(n_ops):
        if i % 2 == 0:
            all_ops.append((i // 2, "read"))
        else:
            all_ops.append((i // 2, "write"))

    batches = split_evenly(all_ops, n_threads)
    _, total_time = timed(lambda: _run_concurrent(mixed_batch, batches))

    return {
        "op": f"concurrent_mixed_{n_threads}t",
        "count": n_ops,
        "threads": n_threads,
        "times": [total_time / n_ops] * n_ops,
        "total_wall_s": total_time,
    }


def scenario_seeded_ls_root(fs, ctx):
    dataset = seed_dataset(fs, ctx)
    repeats = get_count(ctx, "seeded_repeats")
    times = []
    for _ in range(repeats):
        _, t = timed(lambda: fs.ls(SEEDED_ROOT, detail=False))
        times.append(t)
    return _seeded_result("seeded_ls_root", repeats, dataset, times)


def scenario_seeded_ls_deep(fs, ctx):
    dataset = seed_dataset(fs, ctx)
    repeats = get_count(ctx, "seeded_repeats")
    directory = dataset["seeded_directories"][ctx["seed"] % len(dataset["seeded_directories"])]
    times = []
    for _ in range(repeats):
        _, t = timed(lambda: fs.ls(directory, detail=False))
        times.append(t)
    result = _seeded_result("seeded_ls_deep", repeats, dataset, times)
    result["target_dir"] = directory
    return result


def scenario_seeded_exists(fs, ctx):
    dataset = seed_dataset(fs, ctx)
    repeats = get_count(ctx, "seeded_repeats")
    paths = _sample_paths(dataset["seeded_paths"], repeats, ctx["seed"])
    missing = [f"{path}.missing" for path in paths]
    checks = [item for pair in zip(paths, missing) for item in pair][:repeats]
    times = []
    for path in checks:
        _, t = timed(lambda p=path: fs.exists(p))
        times.append(t)
    result = _seeded_result("seeded_exists", repeats, dataset, times)
    result["hit_ratio"] = 0.5
    return result


def scenario_seeded_info(fs, ctx):
    dataset = seed_dataset(fs, ctx)
    repeats = get_count(ctx, "seeded_repeats")
    paths = _sample_paths(dataset["seeded_paths"], repeats, ctx["seed"])
    times = []
    for path in paths:
        _, t = timed(lambda p=path: fs.info(p))
        times.append(t)
    return _seeded_result("seeded_info", repeats, dataset, times)


def scenario_seeded_find(fs, ctx):
    dataset = seed_dataset(fs, ctx)
    repeats = get_count(ctx, "seeded_find_repeats")
    times = []
    for _ in range(repeats):
        _, t = timed(lambda: fs.find(SEEDED_ROOT, detail=False))
        times.append(t)
    return _seeded_result("seeded_find", repeats, dataset, times)


def _sample_paths(paths, count, seed):
    rng = random.Random(seed)
    if count <= len(paths):
        return rng.sample(paths, count)
    return [paths[i % len(paths)] for i in range(count)]


def _seeded_result(op, count, dataset, times):
    return {
        "op": op,
        "count": count,
        "seed_root": dataset["seed_root"],
        "seeded_files": dataset["seeded_files"],
        "seeded_dirs": dataset["seeded_dirs"],
        "times": times,
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
            f.result()


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
    "seeded_ls_root": scenario_seeded_ls_root,
    "seeded_ls_deep": scenario_seeded_ls_deep,
    "seeded_exists": scenario_seeded_exists,
    "seeded_info": scenario_seeded_info,
    "seeded_find": scenario_seeded_find,
}


def select_scenarios(scale, scenario=None):
    if scenario:
        return {scenario: ALL_SCENARIOS[scenario]}

    if scale == "ci":
        names = DEFAULT_CI_SCENARIOS
    else:
        names = DEFAULT_CI_SCENARIOS + SEEDED_SCENARIOS
    return {name: ALL_SCENARIOS[name] for name in names}


def summarize(result):
    """Add summary statistics to a result dict."""
    times = result["times"]
    if not times:
        raise ValueError("benchmark result has no timing samples")

    result["total_s"] = sum(times)
    result["avg_ms"] = statistics.mean(times) * 1000
    result["p50_ms"] = statistics.median(times) * 1000
    result["p95_ms"] = sorted(times)[int(len(times) * 0.95)] * 1000
    result["p99_ms"] = sorted(times)[int(len(times) * 0.99)] * 1000

    if "total_wall_s" in result:
        wall = result["total_wall_s"]
        result["ops_per_sec"] = len(times) / wall if wall > 0 else 0
    else:
        result["ops_per_sec"] = len(times) / sum(times) if sum(times) > 0 else 0

    del result["times"]
    return result


def run_benchmark(db_name, scenarios, ctx):
    """Run benchmark scenarios on a specific database."""
    print(f"\n{'=' * 60}")
    print(f"  Database: {db_name}")
    print(f"  Backend:  {ctx['backend']}")
    print(f"  Scale:    {ctx['scale']}  Seed: {ctx['seed']}")
    print(f"{'=' * 60}")

    call_command("migrate", verbosity=0)

    fs = DjangoFileSystem(namespace=DEFAULT_NAMESPACE_ID)
    results = []

    for name, func in scenarios.items():
        reset_db()
        print(f"\n  Running: {name} ...", end=" ", flush=True)
        try:
            result = func(fs, ctx)
            result = summarize(result)
            result = add_result_metadata(result, db_name, ctx)
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
            results.append(
                add_result_metadata(
                    {"op": name, "error": str(e)},
                    db_name,
                    ctx,
                )
            )

    reset_db()
    return results


def print_summary_table(all_results):
    """Print a comparison table across databases."""
    print(f"\n\n{'=' * 94}")
    print("  SUMMARY")
    print(f"{'=' * 94}")

    scales = sorted({r.get("scale", "?") for r in all_results})
    seeds = sorted({str(r.get("seed", "?")) for r in all_results})
    if len(scales) == 1 and len(seeds) == 1:
        print(f"\nScale: {scales[0]}  Seed: {seeds[0]}")

    ops = {}
    for r in all_results:
        op = r.get("op", "unknown")
        if op not in ops:
            ops[op] = {}
        ops[op][r.get("db", "?")] = r

    header = f"{'Operation':<24} {'Database':<18} {'Avg(ms)':>8} {'P95(ms)':>8} {'Ops/s':>8}"
    print(f"\n{header}")
    print("-" * len(header))

    for op, dbs in ops.items():
        for db, r in sorted(dbs.items()):
            if "error" in r:
                print(f"{op:<24} {db:<18} {'ERROR':>8}")
            else:
                print(
                    f"{op:<24} {db:<18} "
                    f"{r['avg_ms']:>8.2f} {r['p95_ms']:>8.2f} "
                    f"{r['ops_per_sec']:>8.0f}"
                )
        print()


def main():
    parser = argparse.ArgumentParser(description="django-fsspec benchmarks")
    parser.add_argument(
        "--scenario", type=str, default=None,
        choices=sorted(ALL_SCENARIOS.keys()),
        help="Run specific scenario",
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help="Benchmark result label. Actual backend comes from DJANGO_FSSPEC_BENCH_DB.",
    )
    parser.add_argument(
        "--json", type=str, default=None,
        help="Save results to JSON file",
    )
    parser.add_argument(
        "--scale", type=str, default="ci",
        choices=sorted(SCALES.keys()),
        help="Benchmark scale",
    )
    parser.add_argument(
        "--seed", type=int, default=1,
        help="Deterministic dataset seed",
    )
    args = parser.parse_args()

    scenarios = select_scenarios(args.scale, args.scenario)
    db_list = [args.db] if args.db else ["sqlite", "mysql", "postgres"]
    all_results = []

    if args.db:
        ctx = make_context(args.scale, args.seed)
        results = run_benchmark(args.db, scenarios, ctx)
        all_results.extend(results)
    else:
        import subprocess
        scenario_arg = ["--scenario", args.scenario] if args.scenario else []
        json_files = []
        for db in db_list:
            json_file = f"/tmp/bench_{db}_{args.scale}_seed_{args.seed}.json"
            json_files.append((db, json_file))
            env = os.environ.copy()
            env["DJANGO_FSSPEC_BENCH_DB"] = db
            result = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--db", db,
                    "--json", json_file,
                    "--scale", args.scale,
                    "--seed", str(args.seed),
                ] + scenario_arg,
                env=env, capture_output=True, text=True,
            )
            print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)

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
