"""web view（`web/`）が読む静的データを書き出す（SPEC「ビュー」のビュー 1）。

使い方：`uv run python -m analysis.export <data_dir> <course> <outdir>`

`<outdir>/<course>.json` を書き、`<outdir>/index.json`（`{"courses": [...]}`）を
`<outdir>` にあるコースの一覧で書き直す。

- 周の一覧と既定の基準ラップは `analysis.data` で求める。ここで同じ判定を書き直さない。
- 周の配列は `s` の格子（0〜`grid_max` の整数、1 m 刻み、`grid_max = floor(全長)`）の添字で並べる。
  その `s` に行がない周は null にする。
- 全周を書き出す（`lap_valid` が false の周も）。どの周を重ねるかは web view で選ぶ。
- タイム差 `dt` は書き出さない。web view が `t[i] - t_ref[i]` で求め、`data.time_delta` と同じく
  基準ラップと対象の周がどちらも `complete` のときだけ出す。基準ラップは web view で選ぶため、
  全組み合わせを先に計算すると周の数の 2 乗の大きさになる。
  `t` は 1e-3 s に丸めて書くので、web view の `dt` は `data.time_delta` と最大 1e-3 s ずれる
  （小数点以下 3 桁で表示すると最後の桁が違うことがある）。
- web view（`web/src/data.ts`）には、Python の判定を写した処理がほかにもある。
  `data.final_delta`（`finalDelta`）と、`analysis/view1.py` の周の絞り込み
  （一覧に出す周 `shownLaps`、基準ラップの候補 `refCandidates`、既定で重ねる周 `defaultSelection`）。
  どちらかを変えたら、もう片方も合わせる。
- `throttle`・`brake`：直接計測したペダル（0〜1）。AC にはあり、CAN のない実車では全部 null。
- `offset` [m]：同じ `s` での基準線からの横のずれ。進行方向の左を正にする（`ay` と同じ向き）。
  基準線は実車の有効な周を平均した線で、コースの中心でも縁でもない。ライン取りの差を比べるための原点。
- `corner_stats`：周ごと・コーナーごと（`corners` と同じ順）の区間タイム、ボトム速度とその位置、
  区間の始点と終点の速度。区間の格子に 1 点でも欠けがあれば、そのコーナーの値は全部 null にする
  （欠けた所の値を補わない。SPEC 原則 6）。
- ブレーキ開始点 `s_brake` と最大減速度 `ax_min`（SPEC「コーナーごとの指標」の GPS のみでの出し方）：
  前のコーナーの終点（なければ計測線）と、区間の始点の `BRAKE_LOOKBACK` m 手前のうち後の方から、
  ボトムの位置までを探す。`ax_min` はその範囲の `ax` の最小値。`ax_min` が `-BRAKE_G` を下回れば、
  その点から手前へ `ax < -BRAKE_G` が続く最初の点を `s_brake` にする。下回らなければ `s_brake` は null
  （ブレーキなし。`ax_min` は出す）。範囲の `ax` に欠けがあれば両方 null。
  `BRAKE_G` は仮の値（SPEC「未決事項」）で、web view が線を引けるよう `brake_g` として書き出す。
  2026-10-12 の実車では、-0.2 G と -0.3 G で開始点が 30 m 以上動くコーナーがあった。AC では 5 m 以内だった。
- 加速を始める点は出さない。`ax` が正に転じる点は、どの周でもボトムの位置から 1〜8 m 先で、
  `ax` からはボトムの位置と区別できない。実際のアクセルはもっと早い。2026-11-14 の AC では、
  `throttle` が 50% を超える点がボトムより 40〜90 m 手前のコーナーがあった（T2、T3、T4、T7）。
  `throttle` のある周（AC、CAN のある実車）では、ペダルから出す。
- データの場所に別のコースの周が混ざっていれば `data.check_length` で失敗させる。
  `s` が格子の整数でない行、同じ周で `s` が重なる行、NaN も失敗させる。
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import polars as pl

from analysis import data
from ingest.corners import load_corners
from ingest.track import TRACK_DIR, load_refline

# 丸めの桁（小数点以下）。JSON の大きさを抑える
BRAKE_G = 0.3
BRAKE_LOOKBACK = 250.0
DIGITS = {"t": 3, "speed_kmh": 2, "x": 2, "y": 2, "ax": 3, "ay": 3, "offset": 2, "throttle": 3, "brake": 3}
LAP_META = ["label", "source", "session_id", "lap", "lap_valid", "complete", "t_end"]


def _column(
    idx: list[int], values: list[float | None], digits: int, n: int
) -> list[float | None]:
    # ax・ay は停止中や短い区間で null になる（ingest.derive）。行があっても null のまま書く
    out: list[float | None] = [None] * n
    for i, v in zip(idx, values):
        out[i] = None if v is None else round(v, digits)
    return out


def lateral_offset(line, s: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """点 (x, y) の、基準線上の距離 `s` の点からの横のずれ [m]。進行方向の左が正。"""
    def at(v: np.ndarray, col: np.ndarray) -> np.ndarray:
        # 基準線は閉じているので、全長を周期として補間する
        return np.interp(v % line.length, line.s, col, period=line.length)

    rx, ry = at(s, line.x), at(s, line.y)
    # 接線は前後 1 m の点の差
    tx = at(s + 1.0, line.x) - at(s - 1.0, line.x)
    ty = at(s + 1.0, line.y) - at(s - 1.0, line.y)
    norm = np.hypot(tx, ty)
    return ((y - ry) * tx - (x - rx) * ty) / norm


def brake_point(ax: list[float | None], lo: int, hi: int) -> tuple[int | None, float | None]:
    """`ax[lo..hi]` での (`s_brake`, `ax_min`)。決め方はモジュールの docstring。"""
    seg = ax[lo : hi + 1]
    if not seg or any(v is None for v in seg):
        return None, None
    k = min(range(len(seg)), key=seg.__getitem__)
    ax_min = seg[k]
    if ax_min >= -BRAKE_G:
        return None, ax_min
    while k > 0 and seg[k - 1] < -BRAKE_G:
        k -= 1
    return lo + k, ax_min


def corner_stats(lap: dict, corners) -> list[dict]:
    """`lap` は書き出す形の周（s の格子の添字で並んだ配列）。"""
    out = []
    prev_end = 0.0
    for c in corners:
        a, b = math.ceil(c.s_start), math.floor(c.s_end)
        t = lap["t"][a : b + 1]
        v = lap["speed_kmh"][a : b + 1]
        stat = {"id": c.id, "t_section": None, "v_min": None, "s_vmin": None,
                "v_entry": None, "v_exit": None, "s_brake": None, "ax_min": None}
        if all(x is not None for x in t) and all(x is not None for x in v):
            k = min(range(len(v)), key=v.__getitem__)
            lo = math.ceil(max(prev_end, c.s_start - BRAKE_LOOKBACK))
            s_brake, ax_min = brake_point(lap["ax"], lo, a + k)
            stat.update(
                t_section=round(t[-1] - t[0], DIGITS["t"]),
                v_min=v[k], s_vmin=a + k, v_entry=v[0], v_exit=v[-1],
                s_brake=s_brake, ax_min=ax_min,
            )
        out.append(stat)
        prev_end = c.s_end
    return out


def export_course(data_dir: Path, course_dir: Path) -> dict:
    """`data_dir` の全周を、`course_dir` の基準線とコーナーに合わせて書き出す形にする。"""
    line = load_refline(course_dir)
    grid_max = math.floor(line.length)
    n = grid_max + 1
    # corners.toml はコースによってはまだない。ないときは null にして、web view は帯を描かない
    has_corners = (course_dir / "corners.toml").exists()
    corners = load_corners(course_dir) if has_corners else None

    con = data.open_db(data_dir)
    laps = data.lap_list(con)
    data.check_length(laps, line.length)
    tel = data.load_laps(con, laps["label"].to_list()).select(
        "label", "s", "t", pl.col("speed") * 3.6, "x", "y", "ax", "ay", "throttle", "brake"
    ).rename({"speed": "speed_kmh"})
    tel = tel.with_columns(offset=pl.Series(lateral_offset(
        line, tel["s"].to_numpy(), tel["x"].to_numpy(), tel["y"].to_numpy())))

    s = pl.col("s")
    bad = tel.filter((s != s.round()) | (s < 0) | (s > grid_max))
    if bad.height:
        raise ValueError(f"s が 0〜{grid_max} の整数の格子にない行がある:\n{bad.head()}")
    dup = tel.filter(pl.struct("label", "s").is_duplicated())
    if dup.height:
        raise ValueError(f"同じ周で s が重なる行がある:\n{dup.head()}")

    by_label = {k[0]: g for k, g in tel.group_by("label")}
    out_laps = []
    for meta in laps.select(LAP_META).iter_rows(named=True):
        g = by_label[meta["label"]]
        idx = g["s"].cast(pl.Int64).to_list()
        meta["t_end"] = round(meta["t_end"], DIGITS["t"])
        for col, digits in DIGITS.items():
            meta[col] = _column(idx, g[col].to_list(), digits, n)
        meta["corner_stats"] = None if corners is None else corner_stats(meta, corners)
        out_laps.append(meta)

    return {
        "course": course_dir.name,
        "length": line.length,
        "grid_max": grid_max,
        "refline": {
            "s": [round(float(v), 3) for v in line.s],
            "x": [round(float(v), 2) for v in line.x],
            "y": [round(float(v), 2) for v in line.y],
        },
        "corners": None if corners is None else [
            {
                "id": c.id,
                "name": c.name,
                "s_start": c.s_start,
                "s_apex": None if math.isnan(c.s_apex) else c.s_apex,
                "s_end": c.s_end,
            }
            for c in corners
        ],
        "default_ref": data.default_ref(laps),
        "brake_g": BRAKE_G,
        "laps": out_laps,
    }


def write_export(doc: dict, outdir: Path) -> Path:
    """`<outdir>/<course>.json` を書き、`index.json` を書き直す。NaN があれば失敗させる。"""
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{doc['course']}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
                    encoding="utf-8")
    courses = sorted(p.stem for p in outdir.glob("*.json") if p.name != "index.json")
    (outdir / "index.json").write_text(json.dumps({"courses": courses}) + "\n", encoding="utf-8")
    return path


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: python -m analysis.export <data_dir> <course> <outdir>")
    data_dir, course, outdir = Path(argv[0]), argv[1], Path(argv[2])
    doc = export_course(data_dir, TRACK_DIR / course)
    path = write_export(doc, outdir)
    print(f"{path}: {len(doc['laps'])} 周、{path.stat().st_size / 1e6:.1f} MB、"
          f"既定の基準ラップ {doc['default_ref']}")


if __name__ == "__main__":
    main(sys.argv[1:])
