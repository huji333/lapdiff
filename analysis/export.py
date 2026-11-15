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
- web view（`web/src/data.ts`）に写した Python の処理の一覧（ここだけに書く）。どちらかを変えたら、もう片方も合わせる。
  - `timeDelta`・`finalDelta`：`data.time_delta`・`data.final_delta`。
  - `sectorTimes`：`section_times` と同じ求め方を、任意の境界（50 m のミニセクター）で行う。
    基準ラップには依存せず、自己ベストとの比較（コース図の色）に使う。
  - `shownLaps`・`refCandidates`・`defaultSelection`：`analysis/view1.py` の周の絞り込み
    （一覧に出す周、基準ラップの候補、既定で重ねる周）。
  - `SCHEMA` と、`checkCourse` が確かめるチャンネルの一覧（`CHANNELS`）：ここの `SCHEMA` と `DIGITS` のキー。
  `web/src/fixtures/contract.json` は小さいコースの書き出しで、pytest（`test_export_contract.py`）が今の出力と一致することを、
  vitest（`data.test.ts`）がそれを読んで `checkCourse` と写した処理の結果を確かめる。
- `schema`：書き出す形の版 `SCHEMA`。web view は版が違えば「npm run data で書き出し直す」と失敗させる。
- `channels`：周ごとの配列のキー（`DIGITS` のキーの並び）。web view が周のキーと長さを確かめるのに使う。
- `group`：周ごとの比べる組（`data.GROUPS`。実車と AC は `real`、iRacing は `iracing`）。web view は
  比較の周と重ねる周を基準ラップと同じ組から選び、`source` の値から組を判定しない。
- `throttle`・`brake`：直接計測したペダル（0〜1）。AC にはあり、CAN のない実車では全部 null。
- `offset` [m]：同じ `s` での基準線からの横のずれ。進行方向の左を正にする（`ay` と同じ向き）。
  基準線は実車の有効な周を平均した線で、コースの中心でも縁でもない。ライン取りの差を比べるための原点。
- `corner_stats`：周ごと・コーナーごとのボトム速度とその位置、区間の始点と終点の速度、ブレーキ開始点と最大減速度。
  コーナーの `id` をキーにした object。区間は `sections` の `kind = "corner"` の区間で、コーナーの区間タイムは
  `section_times` のその区間の値だけにする（同じコーナーに 2 つの秒数を出さない）。
  区間の格子に 1 点でも欠けがあれば、そのコーナーの値は全部 null にする（欠けた所の値を補わない。SPEC 原則 6）。
- ブレーキ開始点 `s_brake` と最大減速度 `ax_min`（SPEC「コーナーごとの指標」の GPS のみでの出し方）：
  前のコーナーの区間の終点（なければ計測線）と、区間の始点の `BRAKE_LOOKBACK` m 手前のうち後の方から、
  ボトムの位置までを探す。前のコーナーの終点は延ばした終点（`sections`）なので、間の隙間が `SECTION_MERGE` m
  以下なら下限はこの区間の始点になり、その隙間で踏み始めた周の `s_brake` は始点に張り付く。
  `ax_min` はその範囲の `ax` の最小値。`ax_min` が `-BRAKE_G` を下回れば、
  その点から手前へ `ax < -BRAKE_G` が続く最初の点を `s_brake` にする。下回らなければ `s_brake` は null
  （ブレーキなし。`ax_min` は出す）。範囲の `ax` に欠けがあれば両方 null。
  `BRAKE_G` は仮の値（SPEC「未決事項」）で、web view が線を引けるよう `brake_g` として書き出す。
  2026-10-12 の実車では、-0.2 G と -0.3 G で開始点が 30 m 以上動くコーナーがあった。AC では 5 m 以内だった。
- 加速を始める点は出さない。`ax` が正に転じる点は、どの周でもボトムの位置から 1〜8 m 先で、
  `ax` からはボトムの位置と区別できない。実際のアクセルはもっと早い。2026-11-14 の AC では、
  `throttle` が 50% を超える点がボトムより 40〜90 m 手前のコーナーがあった（T2、T3、T4、T7）。
  `throttle` のある周（AC、CAN のある実車）では、ペダルから出す。
- `sections`：1 周を隙間なく覆う区間。`corners.toml` のコーナーと、その間の直線（計測線の前後を含む）。
  境界は `s` の格子の整数で、前の区間の終点と次の区間の始点が同じ点になる。
  長さが `SECTION_MERGE` m 以下の隙間は直線にせず、前のコーナーの終点を延ばして吸収する（計測線の直後なら
  最初のコーナーの始点を 0 にする）。区間ごとの損失を順位にするとき、数 m の区間が並ぶのを避ける。
  コーナーの区間（`kind = "corner"`）はコーナーの定義を兼ね、`s_apex`（なければ null）と `side` を持つ。
  `side` は `corners.toml` の `r_apex` の符号から決めた向き（`"left"`／`"right"`、`r_apex` がなければ null）。
  コーナーの境界は `corners.toml` の値ではなく、この延ばした境界だけを書き出す。
- `section_times`：周ごと・区間ごと（`sections` と同じ順）の区間タイム `t[s_end] - t[s_start]`。
  区間の格子に 1 点でも欠けがあれば null。境界を共有するので、全区間の合計は `t[grid_max] - t[0]` に一致する。
  区間ごとの自己ベストは web view が `section_times` の最小から求める（書き出さない）。
- `min_diff_s`：区間タイムの差をこれ未満なら「差なし」とみなすしきい値 [s]（仮の値）。web view が読む。
- `edges`：コースの端（`track/<course>/edges.csv`、`ingest.edges` が空中写真から作る）を、基準線の格子の各 `s` で
  左右の端の点 `{"left": {"x", "y"}, "right": {"x", "y"}, "kerbs", "attribution", "attribution_url"}` にしたもの。
  出典の表記は `edges.csv` の頭の `# attribution = ...` をそのまま渡す。`edges.csv` がなければ null。
  端は白線の外側（走路の限界）で、写真と GPS の位置合わせの誤差で ±1〜2 m ずれうる。
  `kerbs` は路面の端より外にある縁石（赤い縁石）の帯の多角形の並び `[{"side", "i", "x", "y"}]`。
  路面の端を格子の添字の順に進み、縁石の外の線を戻る。`i` は各点の基準線の格子の添字で、範囲を切り出すのに使う。
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
from ingest.cli import utf8_stdio
from ingest.corners import load_corners
from ingest.track import TRACK_DIR, frame, load_refline

# 書き出す形の版。形を変えたら上げ、web/src/data.ts の SCHEMA も同じ値にする
SCHEMA = 2
BRAKE_G = 0.3
BRAKE_LOOKBACK = 250.0
# 周ごとのチャンネルと丸めの桁（小数点以下）。JSON の大きさを抑える。キーの並びを `channels` として書き出す
DIGITS = {"t": 3, "speed_kmh": 2, "x": 2, "y": 2, "ax": 3, "ay": 3, "offset": 2, "throttle": 3, "brake": 3}
# 区間タイムの差を「差なし」とみなすしきい値 [s]。実車の GPS では低速コーナーの区間タイムが 0.05〜0.1 s ぶれる
MIN_DIFF_S = 0.05
# これ以下の長さ [m] の隙間は直線の区間にせず、前のコーナーに含める
SECTION_MERGE = 10
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
    rx, ry, nx, ny = frame(line, s)
    return (x - rx) * nx + (y - ry) * ny


def edges(line, course_dir: Path) -> dict | None:
    """`edges.csv` の左右の端を、基準線の格子の点 (x, y) にする。ファイルがなければ None。"""
    path = course_dir / "edges.csv"
    if not path.exists():
        return None
    cols = ["left_m", "right_m", "left_kerb_m", "right_kerb_m"]
    header = next(r for r in path.read_text(encoding="utf-8").splitlines() if not r.startswith("#"))
    if header.split(",") != ["s", *cols]:
        raise ValueError(f"{path} の列が {['s', *cols]} でない（python -m ingest.edges で作り直す）")
    df = pl.read_csv(path, comment_prefix="#", schema={"s": pl.Float64, **{c: pl.Float64 for c in cols}})
    s = df["s"].to_numpy()
    if len(s) != len(line.s) or not np.allclose(s, line.s, atol=1e-3):
        raise ValueError(f"{path} の s が基準線の格子と違う（基準線を作り直したら python -m ingest.edges も回す）")
    left, right, left_kerb, right_kerb = (df[c].to_numpy() for c in cols)
    if not all(np.isfinite(v).all() for v in (left, right, left_kerb, right_kerb)):
        raise ValueError(f"{path} に NaN がある")
    if (left_kerb < left).any() or (right_kerb < right).any():
        raise ValueError(f"{path} に縁石の外の線が路面の端より内側の所がある")
    meta = {}
    for row in path.read_text(encoding="utf-8").splitlines():
        if row.startswith("# ") and " = " in row:
            k, v = row[2:].split(" = ", 1)
            meta[k] = v
    missing = {"attribution", "attribution_url"} - meta.keys()
    if missing:
        raise ValueError(f"{path} の頭に出典の行 {sorted(missing)} がない（python -m ingest.edges で作り直す）")
    rx, ry, nx, ny = frame(line, line.s)

    def pts(off: np.ndarray) -> dict:
        return {"x": [round(float(v), 2) for v in rx + off * nx], "y": [round(float(v), 2) for v in ry + off * ny]}

    kerbs = []
    for side, sign, paved, outer in (("left", 1, left, left_kerb), ("right", -1, right, right_kerb)):
        for run in runs(outer > paved):
            # 縁石の両端は路面の端に戻す（帯の端を尖らせて閉じる）
            ii = np.r_[run[0] - 1, run, run[-1] + 1] % len(line.s)
            inner = sign * paved[ii]
            outer_run = sign * np.r_[paved[ii[0]], outer[run], paved[ii[-1]]]
            ring = np.r_[ii, ii[::-1]]
            off = np.r_[inner, outer_run[::-1]]
            kerbs.append({"side": side, "i": [int(v) for v in ring],
                          "x": [round(float(v), 2) for v in rx[ring] + off * nx[ring]],
                          "y": [round(float(v), 2) for v in ry[ring] + off * ny[ring]]})
    return {"left": pts(left), "right": pts(-right), "kerbs": kerbs,
            "attribution": meta["attribution"], "attribution_url": meta["attribution_url"]}


def runs(mask: np.ndarray) -> list[np.ndarray]:
    """閉じた並び `mask` の True が続く所ごとの添字。末尾から先頭へ続く所は 1 つにまとめる（添字は末尾の側から並ぶ）。"""
    n = len(mask)
    if mask.all():
        raise ValueError("縁石が 1 周すべてを覆っている")
    start = int(np.flatnonzero(~mask)[0])  # False の所から回し、先頭をまたぐ並びを切らない
    order = (np.arange(n) + start) % n
    m = mask[order]
    d = np.diff(np.r_[0, m.astype(int), 0])
    return [order[a:b] for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1))]


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


def corner_stats(lap: dict, secs: list[dict]) -> dict[str, dict]:
    """`lap` は書き出す形の周。`secs` の `kind = "corner"` の区間ごとの値を、コーナーの `id` で引ける形にする。"""
    out = {}
    prev_end = 0
    for sec in secs:
        if sec["kind"] != "corner":
            continue
        a, b = sec["s_start"], sec["s_end"]
        t = lap["t"][a : b + 1]
        v = lap["speed_kmh"][a : b + 1]
        stat = {"v_min": None, "s_vmin": None, "v_entry": None, "v_exit": None,
                "s_brake": None, "ax_min": None}
        if all(x is not None for x in t) and all(x is not None for x in v):
            k = min(range(len(v)), key=v.__getitem__)
            lo = math.ceil(max(prev_end, a - BRAKE_LOOKBACK))
            s_brake, ax_min = brake_point(lap["ax"], lo, a + k)
            stat.update(v_min=v[k], s_vmin=a + k, v_entry=v[0], v_exit=v[-1],
                        s_brake=s_brake, ax_min=ax_min)
        out[sec["id"]] = stat
        prev_end = b
    return out


def sections(corners, grid_max: int) -> list[dict]:
    """コーナーと、その間の直線で 0〜`grid_max` を隙間なく覆う区間。境界は格子の整数。"""
    out: list[dict] = []
    prev_end = 0
    for c in corners:
        a, b = math.ceil(c.s_start), math.floor(c.s_end)
        if a - prev_end > SECTION_MERGE:
            prev = out[-1]["id"] if out else None
            out.append(_straight(prev, c.id, prev_end, a))
        elif out:
            out[-1]["s_end"] = a
        else:
            a = 0
        out.append({"id": c.id, "kind": "corner", "name": c.name, "s_start": a, "s_end": b,
                    "s_apex": None if math.isnan(c.s_apex) else c.s_apex, "side": side(c.r_apex)})
        prev_end = b
    if grid_max - prev_end > SECTION_MERGE:
        out.append(_straight(out[-1]["id"] if out else None, None, prev_end, grid_max))
    elif out:
        out[-1]["s_end"] = grid_max
    return out


def side(r_apex: float) -> str | None:
    """`corners.toml` の `r_apex` の符号（正が左）からコーナーの向き。`r_apex` がなければ None。"""
    if math.isnan(r_apex):
        return None
    return "left" if r_apex > 0 else "right"


def _straight(prev: str | None, nxt: str | None, a: int, b: int) -> dict:
    """`prev` の後、`nxt` の前の直線。`None` は計測線。"""
    sid = f"{prev or ''}→{nxt or ''}"
    name = f"{prev or '計測線'}→{nxt or '計測線'}"
    return {"id": sid, "kind": "straight", "name": name, "s_start": a, "s_end": b}


def section_times(lap: dict, secs: list[dict]) -> list[float | None]:
    """`lap` は書き出す形の周。区間の格子に欠けがあれば null。"""
    out: list[float | None] = []
    for sec in secs:
        t = lap["t"][sec["s_start"] : sec["s_end"] + 1]
        out.append(round(t[-1] - t[0], DIGITS["t"]) if all(x is not None for x in t) else None)
    return out


def export_course(data_dir: Path, course_dir: Path) -> dict:
    """`data_dir` の全周を、`course_dir` の基準線とコーナーに合わせて書き出す形にする。"""
    line = load_refline(course_dir)
    grid_max = math.floor(line.length)
    n = grid_max + 1
    # corners.toml はコースによってはまだない。ないときは null にして、web view は帯を描かない
    has_corners = (course_dir / "corners.toml").exists()
    corners = load_corners(course_dir) if has_corners else None
    secs = None if corners is None else sections(corners, grid_max)

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
        meta["group"] = data.group_of(meta["source"])
        for col, digits in DIGITS.items():
            meta[col] = _column(idx, g[col].to_list(), digits, n)
        meta["corner_stats"] = None if secs is None else corner_stats(meta, secs)
        meta["section_times"] = None if secs is None else section_times(meta, secs)
        out_laps.append(meta)

    return {
        "schema": SCHEMA,
        "course": course_dir.name,
        "length": line.length,
        "grid_max": grid_max,
        "refline": {
            "s": [round(float(v), 3) for v in line.s],
            "x": [round(float(v), 2) for v in line.x],
            "y": [round(float(v), 2) for v in line.y],
        },
        "edges": edges(line, course_dir),
        "sections": secs,
        "default_ref": data.default_ref(laps),
        "brake_g": BRAKE_G,
        "min_diff_s": MIN_DIFF_S,
        "channels": list(DIGITS),
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
    utf8_stdio()
    main(sys.argv[1:])
