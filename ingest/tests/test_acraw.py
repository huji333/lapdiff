import gzip
import json
import zlib
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from scipy.signal import savgol_filter

from ingest import acraw
from ingest.ac_transform import Similarity, ac_plane, fit_similarity, load_transform
from ingest.schema import COMMON_SCHEMA, JOIN_KEYS, write_session

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/ac/okayama_mx5nd_lap2.acraw.gz"
RACE_OUT = ROOT / "fixtures/ac/okayama_mx5nd_race_out.json"
N_RECORDS = 43292
REC = 8 + 4096 + 4096  # host_time_ns + physics + graphics（ブロック長はすべて 4096）
BODY = 48 + 4096  # ヘッダ + static


@pytest.fixture(scope="module")
def session():
    return acraw.ingest(FIXTURE)


@pytest.fixture(scope="module")
def head_bytes():
    """展開後の先頭（ヘッダ、static、レコード 2000 件）。小さいファイルを作るのに使う。"""
    d = zlib.decompressobj(wbits=31)
    return d.decompress(FIXTURE.read_bytes()[:2_000_000], BODY + 2000 * REC)


def write_gz(path: Path, data: bytes) -> Path:
    path.write_bytes(gzip.compress(data, compresslevel=1))
    return path


def test_header_and_static(session):
    c = session.capture
    assert c.format_version == 1
    assert c.collector_version == "dev"
    assert c.dtypes.sm_version == "1.7"
    assert c.static_str("acVersion") == "1.16.4"
    assert c.static_str("carModel") == "ks_mazda_mx5_nd"
    assert (c.static_str("track"), c.static_str("trackConfiguration")) == ("okayama", "circuit_gp")
    assert len(c.host_time_ns) == N_RECORDS
    assert not c.truncated and c.n_dropped_bytes == 0
    assert c.n_packet_gaps == 2  # fixtures/ac/README.md


def test_lap_times_match_race_out(session):
    race_out = json.loads(RACE_OUT.read_text())
    ac = {lap["lap"]: lap["time"] / 1000 for lap in race_out["sessions"][0]["laps"]}
    laps = session.laps
    assert laps["lap"].to_list() == [1, 2, 3]
    # 先頭の lap 1 は記録の途中から、lap 3 は終わらないうちに記録が切れている
    assert laps["closed"].to_list() == [False, True, False]
    assert laps["lap_valid"].to_list() == [False, True, False]
    assert laps["lap_time"].to_list()[0::2] == [None, None]
    lap2 = laps.filter(pl.col("lap") == 2)
    # 開始時刻を iCurrentTime で補正しているので、AC の計時と数 ms で一致する
    assert lap2["lap_time"].item() == pytest.approx(ac[2], abs=0.005)
    assert laps["lap_time_own"].to_list()[:2] == [ac[1], ac[2]]


def test_common_schema(session):
    c = session.common
    assert c.schema == COMMON_SCHEMA
    assert (c["source"] == "ac").all()
    assert c["session_id"].unique().to_list() == ["20261114T033002Z"]
    lap2 = c.filter(pl.col("lap") == 2)
    # 基準線の距離 1 m 刻みで 1 周分
    np.testing.assert_allclose(np.diff(lap2["s"].to_numpy()), 1.0)
    assert lap2["s"].min() <= 1 and lap2["s"].max() >= 3690
    assert lap2["t"].min() == pytest.approx(0, abs=0.2)
    assert lap2["t"].max() == pytest.approx(127.888, abs=0.3)
    assert lap2["lap_valid"].all()
    for col in ("steer", "throttle", "brake", "ax"):
        assert c[col].null_count() == 0
    assert c["throttle"].min() >= 0 and c["throttle"].max() <= 1


def test_raw_channels(session):
    r = session.raw
    assert r.columns[:4] == JOIN_KEYS
    assert r.height == N_RECORDS
    assert "physics.tyreContactPoint_3_2" in r.columns
    assert "graphics.carCoordinates_2" in r.columns
    assert "graphics.currentTime" not in r.columns  # wchar は除く
    first = r.filter(pl.col("lap") == 2)["t"].min()
    assert 0 < first < 0.003  # 開始時刻は通過の直前のレコードとの間


def test_derived_g_matches_ac_accelerometer(session):
    """位置と速度から求めた G が、AC 自身の accG と符号・大きさとも一致する。

    座標の向き（(x, -z) で右手系）、座標変換、派生チャンネルの計算をまとめて確かめる。
    accG[0] は左旋回で正（kappa と同じ向き）、accG[2] は加速で正。
    """
    c = session.timed.filter((pl.col("lap") == 2) & pl.col("ay").is_not_null())
    r = session.raw.filter(pl.col("lap") == 2)

    def acc(i):
        a = np.interp(c["t"].to_numpy(), r["t"].to_numpy(), r[f"physics.accG_{i}"].to_numpy())
        return savgol_filter(a, 15, 3)  # derive と同じ 1.5 s 窓

    for col, i in (("ay", 0), ("ax", 2)):
        y, x = c[col].to_numpy(), acc(i)
        assert np.corrcoef(x, y)[0, 1] > 0.99
        assert np.polyfit(x, y, 1)[0] == pytest.approx(1, abs=0.05)


def test_transformed_line_on_real_track(session):
    """変換後の AC の軌跡が、基準線から数 m 以内にある（ac_transform.toml の推定元）。"""
    from ingest.track import TRACK_DIR, lap_s, load_refline

    ac2 = session.timed.filter(pl.col("lap") == 2)
    _, dist = lap_s(np.c_[ac2["x"], ac2["y"]], load_refline(TRACK_DIR / "okayama"))
    assert np.median(dist) < 2
    assert dist.max() < 8


def test_write_session(session, tmp_path):
    paths = write_session(session.common, session.raw, tmp_path)
    assert paths[0] == tmp_path / "ac/20261114T033002Z/telemetry.parquet"
    assert pl.read_parquet(paths[0]).equals(session.common)


def test_fit_similarity_recovers_known_transform():
    th = np.linspace(0, 2 * np.pi, 400, endpoint=False)
    target = np.c_[300 * np.cos(th) + 40 * np.cos(3 * th), 150 * np.sin(th)]
    truth = Similarity(1.01, -90.0, 8.0, 115.0)
    # truth の逆変換で AC 側の点を作る
    r = np.radians(truth.rotation_deg)
    inv = np.array([[np.cos(r), np.sin(r)], [-np.sin(r), np.cos(r)]])
    src = (target - [truth.tx, truth.ty]) @ inv.T / truth.scale
    sim, dist = fit_similarity(src[::3], np.r_[target, target[:1]])
    assert sim.scale == pytest.approx(truth.scale, abs=1e-3)
    assert sim.rotation_deg == pytest.approx(truth.rotation_deg, abs=0.1)
    assert (sim.tx, sim.ty) == pytest.approx((truth.tx, truth.ty), abs=0.5)
    assert dist.max() < 0.5


def test_ac_plane_is_x_minus_z():
    np.testing.assert_array_equal(ac_plane(np.array([[1.0, 5.0, 2.0]])), [[1.0, -2.0]])


def test_no_points_dropped(session):
    assert session.dropped == {"off_line": 0, "not_advancing": 0}


def test_unknown_track_fails():
    with pytest.raises(ValueError, match="ac_transform.toml"):
        load_transform("okayama", "no_such_layout")


def test_part_file_reads_complete_records(head_bytes, tmp_path):
    # gzip の末尾がなく、最後のレコードが途中で切れたファイル
    full = gzip.compress(head_bytes[: BODY + 1500 * REC + 100], compresslevel=1)
    p = tmp_path / "ac_x.acraw.gz.part"
    p.write_bytes(full[:-200])
    c = acraw.read_acraw(p)
    assert c.truncated
    assert 0 < len(c.host_time_ns) < 1500
    assert 0 < c.n_dropped_bytes < REC


def test_truncated_non_part_fails(head_bytes, tmp_path):
    full = gzip.compress(head_bytes, compresslevel=1)
    p = tmp_path / "ac_x.acraw.gz"
    p.write_bytes(full[:-200])
    with pytest.raises(ValueError, match="末尾がない"):
        acraw.read_acraw(p)


def test_trailing_bytes_fail(head_bytes, tmp_path):
    p = write_gz(tmp_path / "x.acraw.gz", head_bytes + b"\0" * 10)
    with pytest.raises(ValueError, match="余り"):
        acraw.read_acraw(p)


def test_bad_magic_fails(head_bytes, tmp_path):
    p = write_gz(tmp_path / "x.acraw.gz", b"XCRAW" + head_bytes[5:])
    with pytest.raises(ValueError, match="magic"):
        acraw.read_acraw(p)


def test_unknown_format_fails(head_bytes, tmp_path):
    p = write_gz(tmp_path / "x.acraw.gz", head_bytes[:8] + b"\x02\0\0\0" + head_bytes[12:])
    with pytest.raises(ValueError, match="format_version"):
        acraw.read_acraw(p)


def test_completed_laps_decrease_fails(head_bytes, tmp_path):
    c = acraw.read_acraw(write_gz(tmp_path / "x.acraw.gz", head_bytes))
    g = c.graphics.copy()
    g["completedLaps"][1000:] -= 1
    with pytest.raises(ValueError, match="completedLaps"):
        acraw.lap_starts(acraw.AcCapture(**{**c.__dict__, "graphics": g}))


def test_pause_in_lap_invalidates_it(tmp_path):
    """周の途中でホスト時刻だけが 5 s 進む（ポーズ）と、AC の計時と合わず無効になる。"""
    d = zlib.decompressobj(wbits=31)
    data = bytearray(d.decompress(FIXTURE.read_bytes()))
    k = 20000  # lap 2 の途中のレコード
    rec = np.ndarray((N_RECORDS,), dtype="<i8", buffer=data, offset=BODY, strides=(REC,))
    rec[k:] += 5_000_000_000
    p = write_gz(tmp_path / "pause.acraw.gz", bytes(data))
    with pytest.warns(UserWarning, match="AC の計時"):
        s = acraw.ingest(p)
    lap2 = s.laps.filter(pl.col("lap") == 2)
    assert lap2["lap_time"].item() == pytest.approx(132.886, abs=0.01)
    assert not lap2["lap_valid"].item()
