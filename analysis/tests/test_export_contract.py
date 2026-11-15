"""Python → JSON → TypeScript の契約：小さいコースの書き出しを `web/src/fixtures/contract.json` に置き、
pytest は今の出力がそれと一致することを、vitest（`web/src/data.test.ts`）はそれを読んで `checkCourse` と
写した処理（`analysis.export` の docstring の一覧）を確かめる。

書き出しの形を変えたら `LAPDIFF_UPDATE_GOLDEN=1 uv run pytest analysis/tests/test_export_contract.py` で
書き直し、差分を見てからコミットする。
"""

import json
import os
from pathlib import Path

import numpy as np
import polars as pl

from analysis import data, export
from analysis.tests.helpers import S, lap_rows, write
from analysis.tests.test_export import write_edges_csv
from ingest.track import Refline, write_refline

GOLDEN = Path(__file__).resolve().parents[2] / "web/src/fixtures/contract.json"


def contract(tmp_path: Path) -> dict:
    course_dir = tmp_path / "track" / "contract"
    course_dir.mkdir(parents=True)
    s = np.arange(101.0)
    write_refline(Refline(s, s, np.zeros(101), 100.5), course_dir / "refline.csv", "contract")
    # T1 と T2 の間の 5 m の隙間は T1 に含める。T2 は r_apex がない（向きは null）
    (course_dir / "corners.toml").write_text(
        '[[corner]]\nid = "T1"\nname = "一"\ns_start = 20\ns_end = 40\ns_apex = 30\nr_apex = -25\n'
        '\n[[corner]]\nid = "T2"\nname = "二"\ns_start = 45\ns_end = 70\n',
        encoding="utf-8",
    )
    left_kerb = np.full(101, 3.0)
    left_kerb[25:35] = 4.5
    write_edges_csv(course_dir, s, np.full(101, 3.0), np.full(101, 4.0), left_kerb=left_kerb)

    speed = 40 - 15 * np.exp(-(((S - 30) / 6.0) ** 2))
    ax = np.where((S >= 15) & (S < 30), -0.5, 0.1)
    rc1 = lap_rows("racechrono", "rc1", 1, True, S, S / 10).with_columns(
        speed=pl.Series(speed), ax=pl.Series(ax))
    rc2 = lap_rows("racechrono", "rc1", 2, True, S, S / 11 + 0.01 * np.sin(S / 7))
    frag = lap_rows("racechrono", "rc1", 3, False, S[10:50], S[10:50] / 12)
    ac = lap_rows("ac", "ac1", 1, True, S, S / 9).with_columns(
        throttle=pl.Series(np.linspace(0, 1, 101)), brake=pl.Series(np.full(101, 0.25)))
    data_dir = tmp_path / "data"
    write(data_dir, rc1, rc2, frag, ac)

    doc = export.export_course(data_dir, course_dir)
    con = data.open_db(data_dir)
    tel = data.time_delta(data.load_laps(con, data.lap_list(con)["label"].to_list()), doc["default_ref"])
    final = {r["label"]: {"s": int(r["s"]), "dt": round(r["dt"], 6)}
             for r in data.final_delta(tel).iter_rows(named=True)}
    # doc は書き出す JSON そのもの。expect は写した処理（TS）が出すべき値を Python で求めたもの
    return {"doc": doc, "expect": {"ref": doc["default_ref"], "final_delta": final}}


def dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=1, sort_keys=True) + "\n"


def test_contract(tmp_path):
    got = dump(contract(tmp_path))
    if os.environ.get("LAPDIFF_UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(got, encoding="utf-8")
    assert GOLDEN.exists(), f"{GOLDEN} がない（LAPDIFF_UPDATE_GOLDEN=1 で作る）"
    assert json.loads(GOLDEN.read_text(encoding="utf-8")) == json.loads(got), (
        f"書き出しが {GOLDEN} と違う。形を変えたなら LAPDIFF_UPDATE_GOLDEN=1 で書き直し、"
        "web の data.test.ts も通す"
    )
