import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("duckdb")


def test_duckdb_parquet_example():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "examples" / "duckdb_parquet.py")],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Beijing" in completed.stdout
    assert "Shanghai" in completed.stdout
    assert "Shenzhen" in completed.stdout
    assert "total=37.25" in completed.stdout
