import json

import numpy as np
import polars as pl
import pytest

from analysis import export
from analysis.tests.helpers import LENGTH, S, lap_rows, write
from ingest.track import Refline, write_refline


@pytest.fixture
def course_dir(tmp_path):
    d = tmp_path / "track" / "test"
    d.mkdir(parents=True)
    s = np.arange(101.0)
    write_refline(Refline(s, s, np.zeros(101), LENGTH), d / "refline.csv", "test")
    return d


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    write(
        d,
        lap_rows("racechrono", "rc1", 1, True, S, S / 10),
        lap_rows("racechrono", "rc1", 2, True, S, S / 20),
        # s = 10〜39 だけの断片
        lap_rows("racechrono", "rc1", 3, False, S[10:40], S[10:40] / 40),
        lap_rows("ac", "ac1", 2, True, S, S / 30),
    )
    return d


def laps_by_label(doc):
    return {lap["label"]: lap for lap in doc["laps"]}


def test_export_course(data_dir, course_dir):
    doc = export.export_course(data_dir, course_dir)
    assert doc["course"] == "test"
    assert doc["length"] == pytest.approx(LENGTH)
    assert doc["grid_max"] == 100
    assert len(doc["refline"]["s"]) == 101
    assert doc["corners"] is None
    assert doc["default_ref"] == "racechrono rc1 #2"

    laps = laps_by_label(doc)
    assert list(laps) == ["ac ac1 #2", "racechrono rc1 #1", "racechrono rc1 #2", "racechrono rc1 #3"]
    for lap in laps.values():
        for col in ("t", "speed_kmh", "x", "y", "ax", "ay"):
            assert len(lap[col]) == 101

    full = laps["racechrono rc1 #1"]
    assert full["complete"] and full["lap_valid"]
    assert full["t_end"] == pytest.approx(10.0)
    # 添字が s と一致する
    assert full["t"][37] == pytest.approx(3.7)
    assert full["x"][37] == pytest.approx(37.0)
    assert full["speed_kmh"][0] == pytest.approx(144.0)

    part = laps["racechrono rc1 #3"]
    assert not part["complete"] and not part["lap_valid"]
    assert part["t"][:10] == [None] * 10
    assert part["t"][40:] == [None] * 61
    assert part["t"][10] == pytest.approx(0.25)
    assert part["x"][39] == pytest.approx(39.0)


def test_export_g_null(tmp_path, course_dir):
    """行があって ax・ay が null の点（停止中など）は null のまま書く。"""
    lap = lap_rows("racechrono", "rc1", 1, True, S, S / 10).with_columns(
        ax=pl.when(pl.col("s") < 5).then(None).otherwise(0.123456),
        ay=pl.when(pl.col("s") < 5).then(None).otherwise(-0.5),
    )
    write(tmp_path / "data", lap)
    full = export.export_course(tmp_path / "data", course_dir)["laps"][0]
    assert full["ax"][:5] == [None] * 5
    assert full["ay"][:5] == [None] * 5
    assert full["ax"][5] == 0.123
    assert full["ay"][50] == -0.5
    assert full["t"][0] == 0.0


def test_export_corners(data_dir, course_dir):
    (course_dir / "corners.toml").write_text(
        '[[corner]]\nid = "T1"\nname = "一"\ns_start = 10\ns_end = 30\ns_apex = 20\n'
        '\n[[corner]]\nid = "T2"\nname = "二"\ns_start = 40\ns_end = 60\n',
        encoding="utf-8",
    )
    doc = export.export_course(data_dir, course_dir)
    assert doc["corners"] == [
        {"id": "T1", "name": "一", "s_start": 10.0, "s_apex": 20.0, "s_end": 30.0},
        {"id": "T2", "name": "二", "s_start": 40.0, "s_apex": None, "s_end": 60.0},
    ]


def test_export_mixed_length(tmp_path, data_dir, course_dir):
    # 別のコース（全長が違う）の周が混ざっている
    write(data_dir, lap_rows("racechrono", "other", 1, True, S, S / 10, length=200.5))
    with pytest.raises(ValueError, match="全長"):
        export.export_course(data_dir, course_dir)


@pytest.mark.parametrize(
    "s",
    [
        # 格子の整数でない s
        np.arange(10, 40) + 0.5,
        # grid_max（100）を超える s。全長は同じで、格子だけからはみ出す
        np.arange(90, 102),
    ],
)
def test_export_off_grid(data_dir, course_dir, s):
    write(data_dir, lap_rows("racechrono", "rc2", 1, False, s, s / 10))
    with pytest.raises(ValueError, match="格子"):
        export.export_course(data_dir, course_dir)


def test_export_duplicate_s(data_dir, course_dir):
    # 同じ周で s = 20 の行が 2 つある
    s = np.concatenate([S, [20]])
    write(data_dir, lap_rows("racechrono", "rc2", 1, True, s, s / 10))
    with pytest.raises(ValueError, match="重なる"):
        export.export_course(data_dir, course_dir)


def test_write_export(tmp_path, data_dir, course_dir):
    out = tmp_path / "out"
    (out).mkdir()
    (out / "other.json").write_text("{}", encoding="utf-8")
    path = export.write_export(export.export_course(data_dir, course_dir), out)
    assert path == out / "test.json"
    assert json.loads(path.read_text(encoding="utf-8"))["grid_max"] == 100
    assert json.loads((out / "index.json").read_text()) == {"courses": ["other", "test"]}
    doc = export.export_course(data_dir, course_dir)
    doc["laps"][0]["t"][0] = float("nan")
    with pytest.raises(ValueError):
        export.write_export(doc, out)


def test_export_offset(tmp_path, course_dir):
    """基準線は x 軸に沿って +x へ進む。y = +2 の周は左に 2 m ずれている。"""
    lap = lap_rows("racechrono", "rc1", 1, True, S, S / 10, xy=(S.astype(float), np.full(101, 2.0)))
    write(tmp_path / "data", lap)
    full = export.export_course(tmp_path / "data", course_dir)["laps"][0]
    assert full["offset"][50] == pytest.approx(2.0)
    assert full["corner_stats"] is None
    # CAN のない実車：ペダルは全部 null
    assert full["throttle"] == [None] * 101
    assert full["brake"] == [None] * 101


def test_export_pedals(tmp_path, course_dir):
    lap = lap_rows("ac", "ac1", 2, True, S, S / 30).with_columns(
        throttle=pl.Series(np.linspace(0, 1, 101)), brake=pl.Series(np.full(101, 0.25))
    )
    write(tmp_path / "data", lap)
    full = export.export_course(tmp_path / "data", course_dir)["laps"][0]
    assert full["throttle"][50] == pytest.approx(0.5)
    assert full["brake"][0] == pytest.approx(0.25)


def test_export_corner_stats(tmp_path, course_dir):
    (course_dir / "corners.toml").write_text(
        '[[corner]]\nid = "T1"\nname = "一"\ns_start = 10.5\ns_end = 30\n'
        '\n[[corner]]\nid = "T2"\nname = "二"\ns_start = 50\ns_end = 60\n',
        encoding="utf-8",
    )
    speed = np.full(101, 40.0)
    speed[20] = 20.0
    full = lap_rows("racechrono", "rc1", 1, True, S, S / 10).with_columns(speed=pl.Series(speed))
    # s = 0〜55 だけの断片：T2 の区間に欠けがある
    part = lap_rows("racechrono", "rc1", 2, False, S[:56], S[:56] / 10)
    write(tmp_path / "data", full, part)
    laps = laps_by_label(export.export_course(tmp_path / "data", course_dir))

    t1, t2 = laps["racechrono rc1 #1"]["corner_stats"]
    # 区間の格子は s = 11〜30
    assert t1 == {"id": "T1", "t_section": pytest.approx(1.9), "v_min": pytest.approx(72.0),
                  "s_vmin": 20, "v_entry": pytest.approx(144.0), "v_exit": pytest.approx(144.0),
                  "s_brake": None, "ax_min": 0.0}
    assert t2["t_section"] == pytest.approx(1.0)

    p1, p2 = laps["racechrono rc1 #2"]["corner_stats"]
    assert p1["t_section"] == pytest.approx(1.9)
    assert p2 == {"id": "T2", "t_section": None, "v_min": None, "s_vmin": None,
                  "v_entry": None, "v_exit": None, "s_brake": None, "ax_min": None}


@pytest.mark.parametrize(
    ("ax", "expected"),
    [
        # 0.1 → -0.2 → -0.5（最小）→ -0.4 → -0.1：-0.3 を下回り始めるのは添字 2
        ([0.1, -0.2, -0.5, -0.4, -0.1], (12, -0.5)),
        # 下回らない：ブレーキなし
        ([0.1, -0.2, -0.25, 0.0], (None, -0.25)),
        # 欠けがある
        ([0.1, None, -0.5], (None, None)),
        # 最小の手前で一度戻った所より前は含めない
        ([-0.5, 0.0, -0.4, -0.6], (12, -0.6)),
    ],
)
def test_brake_point(ax, expected):
    assert export.brake_point([None] * 10 + ax, 10, 10 + len(ax) - 1) == expected


def _corner(cid, a, b):
    from ingest.corners import Corner

    return Corner(cid, cid, a, b, float("nan"), float("nan"))


def test_sections_cover_lap():
    """コーナーの間の直線を足し、0〜grid_max を隙間なく覆う。10 m 以下の隙間は前のコーナーに含める。"""
    secs = export.sections([_corner("T1", 20, 40), _corner("T2", 45, 60), _corner("T3", 80, 95)], 100)
    assert [(s["id"], s["kind"], s["s_start"], s["s_end"]) for s in secs] == [
        ("→T1", "straight", 0, 20),
        ("T1", "corner", 20, 45),  # T1 と T2 の間の 5 m は T1 に含める
        ("T2", "corner", 45, 60),
        ("T2→T3", "straight", 60, 80),
        ("T3", "corner", 80, 100),  # 計測線までの 5 m も T3 に含める
    ]
    assert secs[0]["name"] == "計測線→T1"


def test_sections_short_start():
    """計測線の直後の短い隙間は、最初のコーナーの始点を 0 にして吸収する。最後の直線は計測線まで。"""
    secs = export.sections([_corner("T1", 5.5, 30)], 100)
    assert [(s["id"], s["s_start"], s["s_end"]) for s in secs] == [("T1", 0, 30), ("T1→", 30, 100)]
    assert secs[1]["name"] == "T1→計測線"


def test_export_section_times(tmp_path, course_dir):
    (course_dir / "corners.toml").write_text(
        '[[corner]]\nid = "T1"\nname = "一"\ns_start = 20\ns_end = 40\n',
        encoding="utf-8",
    )
    full = lap_rows("racechrono", "rc1", 1, True, S, S / 10)
    part = lap_rows("racechrono", "rc1", 2, False, S[:56], S[:56] / 10)
    write(tmp_path / "data", full, part)
    doc = export.export_course(tmp_path / "data", course_dir)
    assert [s["id"] for s in doc["sections"]] == ["→T1", "T1", "T1→"]
    assert doc["min_diff_s"] == export.MIN_DIFF_S
    laps = laps_by_label(doc)

    times = laps["racechrono rc1 #1"]["section_times"]
    assert times == [pytest.approx(2.0), pytest.approx(2.0), pytest.approx(6.0)]
    # 境界を共有するので、合計は周の t の始めから終わりまでと一致する
    t = laps["racechrono rc1 #1"]["t"]
    assert sum(times) == pytest.approx(t[100] - t[0])

    # s = 0〜55 だけの断片：最後の直線に欠けがある
    assert laps["racechrono rc1 #2"]["section_times"] == [pytest.approx(2.0), pytest.approx(2.0), None]


def test_export_no_sections(data_dir, course_dir):
    doc = export.export_course(data_dir, course_dir)
    assert doc["sections"] is None
    assert all(lap["section_times"] is None for lap in doc["laps"])


def write_edges_csv(course_dir, s, left, right, left_kerb=None, right_kerb=None):
    left_kerb = left if left_kerb is None else left_kerb
    right_kerb = right if right_kerb is None else right_kerb
    rows = "".join(f"{a},{b},{c},{d},{e}\n" for a, b, c, d, e in zip(s, left, right, left_kerb, right_kerb))
    head = "# test\n# attribution = 出典の表記\n# attribution_url = https://example.com/\n"
    (course_dir / "edges.csv").write_text(
        head + "s,left_m,right_m,left_kerb_m,right_kerb_m\n" + rows, encoding="utf-8")


def test_export_edges(data_dir, course_dir):
    """基準線は +x へ進むので、左の端は +y、右の端は -y に出る。"""
    s = np.arange(101.0)
    write_edges_csv(course_dir, s, np.full(101, 3.0), np.full(101, 4.0))
    doc = export.export_course(data_dir, course_dir)
    edges = doc["edges"]
    for side in ("left", "right"):
        for k in ("x", "y"):
            assert len(edges[side][k]) == len(doc["refline"]["s"])
            assert all(np.isfinite(edges[side][k]))
    assert edges["left"]["x"][50] == pytest.approx(50.0)
    assert edges["left"]["y"][50] == pytest.approx(3.0)
    assert edges["right"]["y"][50] == pytest.approx(-4.0)
    assert edges["attribution"] == "出典の表記"
    assert edges["kerbs"] == []
    json.dumps(doc, allow_nan=False)


def test_export_kerbs(data_dir, course_dir):
    """縁石のある所（左の 40〜49）だけが帯の多角形になる。路面の端を進み、縁石の外の線を戻る。"""
    s = np.arange(101.0)
    left, right = np.full(101, 3.0), np.full(101, 4.0)
    left_kerb = left.copy()
    left_kerb[40:50] = 5.0
    write_edges_csv(course_dir, s, left, right, left_kerb=left_kerb)
    doc = export.export_course(data_dir, course_dir)
    (kerb,) = doc["edges"]["kerbs"]
    assert kerb["side"] == "left"
    assert kerb["i"] == [*range(39, 51), *range(50, 38, -1)]
    assert kerb["y"][:12] == pytest.approx([3.0] * 12)
    assert kerb["y"][12:] == pytest.approx([3.0, *[5.0] * 10, 3.0])


def test_export_kerbs_wrap():
    # 先頭をまたぐ並びは 1 つにまとまる
    mask = np.zeros(10, dtype=bool)
    mask[[0, 1, 8, 9, 4]] = True
    got = [r.tolist() for r in export.runs(mask)]
    assert sorted(got) == sorted([[4], [8, 9, 0, 1]])


def test_export_edges_old_columns(data_dir, course_dir):
    s = np.arange(101.0)
    rows = "".join(f"{a},3,4\n" for a in s)
    head = "# attribution = a\n# attribution_url = b\n"
    (course_dir / "edges.csv").write_text(head + "s,left_m,right_m\n" + rows, encoding="utf-8")
    with pytest.raises(ValueError, match="列が"):
        export.export_course(data_dir, course_dir)


def test_export_edges_missing(data_dir, course_dir):
    assert export.export_course(data_dir, course_dir)["edges"] is None


def test_export_edges_grid_mismatch(data_dir, course_dir):
    s = np.arange(100.0)
    write_edges_csv(course_dir, s, np.full(100, 3.0), np.full(100, 4.0))
    with pytest.raises(ValueError, match="格子"):
        export.export_course(data_dir, course_dir)


def test_export_edges_nan(data_dir, course_dir):
    left = np.full(101, 3.0)
    left[7] = np.nan
    write_edges_csv(course_dir, np.arange(101.0), left, np.full(101, 4.0))
    with pytest.raises(ValueError, match="NaN"):
        export.export_course(data_dir, course_dir)


def test_export_edges_no_attribution(data_dir, course_dir):
    rows = "".join(f"{a},3,4,3,4\n" for a in range(101))
    (course_dir / "edges.csv").write_text("s,left_m,right_m,left_kerb_m,right_kerb_m\n" + rows, encoding="utf-8")
    with pytest.raises(ValueError, match="出典"):
        export.export_course(data_dir, course_dir)
