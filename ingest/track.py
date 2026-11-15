"""基準線（SPEC「基準線と座標変換」）：作成、読み込み、射影。

使い方（基準線を作って `track/<course>/refline.csv` を書く）：
`python -m ingest.track <racechrono csv> <course>`

- 基準線は閉じた折れ線で、`s = 0` が計測線。`STEP` [m] 刻みの点 `(s, x, y)` を持つ。
  最後の点から最初の点へ戻る区間も線の一部で、全長 `length` は最後の点の `s + 最後の区間の長さ`。
- 作り方：実車の有効な周を集め、最速周を初期の線とする。各周を線に射影して `s` を求め、
  同じ `s` での位置を全周で平均し、弧長で取り直す。これを `ITERATIONS` 回繰り返す。
  各周の計測線の通過での `s`（最初の 2 点から外挿）の平均を新しい原点にする。
  平均した線は、細かいジグザグで全長が伸びないよう、窓 `SMOOTH` [m] の Savitzky-Golay（3次、周期境界）で平滑化する。
- 射影：点列を順に、直前の点の `s` の前後 `WINDOW` [m] の範囲から最も近い線上の点を探す。
  コースの別の部分が近くを通っていても、走行の順序から外れた所へは飛ばない。
  最初の点だけは線全体から探す。
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
from scipy.signal import savgol_filter

from ingest.cli import utf8_stdio

TRACK_DIR = Path(__file__).resolve().parents[1] / "track"
STEP = 1.0
ITERATIONS = 3
WINDOW = 60.0
SMOOTH = 15.0


@dataclass(frozen=True)
class Refline:
    s: np.ndarray
    x: np.ndarray
    y: np.ndarray
    length: float

    @property
    def xy(self) -> np.ndarray:
        return np.c_[self.x, self.y]


def frame(line: Refline, s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """基準線上の距離 `s` の点 (x, y) と、進行方向の左向きの単位法線 (nx, ny)。

    基準線は閉じているので全長を周期として補間する。接線は前後 1 m の点の差。
    """
    def at(v: np.ndarray, col: np.ndarray) -> np.ndarray:
        return np.interp(v % line.length, line.s, col, period=line.length)

    s = np.asarray(s, dtype=np.float64)
    tx = at(s + 1.0, line.x) - at(s - 1.0, line.x)
    ty = at(s + 1.0, line.y) - at(s - 1.0, line.y)
    norm = np.hypot(tx, ty)
    return at(s, line.x), at(s, line.y), -ty / norm, tx / norm


def _arclength(xy: np.ndarray, closed: bool) -> np.ndarray:
    pts = np.r_[xy, xy[:1]] if closed else xy
    return np.r_[0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]


def resample_closed(xy: np.ndarray, step: float = STEP, origin: float = 0.0) -> Refline:
    """閉じた折れ線を、`origin` [m]（弧長）を始点として `step` 刻みに取り直す。"""
    xy = np.asarray(xy, dtype=np.float64)
    d = _arclength(xy, closed=True)
    length = d[-1]
    n = max(round(length / step), 3)
    s = np.arange(n) * (length / n)
    pts = np.r_[xy, xy[:1]]
    u = (s + origin) % length
    x, y = np.interp(u, d, pts[:, 0]), np.interp(u, d, pts[:, 1])
    return Refline(s=s, x=x, y=y, length=float(length))


def project(xy: np.ndarray, line: Refline, window: float = WINDOW) -> tuple[np.ndarray, np.ndarray]:
    """点列を基準線に射影し、連続した `s`（周をまたいでも巻き戻さない）と線からの距離を返す。

    返す `s` は最初の点を `[0, length)` に置き、以降は連続になるよう `length` の倍数を足す。
    """
    xy = np.asarray(xy, dtype=np.float64)
    p = line.xy
    seg = np.roll(p, -1, axis=0) - p
    seg_len2 = np.einsum("ij,ij->i", seg, seg)
    seg_len = np.sqrt(seg_len2)
    n = len(p)
    half = int(np.ceil(window / (line.length / n)))

    def nearest(pt, idx):
        v = pt - p[idx]
        u = np.clip(np.einsum("ij,ij->i", v, seg[idx]) / seg_len2[idx], 0, 1)
        foot = p[idx] + u[:, None] * seg[idx]
        d = np.hypot(*(pt - foot).T)
        k = int(np.argmin(d))
        return idx[k], u[k], d[k]

    out_s = np.empty(len(xy))
    out_d = np.empty(len(xy))
    i, u, d = nearest(xy[0], np.arange(n))
    prev = line.s[i] + u * seg_len[i]
    out_s[0], out_d[0] = prev, d
    for j in range(1, len(xy)):
        c = int(prev / line.length * n) % n
        i, u, d = nearest(xy[j], (np.arange(c - half, c + half + 1)) % n)
        sj = line.s[i] + u * seg_len[i]
        # prev に最も近い周回の値にする
        sj += np.round((prev - sj) / line.length) * line.length
        out_s[j], out_d[j], prev = sj, d, sj
    return out_s, out_d


def lap_s(xy: np.ndarray, line: Refline) -> tuple[np.ndarray, np.ndarray]:
    """1 周分の点列の `s`。始点が計測線の手前（`s` が全長に近い）なら負の値にする。"""
    s, d = project(xy, line)
    if s[0] > line.length / 2:
        s = s - line.length
    return s, d


def build_refline(
    laps: list[tuple[np.ndarray, np.ndarray]], step: float = STEP, iterations: int = ITERATIONS
) -> Refline:
    """各周の `(t, xy)` から基準線を作る。最速周を先頭に置く。

    `t` は周の開始（計測線の通過）からの秒。最初の点は通過の少し後なので、
    最初の 2 点から `t = 0` での `s` を外挿し、それを計測線の位置とする。
    """
    line = resample_closed(laps[0][1], step)
    for _ in range(iterations):
        grid = line.s
        acc = np.zeros((len(grid), 2))
        starts = []
        for t, xy in laps:
            s, _ = lap_s(xy, line)
            keep = np.r_[True, s[1:] > np.maximum.accumulate(s)[:-1]]  # 進まない点を除く
            s, pts, tk = s[keep], xy[keep], t[keep]
            starts.append(s[0] - (s[1] - s[0]) / (tk[1] - tk[0]) * tk[0])
            # 計測線の付近も端の値で埋めないよう、1 周ずらした点を前後に継ぎ足して補間する
            se = np.r_[s - line.length, s, s + line.length]
            pe = np.r_[pts, pts, pts]
            mono = np.r_[True, se[1:] > np.maximum.accumulate(se)[:-1]]
            se, pe = se[mono], pe[mono]
            acc += np.c_[np.interp(grid, se, pe[:, 0]), np.interp(grid, se, pe[:, 1])]
        mean = _smooth_closed(acc / len(laps), round(SMOOTH / (line.length / len(grid))) | 1)
        # 原点は旧い線の s で求まる。平均した線の弧長に換算してから使う（両者の全長は違う）
        arc = _arclength(mean, closed=True)[:-1]
        origin = float(np.interp(np.mean(starts) % line.length, grid, arc))
        line = resample_closed(mean, step, origin=origin)
    return line


def _smooth_closed(xy: np.ndarray, window: int) -> np.ndarray:
    pad = np.r_[xy[-window:], xy, xy[:window]]
    return savgol_filter(pad, window, 3, axis=0)[window:-window]


def write_refline(line: Refline, path: Path, note: str) -> None:
    head = "".join(f"# {s}\n" for s in note.splitlines())
    head += f"# length = {line.length:.3f}\n"
    body = pl.DataFrame({"s": line.s, "x": line.x, "y": line.y}).write_csv(float_precision=3)
    path.write_text(head + body, encoding="utf-8")


def load_refline(course_dir: Path) -> Refline:
    path = course_dir / "refline.csv"
    if not path.exists():
        raise ValueError(f"{path} がない（python -m ingest.track で作る）")
    text = path.read_text(encoding="utf-8")
    length = next(
        float(line.split("=", 1)[1]) for line in text.splitlines() if line.startswith("# length")
    )
    df = pl.read_csv(path, comment_prefix="#")
    return Refline(df["s"].to_numpy(), df["x"].to_numpy(), df["y"].to_numpy(), length)


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: python -m ingest.track <racechrono csv> <course>")
    from ingest import racechrono

    path, course = Path(argv[0]), argv[1]
    s = racechrono.ingest_timed(path)
    valid = s.laps.filter("lap_valid").sort("lap_time")
    laps = []
    for lap in valid["lap"]:
        lt = s.timed.filter(pl.col("lap") == lap)
        laps.append((lt["t"].to_numpy(), np.c_[lt["x"], lt["y"]]))
    line = build_refline(laps)
    note = (
        "基準線（ingest.track が書く）。s [m] は計測線からの距離、x, y は局所座標（ENU）[m]\n"
        f"source = {path.name}\n"
        f"laps = {valid['lap'].to_list()}（有効な周。最速周 {valid['lap'][0]} を初期の線にした）"
    )
    out = TRACK_DIR / course / "refline.csv"
    write_refline(line, out, note)
    print(f"{out}: {len(line.s)} 点、全長 {line.length:.1f} m")


if __name__ == "__main__":
    utf8_stdio()
    main(sys.argv[1:])
