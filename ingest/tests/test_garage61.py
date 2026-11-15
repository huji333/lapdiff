from pathlib import Path

import numpy as np
import polars as pl
import pytest

from ingest import garage61, ibt

ROOT = Path(__file__).resolve().parents[2]
BEST = ROOT / "fixtures/iracing/okayama_mx5cup_lap9_best.ibt"
COLUMNS = ["Speed", "LapDistPct", "Lat", "Lon", "Brake", "Throttle", "SteeringWheelAngle",
           "LatAccel", "LongAccel"]


@pytest.fixture(scope="module")
def csv(tmp_path_factory):
    """fixture の .ibt の 9 周目（113.172 s）を、Garage 61 の書き出しと同じ形にした CSV。

    Garage 61 と同じく、計測線を越えた直後の記録から、次の周の最初の記録までを置く。
    """
    r = ibt.read_ibt(BEST)
    lap = r.col("LapCompleted")
    pct = r.col("LapDistPct")
    i9 = np.flatnonzero(lap == 9)
    start = i9[0] + int(np.argmax(pct[i9] < 0.5))  # LapCompleted の更新は通過より少し遅れることがある
    end = start + int(np.argmax(np.diff(pct[start:]) < -0.5)) + 1
    rows = slice(start, end + 1)
    df = pl.DataFrame({c: r.col(c)[rows] for c in COLUMNS})
    d = tmp_path_factory.mktemp("g61")
    p = d / "Garage 61 - Test Driver - Global Mazda MX-5 Cup - Okayama (Full Course) - 01.53.172 - 01ABCDEFGHJK.csv"
    df.write_csv(p)
    return p


def test_parse_name(csv):
    info = garage61.parse_name(csv)
    assert info.driver == "Test Driver"
    assert info.lap_time == pytest.approx(113.172)
    assert info.session_id == "g61-Test_Driver-EFGHJK"
    with pytest.raises(ValueError, match="ファイル名"):
        garage61.parse_name(Path("lap.csv"))


def test_lap_starts_interpolates_line_crossing():
    pct = np.array([0.001, 0.4, 0.8, 0.98, 0.002])
    t = np.arange(5) / 60
    lap, starts = garage61.lap_starts(pct, t)
    assert lap.tolist() == [1, 1, 1, 1, 2]
    t1, t2 = starts["t_start"].to_list()
    assert t1 == pytest.approx(-0.001 / (0.399 * 60))
    assert t2 == pytest.approx((3 + 0.02 / 0.022) / 60)
    assert starts["start_seen"].to_list() == [True, True]


def test_ingest_matches_own_timing(csv, tmp_path):
    s = garage61.ingest(csv)
    assert s.course_dir.name == "okayama"
    lap1 = s.laps.filter(pl.col("lap") == 1).row(0, named=True)
    assert lap1["lap_valid"]
    assert lap1["lap_time"] == pytest.approx(113.172, abs=0.02)
    c = s.common.filter(pl.col("lap") == 1)
    assert c["source"].unique().to_list() == ["iracing"]
    # 先頭の記録が計測線を越えた直後なので、格子は s = 1 から始まる（端の余裕 COVER_TOL の中）
    assert c["s"].min() <= 2 and c["s"].max() >= 3700
    assert "garage61.LapDistPct" in s.raw.columns


def test_unknown_course_fails(csv):
    with pytest.raises(ValueError, match="iracing_transform.toml"):
        garage61.ingest(csv, course="nowhere")
