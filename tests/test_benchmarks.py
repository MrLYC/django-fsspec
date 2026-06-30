import argparse
import importlib

import pytest


bench = importlib.import_module("benchmarks.run")


def test_ci_scale_preserves_existing_counts():
    ci = bench.SCALES["ci"]

    assert ci["write_small_n"] == 1000
    assert ci["write_medium_n"] == 200
    assert ci["write_large_n"] == 50
    assert ci["read_small_n"] == 1000
    assert ci["read_large_n"] == 50
    assert ci["overwrite_n"] == 500
    assert ci["delete_n"] == 500
    assert ci["seek_read_n"] == 100
    assert ci["ls_flat_files"] == 1000
    assert ci["ls_flat_repeats"] == 100
    assert ci["ls_nested_dirs"] == 100
    assert ci["ls_nested_files_per_dir"] == 10
    assert ci["ls_nested_repeats"] == 100
    assert ci["concurrent_threads"] == 8
    assert ci["concurrent_files"] == 100
    assert ci["concurrent_ops"] == 200


def test_small_scale_bridges_ci_and_medium_seeded_counts():
    ci = bench.SCALES["ci"]
    small = bench.SCALES["small"]
    medium = bench.SCALES["medium"]

    assert small["seeded_files"] == 1000
    assert small["seeded_dirs"] == 50
    assert small["seeded_repeats"] == 100
    assert small["seeded_find_repeats"] == 5
    assert ci["seeded_files"] < small["seeded_files"] < medium["seeded_files"]


def test_build_seeded_paths_is_deterministic_and_seeded():
    paths = bench.build_seeded_paths(seed=1, file_count=20, dir_count=5)

    assert paths == bench.build_seeded_paths(seed=1, file_count=20, dir_count=5)
    assert paths != bench.build_seeded_paths(seed=2, file_count=20, dir_count=5)
    assert len(paths) == 20
    assert len({path.rsplit("/", 1)[0] for path in paths}) == 5


def test_select_scenarios_uses_seeded_scenarios_only_for_larger_default_scales():
    ci = bench.select_scenarios("ci")
    small = bench.select_scenarios("small")
    medium = bench.select_scenarios("medium")
    large = bench.select_scenarios("large")

    assert set(bench.SEEDED_SCENARIOS).isdisjoint(ci)
    assert set(bench.SEEDED_SCENARIOS).issubset(small)
    assert set(bench.SEEDED_SCENARIOS).issubset(medium)
    assert set(bench.SEEDED_SCENARIOS).issubset(large)


def test_select_scenarios_allows_explicit_seeded_ci_scenario():
    scenarios = bench.select_scenarios("ci", "seeded_exists")

    assert list(scenarios) == ["seeded_exists"]


def test_make_context_records_optional_block_size(monkeypatch):
    monkeypatch.setenv("DJANGO_FSSPEC_BENCH_DB", "mysql")

    ctx = bench.make_context("small", 7, 64 * 1024)

    assert ctx["backend"] == "mysql"
    assert ctx["scale"] == "small"
    assert ctx["seed"] == 7
    assert ctx["block_size"] == 64 * 1024


def test_make_context_rejects_invalid_block_size():
    with pytest.raises(ValueError):
        bench.make_context("small", 7, 0)


def test_positive_int_rejects_invalid_values():
    assert bench.positive_int("32768") == 32768
    with pytest.raises(argparse.ArgumentTypeError):
        bench.positive_int("0")
    with pytest.raises(argparse.ArgumentTypeError):
        bench.positive_int("not-an-int")


def test_summarize_computes_expected_fields():
    result = bench.summarize({"op": "example", "count": 4, "times": [0.1, 0.2, 0.3, 0.4]})

    assert result["total_s"] == 1.0
    assert result["avg_ms"] == 250.0
    assert result["p50_ms"] == 250.0
    assert result["p95_ms"] == 400.0
    assert result["p99_ms"] == 400.0
    assert result["ops_per_sec"] == 4.0
    assert "times" not in result


def test_add_result_metadata_includes_benchmark_context(monkeypatch):
    monkeypatch.setenv("DJANGO_FSSPEC_BENCH_DB", "postgres")
    ctx = bench.make_context("medium", 42, 128 * 1024)

    result = bench.add_result_metadata({"op": "example"}, "postgres-medium", ctx)

    assert result["db"] == "postgres-medium"
    assert result["backend"] == "postgres"
    assert result["scale"] == "medium"
    assert result["seed"] == 42
    assert result["block_size"] == 128 * 1024


def test_split_evenly_preserves_remainder_items():
    batches = bench.split_evenly(list(range(10)), 3)

    assert [item for batch in batches for item in batch] == [0, 3, 6, 9, 1, 4, 7, 2, 5, 8]
    assert sorted(item for batch in batches for item in batch) == list(range(10))
    assert [len(batch) for batch in batches] == [4, 3, 3]
