"""リポジトリの fixtures を取り込み、track/okayama の基準線・コーナー・コースの端で書き出す。

合成した直線では踏まない条件（T5/T6 が接している、T7/T8 が境界を共有している、縁石の並び）を通す。
`npm run data`（web/scripts/fixtures.sh）と同じ入力。
"""

import json
from itertools import pairwise
from pathlib import Path

import pytest

from analysis import export
from ingest import acraw, racechrono
from ingest.schema import write_session
from ingest.track import TRACK_DIR

ROOT = Path(__file__).resolve().parents[2]
RACECHRONO = sorted((ROOT / "fixtures/racechrono").glob("*.csv"))
ACRAW = ROOT / "fixtures/ac/okayama_mx5nd_lap2.acraw.gz"


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    for csv in RACECHRONO:
        s = racechrono.ingest(csv)
        write_session(s.common, s.raw, data_dir)
    s = acraw.ingest(ACRAW)
    write_session(s.common, s.raw, data_dir)
    return export.export_course(data_dir, TRACK_DIR / "okayama")


def test_laps(doc):
    assert len(RACECHRONO) == 2
    assert len(doc["laps"]) == 10
    assert sum(lap["complete"] for lap in doc["laps"]) == 4
    n = doc["grid_max"] + 1
    for lap in doc["laps"]:
        for ch in doc["channels"]:
            assert len(lap[ch]) == n, (lap["label"], ch)


def test_section_times_sum_to_lap(doc):
    """区間は境界を共有するので、欠けのない周では区間タイムの合計が周の t の始めから終わりまでと一致する。"""
    secs = doc["sections"]
    assert secs[0]["s_start"] == 0 and secs[-1]["s_end"] == doc["grid_max"]
    for a, b in pairwise(secs):
        assert a["s_end"] == b["s_start"]
    # complete の周でも両端が 5 m まで欠けうる（data.COMPLETE_GAP）。格子に欠けのない周で確かめる
    whole = [lap for lap in doc["laps"] if None not in lap["t"]]
    assert len(whole) == 3
    for lap in whole:
        assert lap["complete"]
        times = lap["section_times"]
        assert None not in times, lap["label"]
        t = lap["t"]
        assert t[doc["grid_max"]] == pytest.approx(lap["t_end"])
        # 区間ごとに 1e-3 s に丸めるので、合計は区間の数 × 0.5e-3 s までずれうる
        assert sum(times) == pytest.approx(t[doc["grid_max"]] - t[0], abs=0.5e-3 * len(times))


def test_corner_stats_by_id(doc):
    ids = [s["id"] for s in doc["sections"] if s["kind"] == "corner"]
    assert ids == [f"T{i}" for i in range(1, 11)]
    assert all(s["side"] in ("left", "right") for s in doc["sections"] if s["kind"] == "corner")
    for lap in doc["laps"]:
        assert list(lap["corner_stats"]) == ids


def test_kerbs_in_grid(doc):
    kerbs = doc["edges"]["kerbs"]
    assert kerbs
    n = len(doc["refline"]["s"])
    for k in kerbs:
        assert k["side"] in ("left", "right")
        assert len(k["i"]) == len(k["x"]) == len(k["y"])
        assert all(0 <= i < n for i in k["i"])


def test_json_has_no_nan(doc):
    json.dumps(doc, allow_nan=False)
