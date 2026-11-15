from pathlib import Path

import numpy as np
import polars as pl
import pytest

from ingest import ibt
from ingest.derive import derive
from ingest.schema import COMMON_SCHEMA, JOIN_KEYS, write_session

ROOT = Path(__file__).resolve().parents[2]
BEST = ROOT / "fixtures/iracing/okayama_mx5cup_lap9_best.ibt"
RESET = ROOT / "fixtures/iracing/okayama_mx5cup_reset.ibt"


@pytest.fixture(scope="module")
def best():
    return ibt.ingest(BEST)


@pytest.fixture(scope="module")
def reset():
    with pytest.warns(UserWarning, match="リセット"):
        return ibt.ingest(RESET)


def test_header_and_session_info(best):
    r = best.ibt
    assert r.tick_rate == 60
    assert len(r.records) == 7150  # fixtures/iracing/README.md
    assert (r.info("TrackName"), r.info("TrackConfigName")) == ("okayama full", "Full Course")
    assert r.vars["LatAccel"].unit == "m/s^2"
    assert best.n_garage == 0


def test_lap_time_matches_iracing(best):
    laps = best.laps
    assert laps["lap"].to_list() == [8, 9, 10]
    # 前後の周は記録の途中から始まる・途中で終わる
    assert laps["closed"].to_list() == [False, True, False]
    assert laps["lap_valid"].to_list() == [False, True, False]
    lap9 = laps.filter(pl.col("lap") == 9)
    assert lap9["lap_time_own"].item() == pytest.approx(113.172, abs=1e-3)
    assert lap9["lap_time"].item() == pytest.approx(113.172, abs=0.005)


def test_common_schema(best):
    c = best.common
    assert c.schema == COMMON_SCHEMA
    assert c["source"].unique().to_list() == ["iracing"]
    assert c["session_id"].unique().to_list() == ["20261114T165838Z"]
    lap9 = c.filter(pl.col("lap") == 9)
    assert lap9["s"].min() == 0 and lap9["s"].max() >= 3700
    assert lap9["steer"].null_count() == lap9.height  # 単位が決まるまで入れない
    # iRacing の値自体が float32 の丸めで 0〜1 をわずかに外れる（-2e-8、1.0000001）
    for col in ("throttle", "brake"):
        assert -1e-6 <= lap9[col].min() and lap9[col].max() <= 1 + 1e-6


def test_raw_channels(best):
    raw = best.raw
    assert raw.columns[:4] == JOIN_KEYS
    assert "ibt.LatAccel" in raw.columns
    assert raw.height == len(best.ibt.records)
    # 配列の変数は添字で展開する
    arrays = [v for v in best.ibt.vars.values() if v.count > 1]
    assert arrays and f"ibt.{arrays[0].name}_0" in raw.columns


def test_g_is_iracing_accel_without_kerb_spikes(best):
    """ax・ay は iRacing の加速度を平滑化したもの。縁石の跳ね（生で 2.11 G）は消え、位置から求めた値と向きがそろう。"""
    timed = best.timed.filter(pl.col("lap") == 9)
    raw_lat = best.raw.filter(pl.col("lap") == 9)["ibt.LatAccel"].to_numpy() / 9.80665
    assert np.abs(raw_lat).max() > 2.0
    ay = timed["ay"].to_numpy()
    assert 1.5 < np.nanmax(np.abs(ay)) < 1.8

    r = best.ibt
    t = r.col("SessionTime")
    _, xy = ibt.course_xy(r)
    d = derive(t, xy[:, 0], xy[:, 1], r.col("Speed").astype(np.float64), rate_hz=60)
    t9 = timed["t"].to_numpy() + best.laps.filter(pl.col("lap") == 9)["t_start"].item()
    for ours, theirs in (("ax", "ax"), ("ay", "ay")):
        a = timed[ours].to_numpy()
        b = np.interp(t9, d["t"].to_numpy(), d[theirs].to_numpy())
        ok = np.isfinite(a) & np.isfinite(b)
        assert np.corrcoef(a[ok], b[ok])[0, 1] > 0.97


def test_transformed_line_on_refline(best):
    # 相似変換で合わせたので、距離の格子に使わなかった点がない
    assert best.dropped == {"off_line": 0, "not_advancing": 0}


def test_write_session(best, tmp_path):
    common, raw = write_session(best.common, best.raw, tmp_path)
    assert common == tmp_path / "iracing" / "20261114T165838Z" / "telemetry.parquet"
    assert raw.exists()


def test_reset_keeps_lap_and_closes_nothing(reset):
    laps = reset.laps
    # リセットで LapCompleted が 1→0→1 となっても周は割れない
    assert laps["lap"].to_list() == [1, 2]
    assert not laps["closed"].any()
    # ピットレーンで越えた計測線のあとの周は、LapLastLapTime が -1 なので計時がない
    assert laps["lap_time_own"].null_count() == 2


def test_lap_records_reset_garage_and_jump():
    lr = ibt.lap_records(np.array([-1, -1, 0, 0, 1, 1, 0, 1, 2, 4, 4]))
    assert lr.lap.tolist() == [-1, -1, 0, 0, 1, 1, 1, 1, 2, 4, 4]
    assert lr.resets == [1]
    assert lr.broken_ends == [2]


def test_lap_records_outlap_zero_is_not_reset():
    # ガレージ（-1）から出たアウトラップの 0 はリセットではない
    lr = ibt.lap_records(np.array([-1, 0, 0, 1]))
    assert lr.resets == []


def test_lap_records_decrease_fails():
    with pytest.raises(ValueError, match="減った"):
        ibt.lap_records(np.array([3, 3, 2, 2]))


def test_lap_starts_uses_reset_of_current_lap_time():
    t = np.arange(8) * 0.5
    lap = np.array([0, 0, 1, 1, 1, 2, 2, 2])
    # 変化の記録ではまだ前の周の経過時間。1 記録あとに戻る。lap 2 では戻らないまま終わる
    cur = np.array([5.0, 5.5, 6.0, 0.2, 0.7, 1.2, 1.7, 2.2])
    starts, unknown = ibt.lap_starts(t, lap, cur)
    assert starts["t_start"].to_list() == pytest.approx([0.0, 1.5 - 0.2, 2.5])
    assert starts["start_seen"].to_list() == [False, True, False]
    assert unknown == [1, 2]


def test_own_lap_times_reads_first_update():
    lap = np.array([0, 0, 1, 1, 1, 2, 2])
    last = np.array([-1, -1, -1, -1, 80.0, 80.0, 80.0])  # lap 2 への通過では更新されないまま終わる
    own = ibt.own_lap_times(lap, last)
    assert own.rows() == [(0, 80.0), (1, None)]


def test_truncated_file_fails(tmp_path):
    p = tmp_path / "cut.ibt"
    p.write_bytes(BEST.read_bytes()[:-500])
    with pytest.raises(ValueError, match="書き終えていない"):
        ibt.read_ibt(p)


def _with_session_time(tmp_path, k: int, value_of: int) -> Path:
    """BEST の記録 k の SessionTime を記録 value_of の値に書き換えたファイル。"""
    b = bytearray(BEST.read_bytes())
    buf_len = ibt.HEADER.unpack_from(b, 0)[9]
    buf_off = ibt.VAR_BUF.unpack_from(b, 48)[1]
    off = ibt.read_ibt(BEST).vars["SessionTime"].offset
    src = buf_off + value_of * buf_len + off
    dst = buf_off + k * buf_len + off
    b[dst:dst + 8] = b[src:src + 8]
    p = tmp_path / "edited.ibt"
    p.write_bytes(bytes(b))
    return p


def test_repeated_session_time_drops_record(tmp_path):
    """実物では SessionTime が前の記録と同じ値になることがある。後ろの記録を落として読む。"""
    p = _with_session_time(tmp_path, 100, 99)
    with pytest.warns(UserWarning, match="1 件を落とした"):
        r = ibt.read_ibt(p)
    assert len(r.records) == 7149
    assert np.all(np.diff(r.col("SessionTime")) > 0)
    ticks = r.col("SessionTick")
    assert ticks[100] - ticks[99] == 2  # 落としたのは書き換えた記録


def test_session_time_going_back_fails(tmp_path):
    p = _with_session_time(tmp_path, 100, 98)
    with pytest.raises(ValueError, match="戻っている"):
        ibt.read_ibt(p)


def test_unknown_track_fails(tmp_path):
    with pytest.raises(ValueError, match="iracing_transform.toml"):
        ibt.ingest(BEST, track_dir=tmp_path)


def test_raw_laps_split_by_start_time(best):
    """生チャンネルも開始時刻で周に分ける。周の数が変わった記録とは 1〜2 記録ずれるが、t は周の中に収まる。"""
    raw9 = best.raw.filter(pl.col("lap") == 9)
    lap_time = best.laps.filter(pl.col("lap") == 9)["lap_time"].item()
    assert raw9["t"].min() >= 0 and raw9["t"].max() < lap_time
    # 1007.2333 s の記録は LapCompleted が 8 のままだが、開始時刻 1007.222 s より後なので lap 9
    assert raw9["ibt.LapCompleted"].to_list()[0] == 8


def test_reset_at_end_or_before_garage_is_restored():
    for completed in ([1, 2, 2, 0], [1, 2, 2, 0, -1, -1]):
        lr = ibt.lap_records(np.array(completed))
        assert lr.resets == [2]
        assert lr.lap.tolist()[:4] == [1, 2, 2, 2]


def test_empty_session_info_value(best):
    r = ibt.Ibt(**{**best.ibt.__dict__, "session_info": "WeekendInfo:\n TrackConfigName:\n"})
    assert r.info("TrackConfigName") == ""


def test_transform_toml_escapes_names(tmp_path):
    from ingest.ac_transform import Similarity, find_transform, transform_toml

    match = {"iracing_track": 'a "quoted" \\ name', "iracing_config": ""}
    d = tmp_path / "course"
    d.mkdir()
    (d / "x.toml").write_text(
        transform_toml("# h\n", match, Similarity(1, 0, 0, 0), np.zeros(3), "f.ibt"), encoding="utf-8"
    )
    assert find_transform("x.toml", match, tmp_path)[0] == d
