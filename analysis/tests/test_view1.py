import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from analysis import data
from analysis.charts import view1_chart
from analysis.tests.helpers import LENGTH, S, lap_rows, write
from ingest.track import TRACK_DIR, Refline, load_refline

REPO_DIR = Path(__file__).resolve().parents[2]


@pytest.mark.filterwarnings("ignore:Automatically deduplicated selection parameter")
def test_view1_chart_links_selections(db):
    labels = ["racechrono rc1 #1", "racechrono rc1 #2", "ac ac1 #2"]
    tel = data.time_delta(data.load_laps(db, labels), "racechrono rc1 #2")
    line = Refline(S.astype(float), S.astype(float), np.zeros(len(S)), LENGTH)
    spec = view1_chart(tel, line, "racechrono rc1 #2").to_dict()
    params = {p["name"]: p for p in spec["params"]}
    # 距離のグラフ 2 つが s_brush を共有し、コース図が map_brush を持つ
    assert params["s_brush"]["select"]["encodings"] == ["x"]
    assert {"lines_speed_kmh_0", "lines_dt_1"} <= set(params["s_brush"]["views"])
    assert params["map_brush"]["views"] == ["map_points_0"]


@pytest.mark.filterwarnings("ignore:Automatically deduplicated selection parameter")
def test_view1_chart_draws_corners(db):
    from ingest.corners import Corner

    tel = data.time_delta(data.load_laps(db, ["racechrono rc1 #2"]), "racechrono rc1 #2")
    line = Refline(S.astype(float), S.astype(float), np.zeros(len(S)), LENGTH)
    corners = [Corner("T1", "1コーナー", 10, 30, 20, -40), Corner("T2", "x", 50, 70, 60, 40)]
    with_c = view1_chart(tel, line, "racechrono rc1 #2", corners).to_dict()
    without = view1_chart(tel, line, "racechrono rc1 #2").to_dict()
    # 帯（速度・Δt）、id（速度）、コース図の id の 4 層が増える
    def n_layers(spec):
        return len(spec["hconcat"][0]["layer"]) + sum(
            len(v["layer"]) for v in spec["hconcat"][1]["vconcat"]
        )

    assert n_layers(with_c) - n_layers(without) == 4
    assert "T2" in str(with_c["datasets"])


def test_view1_notebook_runs(tmp_path):
    """実際の基準線の上に作った周で、ノートブックを script として最後まで実行する。"""
    line = load_refline(TRACK_DIR / "okayama")
    n = int(np.floor(line.length)) + 1
    s = np.arange(n, dtype=float)
    xy = (np.interp(s, line.s, line.x), np.interp(s, line.s, line.y))
    write(
        tmp_path,
        lap_rows("racechrono", "rc", 1, True, s, s / 30, line.length, xy),
        lap_rows("ac", "ac", 1, True, s, s / 31, line.length, xy),
    )
    env = {**os.environ, "LAPDIFF_DATA": str(tmp_path)}
    r = subprocess.run(
        [sys.executable, str(REPO_DIR / "analysis" / "view1.py")],
        env=env,
        capture_output=True,
        check=False,
        text=True,
        cwd=REPO_DIR,
    )
    assert r.returncode == 0, r.stderr
