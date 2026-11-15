import os
import shutil
from pathlib import Path

import pytest

from ingest import iracing_dir

ROOT = Path(__file__).resolve().parents[2]
RESET = ROOT / "fixtures/iracing/okayama_mx5cup_reset.ibt"


@pytest.fixture
def telemetry(tmp_path):
    d = tmp_path / "telemetry"
    d.mkdir()
    shutil.copy(RESET, d / "a.ibt")
    (d / "broken.ibt").write_bytes(RESET.read_bytes()[:-500])
    return d


def _age(path: Path, seconds: float) -> None:
    st = path.stat()
    os.utime(path, (st.st_atime - seconds, st.st_mtime - seconds))


def test_scan_ingests_once_and_records_failures(telemetry, tmp_path):
    data = tmp_path / "data"
    for p in telemetry.iterdir():
        _age(p, 60)
    state = iracing_dir.State.load(data)
    assert iracing_dir.scan(iracing_dir.find_files(telemetry, None), data, 30, state, set())
    assert (data / "iracing/20261114T164912Z/telemetry.parquet").exists()
    files = iracing_dir.State.load(data).files  # 保存した状態を読み直す
    assert files["a.ibt"]["course"] == "okayama"
    assert "書き終えていない" in files["broken.ibt"]["error"]
    # 変わっていなければ取り込み直さない（失敗したファイルも繰り返さない）
    assert not iracing_dir.scan(iracing_dir.find_files(telemetry, None), data, 30, iracing_dir.State.load(data), set())


def test_scan_defers_recent_files(telemetry, tmp_path):
    data = tmp_path / "data"
    state = iracing_dir.State.load(data)
    pending: set[str] = set()
    assert not iracing_dir.scan(iracing_dir.find_files(telemetry, None), data, 30, state, pending)
    assert pending == {"a.ibt", "broken.ibt"}
    assert state.files == {}
