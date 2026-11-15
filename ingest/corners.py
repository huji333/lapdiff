"""コーナー定義（SPEC「基準線と座標変換」の `corners.toml`）：候補の作成、読み込み、検証。

使い方（最速周から候補を作って `track/<course>/corners.toml` を書く）：
`python -m ingest.corners <racechrono csv> <course> [--force]`

書いたファイルは候補であり、名前と境界は人がコース図を見て直す。
直したものを消さないよう、既にファイルがあれば `--force` なしでは失敗させる。

候補の作り方（`propose`）。入力は 1 周分の距離の格子（`s` [m]、`speed`、`kappa`）：

- `kappa` を窓 `SMOOTH` [m] の移動平均（周期境界）でならす。GPS の雑音で頂点の位置と半径がぶれるのを抑える。
- 芯：`|kappa| > 1 / R_CORNER` が続き、符号が変わらない区間。1 つの芯を 1 つのコーナーとする。
  `R_CORNER = 150` は岡山の最速周（lap 10）で決めた。減速する曲がりの頂点の半径は 25〜78 m、
  全開で抜ける曲がりは 230 m 以上で、その間にある。
  - 同じ向きのまま曲率が上下する所（複合コーナー）は 1 つにする。
  - 向きが変わる所（S 字）と、同じ向きでも途中で曲率が `1 / R_CORNER` を下回る所は分ける。
    分けた各部分にそれぞれブレーキとボトム速度があり、指標（ボトム速度など）を 1 区間に 1 つずつ出せるため。
- 頂点：芯の中で `|kappa|` が最大の点。最大値の `APEX_TOL` 倍以上の点が続く所（半径が一定の弧）では
  その真ん中をとる。半径は頂点の `|kappa|` の逆数で、走ったラインの半径（基準線の半径ではない）。
- 隣り合う芯の間（周期境界）の `speed` が最大の点を「切れ目」とする。ここが次のコーナーの減速を始める所
  （`ax` が負に転じる所）。
- 始点：切れ目の `ENTRY_MARGIN` [m] 手前。ブレーキ開始点が実車より早いソース（AC など）でも区間に入るよう余裕をとる。
  候補は実車の周だけから作る。AC の lap 2 の `ax < -0.2 G` になる点も、岡山の全区間で始点より後にあった。
- 終点：芯の後で `|kappa| < 1 / R_STRAIGHT` に戻った点（横 G が抜けて直進で加速している所）の
  `EXIT_MARGIN` [m] 先。
- 始点・終点は切れ目を越えない。直線が短く両者がぶつかる所は、前のコーナーの終点と次の始点が切れ目で接する。
- 芯にならなかった曲がり（`|kappa|` が `1 / R_BEND` を超えるが `1 / R_CORNER` に届かないもの）は、
  捨てずに `bends` として返し、toml にコメントで残す。全開で抜ける緩い曲がりで、ブレーキの指標が出ない。
- 区間が計測線（`s = 0`）をまたぐ場合は未対応として例外にする。

`load_corners` は区間が `s` の順に並び、`[0, 全長)` に収まり、重ならないことを確かめる。
違反は直さずに ValueError にする。
"""

import argparse
import sys
import tomllib
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np
import polars as pl

from ingest.cli import utf8_stdio
from ingest.track import TRACK_DIR, load_refline

SMOOTH = 15.0
R_CORNER = 150.0
R_STRAIGHT = 500.0
R_BEND = 400.0
ENTRY_MARGIN = 20.0
EXIT_MARGIN = 20.0
APEX_TOL = 0.98


@dataclass(frozen=True)
class Corner:
    id: str
    name: str
    s_start: float
    s_end: float
    s_apex: float
    r_apex: float  # 頂点での走行ラインの曲率半径 [m]。正なら左、負なら右に曲がる


@dataclass(frozen=True)
class Bend:
    """芯にならなかった緩い曲がり。"""

    s_apex: float
    r_apex: float


def _smooth_periodic(a: np.ndarray, n: int) -> np.ndarray:
    pad = np.r_[a[-n:], a, a[:n]]
    return np.convolve(pad, np.ones(n) / n, mode="same")[n:-n]


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """`mask` が真の連続区間 `[a, b)` の一覧（端をまたぐ区間は考えない）。"""
    d = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def _apex(k: np.ndarray, a: int, b: int) -> int:
    """`k[a:b]` の頂点の添字。`|k|` の最大を含み、最大の `APEX_TOL` 倍以上が続く所の真ん中。"""
    ak = np.abs(k[a:b])
    i = int(np.argmax(ak))
    for lo, hi in _runs(ak >= APEX_TOL * ak[i]):
        if lo <= i < hi:
            return a + (lo + hi - 1) // 2
    raise AssertionError("最大値を含む区間がない")


def propose(
    s: np.ndarray, speed: np.ndarray, kappa: np.ndarray, length: float
) -> tuple[list[Corner], list[Bend]]:
    """1 周分の距離の格子から、コーナーの候補（`s` の順）と芯にならなかった曲がりを返す。

    `s` は `[0, length)` を等間隔に覆う狭義単調増加の格子であること。
    """
    s, speed, kappa = (np.asarray(a, dtype=np.float64) for a in (s, speed, kappa))
    n = len(s)
    if not (len(speed) == len(kappa) == n):
        raise ValueError(f"入力の長さが揃っていない: s={n} speed={len(speed)} kappa={len(kappa)}")
    for name, a in (("s", s), ("speed", speed), ("kappa", kappa)):
        if not np.all(np.isfinite(a)):
            raise ValueError(f"{name} に NaN/inf/null がある")
    step = np.diff(s)
    if n < 3 or not np.allclose(step, step[0]) or step[0] <= 0:
        raise ValueError("s が等間隔の狭義単調増加でない")
    step = step[0]
    if s[0] != 0 or s[-1] >= length or length - s[-1] > 1.5 * step:
        raise ValueError(f"s が [0, {length}) を覆っていない: {s[0]}〜{s[-1]}")

    k = _smooth_periodic(kappa, max(round(SMOOTH / step), 1))
    sign = np.sign(k)
    cores = []
    for d in (1, -1):
        for a, b in _runs((sign == d) & (np.abs(k) > 1 / R_CORNER)):
            cores.append((int(a), int(b)))
    cores.sort()
    if len(cores) > 1 and cores[0][0] == 0 and cores[-1][1] == n and sign[0] == sign[-1]:
        raise ValueError("計測線（s = 0）をまたぐコーナーは未対応")
    if not cores:
        raise ValueError(f"半径 {R_CORNER} m より急な曲がりがない")

    m = len(cores)
    # 芯 i と i+1 の間の切れ目（周期境界。最後の芯の後は最初の芯の前につながる）
    cuts = []
    for i in range(m):
        a = cores[i][1]
        b = cores[(i + 1) % m][0] + (n if i == m - 1 else 0)
        idx = np.arange(a, b) % n
        if len(idx) == 0:
            raise ValueError(f"s={s[a % n]:.0f} で向きの違う芯が接している")
        cuts.append(a + int(np.argmax(speed[idx])))  # 周期をほどいた添字

    ends = []
    for (a, b), cut in zip(cores, cuts):
        straight = np.flatnonzero(np.abs(k[np.arange(b, cut) % n]) < 1 / R_STRAIGHT)
        exit_ = b + (int(straight[0]) if len(straight) else cut - b)
        ends.append(min(round(exit_ + EXIT_MARGIN / step), cut))

    corners = []
    for i, (a, b) in enumerate(cores):
        wrap = n if i == 0 else 0  # 最初のコーナーの手前は前の周の最後のコーナーにつながる
        start = max(round(cuts[i - 1] - wrap - ENTRY_MARGIN / step), ends[i - 1] - wrap)
        end = ends[i]
        if start < 0 or end >= n:
            raise ValueError(f"s={s[a]:.0f} のコーナーの区間が計測線（s = 0）をまたぐ（未対応）")
        apex = _apex(k, a, b)
        corners.append(Corner(
            id=f"T{i + 1}", name=f"T{i + 1}（仮）",
            s_start=float(s[start]), s_end=float(s[end]),
            s_apex=float(s[apex]), r_apex=float(1 / k[apex]),
        ))

    in_core = np.zeros(n, dtype=bool)
    for a, b in cores:
        in_core[a:b] = True
    bends = []
    for d in (1, -1):
        for a, b in _runs((sign == d) & (np.abs(k) > 1 / R_BEND)):
            if in_core[a:b].any():
                continue
            apex = _apex(k, a, b)
            bends.append(Bend(s_apex=float(s[apex]), r_apex=float(1 / k[apex])))
    bends.sort(key=lambda b: b.s_apex)
    return corners, bends


def write_corners(corners: list[Corner], bends: list[Bend], path: Path, note: str) -> None:
    lines = [f"# {s}" for s in note.splitlines()]
    lines.append("#")
    lines.append(f"# 候補にしなかった緩い曲がり（半径 {R_CORNER:.0f}〜{R_BEND:.0f} m。全開で抜ける）：")
    lines += [f"#   s_apex = {b.s_apex:.0f}, r_apex = {b.r_apex:.0f}" for b in bends] or ["#   なし"]
    for c in corners:
        lines += [
            "",
            "[[corner]]",
            f'id = "{c.id}"',
            f'name = "{c.name}"',
            f"s_start = {c.s_start:.0f}",
            f"s_end = {c.s_end:.0f}",
            f"s_apex = {c.s_apex:.0f}",
            f"r_apex = {c.r_apex:.0f}",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


REQUIRED = {"id": str, "name": str, "s_start": (int, float), "s_end": (int, float)}
OPTIONAL = {"s_apex": (int, float), "r_apex": (int, float)}


def load_corners(course_dir: Path) -> list[Corner]:
    """`corners.toml` を読み、区間が `s` の順に並び、`[0, 全長)` に収まり、重ならないことを確かめる。"""
    path = course_dir / "corners.toml"
    if not path.exists():
        raise ValueError(f"{path} がない（python -m ingest.corners で候補を作る）")
    length = load_refline(course_dir).length
    raw = tomllib.loads(path.read_text(encoding="utf-8")).get("corner")
    if not raw:
        raise ValueError(f"{path}: [[corner]] がない")

    corners = []
    for i, c in enumerate(raw):
        where = f"{path}: {i + 1} 番目の corner"
        unknown = set(c) - set(REQUIRED) - set(OPTIONAL)
        if unknown:
            raise ValueError(f"{where} に未知のキー {sorted(unknown)}")
        for key, typ in (REQUIRED | OPTIONAL).items():
            if key not in c:
                if key in REQUIRED:
                    raise ValueError(f"{where} に {key} がない")
                continue
            if not isinstance(c[key], typ) or isinstance(c[key], bool):
                raise TypeError(f"{where} の {key} の型が違う: {c[key]!r}")
        corners.append(Corner(
            id=c["id"], name=c["name"],
            s_start=float(c["s_start"]), s_end=float(c["s_end"]),
            s_apex=float(c.get("s_apex", np.nan)), r_apex=float(c.get("r_apex", np.nan)),
        ))

    ids = [c.id for c in corners]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: id が重複している: {ids}")
    for c in corners:
        if not (0 <= c.s_start < c.s_end < length):
            raise ValueError(
                f"{path}: {c.id} の区間 {c.s_start}〜{c.s_end} が [0, {length}) に収まらないか、"
                "始点が終点より後"
            )
        if not np.isnan(c.s_apex) and not (c.s_start <= c.s_apex <= c.s_end):
            raise ValueError(f"{path}: {c.id} の s_apex={c.s_apex} が区間の外")
    for p, q in pairwise(corners):
        if q.s_start < p.s_start:
            raise ValueError(f"{path}: {q.id} が {p.id} より前にある（s の順に並べる）")
        if q.s_start < p.s_end:
            raise ValueError(f"{path}: {p.id}（〜{p.s_end}）と {q.id}（{q.s_start}〜）が重なる")
    return corners


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="python -m ingest.corners")
    ap.add_argument("csv", type=Path, help="RaceChrono の CSV")
    ap.add_argument("course")
    ap.add_argument("--force", action="store_true", help="既存の corners.toml を上書きする")
    args = ap.parse_args(argv)
    from ingest import racechrono

    out = TRACK_DIR / args.course / "corners.toml"
    if out.exists() and not args.force:
        raise SystemExit(f"{out} がある。手で直した内容を消さないよう、上書きは --force で行う")
    session = racechrono.ingest(args.csv)
    valid = session.laps.filter("lap_valid").sort("lap_time")
    if valid.is_empty():
        raise SystemExit(f"{args.csv} に有効な周がない")
    best, lap_time = valid["lap"][0], valid["lap_time"][0]
    lap = session.common.filter(pl.col("lap") == best).sort("s")
    length = load_refline(TRACK_DIR / args.course).length
    corners, bends = propose(lap["s"].to_numpy(), lap["speed"].to_numpy(),
                             lap["kappa"].to_numpy(), length)
    note = (
        "コーナーの区間（基準線上の距離 [m]）。ingest.corners が候補を書き、人が直す。\n"
        f"source = {args.csv.name}、lap {best}（{lap_time:.3f} s、有効な周の最速）\n"
        "s_apex, r_apex は参考値：その周の走行ラインで |kappa| が最大の点と、そこでの曲率半径 [m]\n"
        "（正が左、負が右）。区間の決め方は ingest/corners.py の docstring にある"
    )
    write_corners(corners, bends, out, note)
    print(f"{out}: {len(corners)} コーナー、候補にしなかった曲がり {len(bends)}")
    for c in corners:
        print(f"  {c.id}: {c.s_start:.0f}〜{c.s_end:.0f} m、頂点 {c.s_apex:.0f} m、R {c.r_apex:.0f} m")


if __name__ == "__main__":
    utf8_stdio()
    main(sys.argv[1:])
