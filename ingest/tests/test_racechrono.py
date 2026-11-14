from pathlib import Path

import numpy as np
import polars as pl
import pytest

from ingest.racechrono import ingest, parse_csv, to_enu
from ingest.schema import COMMON_SCHEMA, JOIN_KEYS, write_session

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/racechrono/okayama_laps1-2.csv"
FULL = Path.home() / "Downloads/session_20261012_113238_okayama_international_v3.csv"
FIXTURE_ROWS = 6972  # ヘッダを除いたデータ行数（重複を含む）


@pytest.fixture(scope="module")
def session():
    return ingest(FIXTURE)


def rewrite(tmp_path, old, new, count=1):
    text = FIXTURE.read_text(encoding="utf-8")
    assert old in text
    p = tmp_path / "edited.csv"
    p.write_text(text.replace(old, new, count), encoding="utf-8")
    return p


def test_header_detection():
    rc = parse_csv(FIXTURE)
    assert rc.meta["Format"] == "3"
    assert rc.meta["Track name"] == "Okayama International"
    assert rc.gps_source == "100: gps"
    assert rc.units["speed|100: gps"] == "m/s"
    assert rc.data.width == 26


def test_header_not_fixed_to_line_numbers(tmp_path):
    p = rewrite(tmp_path, "Note,", "Note,\r\nExtra line,foo")
    assert parse_csv(p).data.columns == parse_csv(FIXTURE).data.columns


def test_unknown_format_fails(tmp_path):
    with pytest.raises(ValueError, match="Format"):
        parse_csv(rewrite(tmp_path, "Format,3", "Format,4"))


def test_missing_required_column_fails(tmp_path):
    with pytest.raises(ValueError, match="lap_number"):
        parse_csv(rewrite(tmp_path, ",lap_number,", ",lap_no,"))


def test_name_source_keys():
    cols = parse_csv(FIXTURE).data.columns
    for k in ["speed|100: gps", "speed|calc", "device_update_rate|100: gps",
              "device_update_rate|calc", "device_update_rate|101: acc",
              "distance_traveled|", "lateral_acc|calc"]:
        assert k in cols


def test_duplicate_timestamps_dropped(session):
    assert session.n_duplicates > 0
    assert session.raw.height + session.n_duplicates == FIXTURE_ROWS
    assert session.raw["timestamp|"].is_unique().all()


def test_lap_split(session):
    laps = session.laps
    assert laps["lap"].to_list() == [0, 1, 2, 3]
    # アウトラップは開始が計測線の通過ではないので閉じていない
    assert laps["closed"].to_list() == [False, True, True, False]
    assert laps.filter(pl.col("lap") == 2)["lap_time"].item() == pytest.approx(134.26, abs=0.01)
    # 各周の t は 0 から始まる
    first = session.raw.group_by("lap").agg(pl.col("t").min())
    assert (first["t"] == 0).all()
    assert (session.common["t"] >= 0).all()


def test_lap_valid(session):
    # アウトラップ、遅い周（lap 1 は lap 2 の 110%）、閉じていない周は false
    assert session.laps["lap_valid"].to_list() == [False, False, True, False]
    v = session.common.group_by("lap").agg(pl.col("lap_valid").unique())
    assert all(len(x) == 1 for x in v["lap_valid"])


def test_common_schema_and_roundtrip(session, tmp_path):
    c = session.common
    assert c.schema == COMMON_SCHEMA
    for col in ["steer", "throttle", "brake", "s", "dist_norm"]:
        assert c[col].null_count() == c.height
    assert (c["source"] == "racechrono").all()
    assert session.raw.columns[:4] == JOIN_KEYS
    p_common, p_raw = write_session(c, session.raw, tmp_path)
    assert p_common == tmp_path / "racechrono" / c["session_id"][0] / "telemetry.parquet"
    assert pl.read_parquet(p_common).schema == COMMON_SCHEMA
    assert pl.read_parquet(p_raw).equals(session.raw)


def test_write_rejects_wrong_schema(session, tmp_path):
    with pytest.raises(ValueError, match="共通スキーマ"):
        write_session(session.common.drop("steer"), session.raw, tmp_path)


def test_enu():
    x, y = to_enu(np.array([34.9145, 34.9145 + 1e-3]), np.array([134.2210 + 1e-3, 134.2210]),
                  34.9145, 134.2210)
    # 緯度 35° 付近の 0.001° は 東西 91.38 m、南北 110.94 m（WGS84 の曲率半径から手計算）
    np.testing.assert_allclose(x, [91.38, 0], atol=0.01)
    np.testing.assert_allclose(y, [0, 110.94], atol=0.01)


def test_derived_channels_plausible(session):
    lap = session.common.filter(pl.col("lap") == 2)
    np.testing.assert_allclose(np.diff(lap["t"].to_numpy()), 0.1, atol=1e-6)
    assert lap["ax"].null_count() == lap["ay"].null_count() == 0
    # ND ロードスター（街乗りタイヤ）として妥当な範囲
    assert lap["ax"].abs().max() < 1.3
    assert lap["ay"].abs().max() < 2.0
    assert 1.0 < lap["ay"].abs().quantile(0.99) < 1.5
    length = np.hypot(np.diff(lap["x"].to_numpy()), np.diff(lap["y"].to_numpy())).sum()
    assert 3690 < length < 3730


def test_ay_sign_matches_turn_direction(session):
    c = (
        session.common.drop_nulls("ay")
        .join(session.laps.select("lap", "t_start"), on="lap")
        .with_columns(t_abs=pl.col("t") + pl.col("t_start"))
        .sort("t_abs")
    )
    assert ((np.sign(c["ay"]) == np.sign(c["kappa"])) | (c["kappa"] == 0)).all()
    # GPS の方位（北から時計回り）が増えるのは右旋回なので、ay とは逆相関になる
    raw = session.raw.filter(pl.col("speed|100: gps") > 10)
    ts = raw["timestamp|"].to_numpy()
    yaw = np.gradient(np.unwrap(np.radians(raw["bearing|100: gps"].to_numpy())), ts)
    ay = np.interp(ts, c["t_abs"].to_numpy(), c["ay"].to_numpy())
    assert np.corrcoef(ay, yaw)[0, 1] < -0.7
    assert np.corrcoef(ay, raw["lateral_acc|calc"].to_numpy())[0, 1] > 0.9


@pytest.mark.skipif(not FULL.exists(), reason="実セッションの CSV がない")
def test_full_session():
    s = ingest(FULL)
    assert s.laps["lap"].to_list() == list(range(14))
    assert s.n_duplicates == 7210
    assert s.laps.filter("lap_valid")["lap"].to_list() == [2, 3, 4, 5, 6, 7, 9, 10, 11, 12]
    valid = s.common.filter("lap_valid").sort("lap", "t")
    lengths = valid.group_by("lap").agg(
        (pl.col("x").diff() ** 2 + pl.col("y").diff() ** 2).sqrt().sum().alias("m")
    )["m"]
    assert lengths.min() > 3690 and lengths.max() < 3730
