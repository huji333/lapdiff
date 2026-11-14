from pathlib import Path

import numpy as np
import polars as pl
import pytest

from ingest.derive import by_distance
from ingest.track import TRACK_DIR, build_refline, lap_s, load_refline, project, resample_closed


def stadium(n=2000, gap=8.0):
    """直線 300 m の折り返し（ヘアピンの直線同士が gap [m] まで近づく）。"""
    r = gap / 2
    a = np.linspace(0, 1, n, endpoint=False)
    length = 2 * 300 + 2 * np.pi * r
    u = a * length
    pts = []
    for v in u:
        if v < 300:
            pts.append((v, -r))
        elif v < 300 + np.pi * r:
            th = (v - 300) / r - np.pi / 2
            pts.append((300 + r * np.cos(th), r * np.sin(th)))
        elif v < 600 + np.pi * r:
            pts.append((300 - (v - 300 - np.pi * r), r))
        else:
            th = (v - 600 - np.pi * r) / r + np.pi / 2
            pts.append((r * np.cos(th), r * np.sin(th)))
    return np.array(pts), length


def test_resample_closed_length():
    pts, length = stadium()
    line = resample_closed(pts, 1.0)
    assert line.length == pytest.approx(length, rel=1e-3)
    np.testing.assert_allclose(np.diff(line.s), line.length / len(line.s))


def test_project_does_not_jump_to_nearby_straight():
    # 直線同士が 8 m しか離れていなくても、横に 3 m ずれた走行は自分の直線に射影される
    pts, _ = stadium(gap=8.0)
    line = resample_closed(pts, 1.0)
    run = np.r_[pts[::5], pts[:200:5]] + [0, 0]
    run[:, 1] += np.where(run[:, 1] < 0, 3.0, -3.0) * (np.abs(run[:, 0] - 150) < 100)
    s, d = project(run, line)
    assert (np.diff(s) > 0).all()  # 周をまたいでも巻き戻らない
    assert s[-1] > line.length
    assert d.max() < 3.5


def test_lap_s_starts_negative_before_line():
    pts, _ = stadium()
    line = resample_closed(pts, 1.0)
    run = np.r_[line.xy[-3:], line.xy[:50]]
    s, _ = lap_s(run, line)
    assert s[0] < 0 and s[3] == pytest.approx(0, abs=0.01)


def test_build_refline_averages_noise():
    rng = np.random.default_rng(0)
    pts, length = stadium(gap=40.0)
    # 周ごとに通過の 0.05 s 後から 0.25 s 刻み（20 m/s で 5 m 刻み）で標本化する
    line0 = resample_closed(pts, 0.5)
    laps = []
    for _ in range(10):
        t = 0.05 + np.arange(0, line0.length / 20 - 0.1, 0.25)
        u = (t * 20) % line0.length
        xy = np.c_[np.interp(u, line0.s, line0.x), np.interp(u, line0.s, line0.y)]
        laps.append((t, xy + rng.normal(0, 1.0, xy.shape)))
    line = build_refline(laps)
    assert line.length == pytest.approx(length, rel=0.01)
    _, d = project(pts[::4], line)
    assert np.median(d) < 0.3
    # 原点は計測線（元の線の始点）。始点の外挿の誤差（10 周の平均で約 0.4 m）の範囲に収まる
    assert np.hypot(*(line.xy[0] - pts[0])) < 1.0


def test_by_distance_interpolates_time():
    pts, _ = stadium(gap=40.0)
    line = resample_closed(pts, 1.0)
    xy = line.xy[::5]
    t = line.s[::5] / 20  # 20 m/s
    timed = pl.DataFrame({"lap": 1, "t": t, "t_abs": t, "x": xy[:, 0], "y": xy[:, 1], "steer": None})
    d, dropped, _ = by_distance(timed, line)
    assert dropped == {"off_line": 0, "not_advancing": 0}
    np.testing.assert_allclose(d["t"].to_numpy(), d["s"].to_numpy() / 20, atol=1e-4)
    assert d["steer"].null_count() == d.height
    assert d["dist_norm"].max() < 1


def test_okayama_refline_file():
    line = load_refline(TRACK_DIR / "okayama")
    assert 3700 < line.length < 3720  # 実車のラップ長 3704〜3728 m（SPEC）
    assert line.s[0] == 0
    step = np.hypot(*np.diff(line.xy, axis=0).T)
    assert step.min() > 0.9 and step.max() < 1.1


def test_missing_refline_fails(tmp_path: Path):
    with pytest.raises(ValueError, match="refline.csv"):
        load_refline(tmp_path)


def test_by_distance_spans_timing_line():
    """前後の周の点を使い、計測線（s = 0）から全長の手前まで格子がそろう。"""
    pts, _ = stadium(gap=40.0)
    line = resample_closed(pts, 1.0)
    period = line.length / 20
    t_abs = np.arange(0.07, 3 * period, 0.1)  # 通過の 0.07 s 後から 10 Hz
    u = (t_abs * 20) % line.length
    lap = (t_abs // period).astype(int)
    timed = pl.DataFrame({
        "lap": lap, "t": t_abs - lap * period, "t_abs": t_abs,
        "x": np.interp(u, np.r_[line.s, line.length], np.r_[line.x, line.x[0]]),
        "y": np.interp(u, np.r_[line.s, line.length], np.r_[line.y, line.y[0]]),
    })
    d, _, stats = by_distance(timed, line)
    lap1 = d.filter(pl.col("lap") == 1)
    assert lap1["s"].min() == 0 and lap1["s"].max() == np.floor(line.length - 1e-9)
    np.testing.assert_allclose(lap1["t"].to_numpy(), lap1["s"].to_numpy() / 20, atol=1e-3)
    st = stats.filter(pl.col("lap") == 1)
    assert st["s_first"].item() < 0 and st["s_last"].item() > line.length
    assert st["max_gap_m"].item() == pytest.approx(2.0, abs=0.01)  # 20 m/s × 0.1 s
