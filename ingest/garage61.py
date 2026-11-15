"""Garage 61 から書き出した iRacing の周の CSV を共通スキーマと生チャンネルの Parquet にする。

使い方：`python -m ingest.garage61 <csv> <outdir> [<course>]`

ほかの人の速い周を基準ラップにするためのもの。iRacing の周どうしで比べるので `source` は `iracing`。

- ファイル名：`Garage 61 - <運転者> - <車> - <コース> - <MM.SS.mmm> - <ID>.csv`。
  `<MM.SS.mmm>` を Garage 61 の計時（`lap_time_own`）にする。
- 列：`.ibt` と同じ名前の変数（`Speed`、`LapDistPct`、`Lat`、`Lon`、`Brake`、`Throttle`、`LatAccel`、
  `LongAccel` など）。時刻の列はないので、記録は 60 Hz の等間隔とみなす（行数 / 60 が周のタイムと合う）。
- 周：`LapDistPct` が 1 から 0 に戻ったところを計測線の通過とし、前後の記録の間を線形補間して時刻を求める。
  最初の周の開始は、先頭の 2 記録から計測線まで外挿する（先頭が計測線から `START_TOL` 以内のときだけ）。
  計時と合う閉じた周だけを有効にする（`ingest.laps.agrees_with_own_timing`）。
- コース：CSV にはセッション情報がないので、`<course>` を渡すか、`iracing_transform.toml` を持つコースが
  1 つだけならそれを使う。座標変換は `.ibt` と同じ（`track/<course>/iracing_transform.toml`）。
- 派生チャンネル・`ax`、`ay`・`throttle`、`brake`・`steer` の扱いは `ingest.ibt` と同じ。
- `session_id`：`g61-<運転者>-<ID の末尾 6 文字>`（空白などは `_` にする）。
- 生チャンネル：全列を `garage61.<名前>` の列にする。
"""

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from ingest.ac_transform import TRACK_DIR, Similarity
from ingest.cli import utf8_stdio
from ingest.derive import derive
from ingest.ibt import RATE_HZ, SOURCE, TRANSFORM_FILE, smooth_g
from ingest.laps import agrees_with_own_timing, lap_table
from ingest.racechrono import to_enu
from ingest.schema import JOIN_KEYS, TIMED_COLUMNS, to_common, write_session
from ingest.track import load_refline

NAME = re.compile(
    r"^Garage 61 - (?P<driver>.+?) - (?P<car>.+?) - (?P<track>.+) - "
    r"(?P<m>\d+)\.(?P<s>\d{2})\.(?P<ms>\d{3}) - (?P<id>[0-9A-Za-z]+)$"
)
START_TOL = 0.002  # 先頭の LapDistPct がこれ以下なら、計測線からの外挿で周の開始時刻を求める
REQUIRED = ["Speed", "LapDistPct", "Lat", "Lon", "Throttle", "Brake", "LatAccel", "LongAccel"]


@dataclass(frozen=True)
class FileInfo:
    driver: str
    car: str
    track: str
    lap_time: float
    id: str

    @property
    def session_id(self) -> str:
        return "g61-" + re.sub(r"[^0-9A-Za-z]+", "_", self.driver).strip("_") + "-" + self.id[-6:]


def parse_name(path: Path) -> FileInfo:
    m = NAME.match(path.stem)
    if m is None:
        raise ValueError(f"{path.name}: Garage 61 のファイル名の形ではない")
    t = int(m["m"]) * 60 + int(m["s"]) + int(m["ms"]) / 1000
    return FileInfo(m["driver"], m["car"], m["track"], t, m["id"])


def course_transform(course: str | None, track_dir: Path = TRACK_DIR) -> tuple[Path, Similarity]:
    if course is None:
        found = sorted(track_dir.glob(f"*/{TRANSFORM_FILE}"))
        if len(found) != 1:
            raise ValueError(
                f"{TRANSFORM_FILE} を持つコースが 1 つではない（{[p.parent.name for p in found]}）。"
                "コースを渡す"
            )
        path = found[0]
    else:
        path = track_dir / course / TRANSFORM_FILE
        if not path.exists():
            raise ValueError(f"{path} がない（python -m ingest.ibt --fit で推定する）")
    conf = tomllib.loads(path.read_text(encoding="utf-8"))
    return path.parent, Similarity(conf["scale"], conf["rotation_deg"], conf["tx"], conf["ty"])


def lap_starts(pct: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, pl.DataFrame]:
    """各記録の周の番号と、`lap, t_start, start_seen` の表。

    最初の周は、先頭が計測線の直後なら 1、そうでなければ 0（開始の見えない周）。
    """
    dt = 1 / RATE_HZ
    wraps = np.flatnonzero(np.diff(pct) < -0.5) + 1  # 計測線を越えた直後の記録
    first = 1 if pct[0] <= START_TOL else 0
    lap = first + np.searchsorted(wraps, np.arange(len(pct)), side="right")
    rows = []
    if first == 1:
        rate = (pct[1] - pct[0]) / dt
        rows.append((1, float(t[0] - pct[0] / rate), True))
    else:
        rows.append((0, float(t[0]), False))
    for k, i in enumerate(wraps):
        before = 1 - pct[i - 1]
        frac = before / (before + pct[i])
        rows.append((first + k + 1, float(t[i - 1] + frac * dt), True))
    starts = pl.DataFrame(rows, schema=["lap", "t_start", "start_seen"], orient="row")
    return lap, starts


@dataclass(frozen=True)
class Session:
    common: pl.DataFrame
    raw: pl.DataFrame
    laps: pl.DataFrame
    info: FileInfo
    course_dir: Path


def ingest(path: Path, course: str | None = None, track_dir: Path = TRACK_DIR) -> Session:
    info = parse_name(path)
    df = pl.read_csv(path, infer_schema_length=None)  # 先頭だけ見ると Brake などが整数に見える
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: 列がない: {missing}")
    course_dir, sim = course_transform(course, track_dir)
    origin = tomllib.loads((course_dir / "origin.toml").read_text(encoding="utf-8"))

    def col(name: str) -> np.ndarray:
        return df[name].to_numpy().astype(np.float64)

    t = np.arange(df.height) / RATE_HZ
    lap, starts = lap_starts(col("LapDistPct"), t)
    table = lap_table(starts)
    closed = table.filter("closed")["lap"].to_list()
    own = info.lap_time if len(closed) == 1 else None
    table = table.with_columns(
        lap_time_own=pl.when(pl.col("lap").is_in(closed)).then(pl.lit(own, dtype=pl.Float64))
    )
    t_start = dict(starts.select("lap", "t_start").iter_rows())

    def lap_time(times: np.ndarray, laps: np.ndarray) -> np.ndarray:
        return times - np.array([t_start[k] for k in laps])

    x, y = to_enu(col("Lat"), col("Lon"), origin["lat"], origin["lon"])
    xy = sim.apply(np.c_[x, y])
    d = derive(t, xy[:, 0], xy[:, 1], col("Speed"), rate_hz=RATE_HZ)
    tg = d["t"].to_numpy()
    lap_g = np.interp(tg, t, lap).round().astype(np.int64)
    timed = d.with_columns(
        lap=pl.Series(lap_g, dtype=pl.Int64),
        t=pl.Series(lap_time(tg, lap_g)),
        ax=pl.Series(smooth_g(t, col("LongAccel"), tg)),
        ay=pl.Series(smooth_g(t, col("LatAccel"), tg)),
        steer=pl.lit(None, dtype=pl.Float64),
        throttle=pl.Series(np.interp(tg, t, col("Throttle"))),
        brake=pl.Series(np.interp(tg, t, col("Brake"))),
    ).with_columns(pl.col("ax", "ay").fill_nan(None)).select(TIMED_COLUMNS)

    agrees = agrees_with_own_timing(table, "Garage 61")
    common, laps, _ = to_common(
        timed, table, load_refline(course_dir), SOURCE, info.session_id, usable=agrees
    )
    raw = pl.DataFrame({
        "source": SOURCE,
        "session_id": info.session_id,
        "lap": pl.Series(lap, dtype=pl.Int64),
        "t": lap_time(t, lap),
    }).hstack(df.rename({c: f"garage61.{c}" for c in df.columns}))
    return Session(common, raw.select(*JOIN_KEYS, pl.exclude(JOIN_KEYS)), laps, info, course_dir)


def main(argv: list[str]) -> None:
    if len(argv) not in (2, 3):
        raise SystemExit("usage: python -m ingest.garage61 <csv> <outdir> [<course>]")
    s = ingest(Path(argv[0]), argv[2] if len(argv) == 3 else None)
    paths = write_session(s.common, s.raw, Path(argv[1]))
    print(f"{s.info.driver} / {s.info.car} / {s.course_dir.name}、Garage 61 の計時 {s.info.lap_time:.3f} s")
    with pl.Config(tbl_rows=-1):
        print(s.laps)
    for path in paths:
        print(path)


if __name__ == "__main__":
    utf8_stdio()
    main(sys.argv[1:])
