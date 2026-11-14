from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from ingest.corners import Corner, load_corners, propose, write_corners
from ingest.track import TRACK_DIR, resample_closed, write_refline

LENGTH = 1000.0
S = np.arange(1000.0)


def lap(bends: list[tuple[float, float, float]], dips: list[tuple[float, float]], top=100.0):
    """`bends`（始点, 終点, 半径。正が左）の曲率と、`dips`（中心, 深さ [m/s]）で速度が落ちる周。

    速度はゆるやかに `top` [m] で最大になる（直線の終わり）。
    """
    kappa = np.zeros_like(S)
    for a, b, r in bends:
        kappa[(S >= a) & (S < b)] = 1 / r
    speed = 40.0 + 3 * np.cos(2 * np.pi * (S - top) / LENGTH)
    for c, depth in dips:
        speed -= depth * np.exp(-0.5 * ((S - c) / 40) ** 2)
    return S, speed, kappa


def test_propose_single_corner():
    corners, bends = propose(*lap([(400, 480, -40)], [(440, 15)]), LENGTH)
    assert bends == []
    [c] = corners
    assert c.id == "T1" and c.name == "T1（仮）"
    assert 430 <= c.s_apex <= 450
    assert c.r_apex == pytest.approx(-40, rel=0.05)
    # 始点は手前の速度の極大（s = 100 付近）の ENTRY_MARGIN 手前
    assert 75 <= c.s_start <= 85
    # 終点は曲率が抜けた所の EXIT_MARGIN 先
    assert 480 < c.s_end <= 510


def test_propose_rejects_start_before_timing_line():
    # 速度の極大が周の終わり（s = 990）にあると、T1 の始点は前の周に入る
    with pytest.raises(ValueError, match="計測線"):
        propose(*lap([(400, 480, -40)], [(440, 15)], top=990), LENGTH)


def test_propose_splits_s_curve_and_keeps_compound():
    corners, _ = propose(*lap(
        [
            (200, 250, 40), (250, 300, 25),  # 同じ向きで曲率だけ変わる複合コーナー
            (600, 650, 30), (650, 700, -30),  # 左から右への切り返し
        ],
        [(260, 15), (625, 12), (680, 12)],
    ), LENGTH)
    assert [c.id for c in corners] == ["T1", "T2", "T3"]
    t1, t2, t3 = corners
    assert t1.r_apex == pytest.approx(25, rel=0.1)
    assert t2.r_apex > 0 > t3.r_apex
    # S 字の 2 つは切れ目（間の速度の極大）で接する
    assert t2.s_end == t3.s_start
    assert 625 < t2.s_end < 680
    assert all(p.s_end <= q.s_start for p, q in pairwise(corners))


def test_propose_reports_gentle_bend():
    corners, bends = propose(*lap([(300, 380, -40), (600, 700, 250)], [(340, 15)]), LENGTH)
    assert len(corners) == 1
    [b] = bends
    assert 600 <= b.s_apex <= 700 and b.r_apex == pytest.approx(250, rel=0.05)


def test_propose_rejects_corner_across_timing_line():
    with pytest.raises(ValueError, match="計測線"):
        propose(*lap([(0, 40, -40), (960, 1000, -40)], [(0, 15)]), LENGTH)


def test_propose_rejects_null():
    s, speed, kappa = lap([(400, 480, -40)], [(440, 15)])
    kappa[10] = np.nan
    with pytest.raises(ValueError, match="kappa"):
        propose(s, speed, kappa, LENGTH)


@pytest.fixture
def course(tmp_path: Path) -> Path:
    th = np.linspace(0, 2 * np.pi, 400, endpoint=False)
    r = LENGTH / (2 * np.pi)
    write_refline(resample_closed(np.c_[r * np.cos(th), r * np.sin(th)]), tmp_path / "refline.csv",
                  "テスト用の円")
    return tmp_path


def write(course: Path, corners: list[Corner]) -> None:
    write_corners(corners, [], course / "corners.toml", "テスト")


def corner(i: int, a: float, b: float, apex: float | None = None) -> Corner:
    return Corner(f"T{i}", f"C{i}", a, b, apex if apex is not None else (a + b) / 2, -40.0)


def test_load_corners_roundtrip(course: Path):
    cs = [corner(1, 100, 200), corner(2, 200, 350), corner(3, 600, 990)]
    write(course, cs)
    assert load_corners(course) == cs


@pytest.mark.parametrize(
    ("corners", "match"),
    [
        ([corner(1, 300, 400), corner(2, 100, 200)], "前にある"),
        ([corner(1, 100, 250), corner(2, 200, 300)], "重なる"),
        ([corner(1, 900, 1000)], "収まらない"),
        ([corner(1, -5, 100, 50)], "収まらない"),
        ([corner(1, 300, 200, 250)], "収まらない"),
        ([corner(1, 100, 200, 250)], "区間の外"),
        ([corner(1, 100, 200), corner(1, 300, 400)], "重複"),
    ],
)
def test_load_corners_rejects(course: Path, corners: list[Corner], match: str):
    write(course, corners)
    with pytest.raises(ValueError, match=match):
        load_corners(course)


def test_load_corners_rejects_missing_key(course: Path):
    (course / "corners.toml").write_text('[[corner]]\nid = "T1"\nname = "a"\ns_start = 1\n')
    with pytest.raises(ValueError, match="s_end がない"):
        load_corners(course)


def test_load_corners_rejects_unknown_key(course: Path):
    (course / "corners.toml").write_text(
        '[[corner]]\nid = "T1"\nname = "a"\ns_start = 1\ns_end = 2\nstart = 1\n'
    )
    with pytest.raises(ValueError, match="未知のキー"):
        load_corners(course)


def test_load_corners_missing_file(course: Path):
    with pytest.raises(ValueError, match="corners.toml"):
        load_corners(course)


def test_okayama_corners_file():
    cs = load_corners(TRACK_DIR / "okayama")
    # 岡山の公称のコーナー数は 10 余り。全開で抜ける緩い曲がりは入れていない
    assert 8 <= len(cs) <= 14
    assert [c.id for c in cs] == [f"T{i + 1}" for i in range(len(cs))]
    assert all(c.name for c in cs)
    assert all(np.isfinite(c.r_apex) and abs(c.r_apex) < 150 for c in cs)
