"""RaceChrono の CSV（Format 3）を共通スキーマと生チャンネルの Parquet にする。

使い方：`python -m ingest.racechrono <csv> <outdir>`

- ヘッダ：先頭セルが `timestamp` の最初の行を列名とし、続く2行を単位・ソースとする。
  それより前はメタデータ。`Format` が 3 でなければ失敗させる。
- 列は `列名|ソース`（例：`speed|100: gps`、`distance_traveled|`）で識別する。
- タイムスタンプが重複した行は、最初の行だけを残す。
- ラップは `lap_number` で分ける。最初の周より前の空欄はアウトラップとして `lap = 0` にする。
  各周の開始時刻はその周の最初の行（RaceChrono が計測線の通過時刻に行を挿入している）。
- `lap_valid`：`ingest.laps` と `ingest.schema.to_common` の規則。
- 局所座標：`track/<course>/origin.toml` の固定原点を使う ENU（x 東、y 北 [m]）。
  セッションをまたいで同じ座標系にし、基準線と重ねられるようにするため、最初の測位点は使わない。
- `session_id`：最初の行の時刻（UTC）を `%Y%m%dT%H%M%SZ` で表したもの。
"""

import csv
import io
import math
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from ingest.derive import derive
from ingest.laps import lap_table
from ingest.schema import JOIN_KEYS, TIMED_COLUMNS, to_common, write_session
from ingest.track import load_refline

SOURCE = "racechrono"
SUPPORTED_FORMATS = {"3"}
TRACK_DIR = Path(__file__).resolve().parents[1] / "track"

TS = "timestamp|"
LAP = "lap_number|"

# WGS84
_A = 6378137.0
_E2 = 6.69437999014e-3


@dataclass(frozen=True)
class RaceChronoCsv:
    meta: dict[str, str]
    units: dict[str, str]  # 列キー -> 単位
    data: pl.DataFrame  # 重複除去後。列キーは `列名|ソース`
    gps_source: str
    n_duplicates: int


@dataclass(frozen=True)
class Timed:
    """基準線に射影する前の段。基準線を作るときはここまでを使う。"""

    timed: pl.DataFrame  # 周ごとの 10 Hz の格子（`ingest.schema.TIMED_COLUMNS`）
    raw: pl.DataFrame
    laps: pl.DataFrame
    session_id: str
    course_dir: Path
    n_duplicates: int
    n_no_fix: int  # 位置か速度が欠けていて派生チャンネルに使えなかった行


@dataclass(frozen=True)
class Session:
    common: pl.DataFrame
    timed: pl.DataFrame
    raw: pl.DataFrame
    laps: pl.DataFrame
    n_duplicates: int
    n_no_fix: int
    dropped: dict[str, int]  # 距離の格子に使わなかった点（`ingest.derive.by_distance`）


def parse_csv(path: Path) -> RaceChronoCsv:
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = csv.reader(lines)
    h = next((i for i, r in enumerate(rows) if r and r[0] == "timestamp"), None)
    if h is None or h + 3 > len(lines):
        raise ValueError(f"{path}: `timestamp` で始まる3行ヘッダが見つからない")

    meta = {r[0]: ",".join(r[1:]) for r in csv.reader(lines[:h]) if r}
    fmt = meta.get("Format")
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"{path}: 未対応の Format {fmt!r}（対応: {sorted(SUPPORTED_FORMATS)}）")

    names, units, sources = (next(csv.reader([line])) for line in lines[h : h + 3])
    if not (len(names) == len(units) == len(sources)):
        raise ValueError(f"{path}: ヘッダ3行の列数が揃っていない")
    keys = [f"{n}|{s}" for n, s in zip(names, sources, strict=True)]
    dup = {k for k in keys if keys.count(k) > 1}
    if dup:
        raise ValueError(f"{path}: 列名＋ソースが重複している: {sorted(dup)}")

    gps = [s for n, s in zip(names, sources, strict=True) if n == "latitude"]
    if len(gps) != 1:
        raise ValueError(f"{path}: latitude 列が1つではない: {gps}")
    gps_source = gps[0]
    required = [TS, LAP, f"latitude|{gps_source}", f"longitude|{gps_source}",
                f"speed|{gps_source}"]
    missing = [k for k in required if k not in keys]
    if missing:
        raise ValueError(f"{path}: 必須の列がない: {missing}")

    body = "\n".join(line for line in lines[h + 3 :] if line)
    data = pl.read_csv(
        io.StringIO(body), has_header=False, new_columns=keys, infer_schema_length=0
    ).with_columns(pl.all().cast(pl.Float64, strict=True))
    if data[TS].null_count():
        raise ValueError(f"{path}: timestamp が空の行がある")
    if (data[TS].diff().drop_nulls() < 0).any():
        raise ValueError(f"{path}: timestamp が逆行している")

    deduped = data.unique(TS, keep="first", maintain_order=True)
    return RaceChronoCsv(
        meta=meta,
        units=dict(zip(keys, units, strict=True)),
        data=deduped,
        gps_source=gps_source,
        n_duplicates=data.height - deduped.height,
    )


def find_course(track_name: str, track_dir: Path = TRACK_DIR) -> tuple[Path, float, float]:
    """RaceChrono の Track name に一致するコースの (ディレクトリ, 原点の lat, lon) を返す。"""
    found = []
    for p in sorted(track_dir.glob("*/origin.toml")):
        conf = tomllib.loads(p.read_text(encoding="utf-8"))
        if track_name in conf["track_names"]:
            found.append((p.parent, conf["lat"], conf["lon"]))
    if len(found) != 1:
        raise ValueError(
            f"Track name {track_name!r} に一致する origin.toml が1つではない: "
            f"{[str(f[0]) for f in found]}（{track_dir}/<course>/origin.toml を足す）"
        )
    return found[0]


def to_enu(lat, lon, lat0: float, lon0: float) -> tuple[np.ndarray, np.ndarray]:
    """緯度・経度 [deg] を原点まわりの ENU（東 x、北 y [m]）にする。高さは 0 とみなす。"""

    def ecef(phi, lam):
        n = _A / np.sqrt(1 - _E2 * np.sin(phi) ** 2)
        return (n * np.cos(phi) * np.cos(lam), n * np.cos(phi) * np.sin(lam),
                n * (1 - _E2) * np.sin(phi))

    phi, lam = np.radians(np.asarray(lat)), np.radians(np.asarray(lon))
    phi0, lam0 = math.radians(lat0), math.radians(lon0)
    px, py, pz = ecef(phi, lam)
    ox, oy, oz = ecef(phi0, lam0)
    dx, dy, dz = px - ox, py - oy, pz - oz
    x = -math.sin(lam0) * dx + math.cos(lam0) * dy
    y = (-math.sin(phi0) * math.cos(lam0) * dx - math.sin(phi0) * math.sin(lam0) * dy
         + math.cos(phi0) * dz)
    return x, y


def lap_numbers(data: pl.DataFrame) -> pl.Series:
    """`lap_number` を整数にする。最初の周より前の空欄は 0（アウトラップ）。"""
    lap = data[LAP]
    first = lap.is_not_null().arg_true()
    if first.len() == 0:
        raise ValueError("lap_number がすべて空（計測線を一度も通っていない）")
    if lap[int(first[0]) :].null_count():
        raise ValueError("周回の途中で lap_number が空になっている")
    if (lap.round() != lap).any():
        raise ValueError("lap_number が整数でない")
    lap = lap.fill_null(0).cast(pl.Int64)
    if (lap.diff().drop_nulls() < 0).any():
        raise ValueError("lap_number が減っている")
    return lap.alias("lap")


def lap_starts(ts: pl.Series, lap: pl.Series) -> pl.DataFrame:
    """周ごとの開始時刻。

    2 番目以降の周は、最初の行が計測線の通過（RaceChrono が挿入する行）。
    ファイルの最初の周は、アウトラップか、切り出したファイルで途中から始まった周なので、開始を観測していない。
    """
    starts = (
        pl.DataFrame({"lap": lap, "ts": ts})
        .group_by("lap", maintain_order=True)
        .agg(pl.col("ts").first().alias("t_start"))
    )
    return starts.with_columns(start_seen=pl.int_range(pl.len()) > 0)


def ingest_timed(path: Path, track_dir: Path = TRACK_DIR) -> Timed:
    rc = parse_csv(path)
    data = rc.data
    g = rc.gps_source
    course_dir, lat0, lon0 = find_course(rc.meta.get("Track name", ""), track_dir)

    ts = data[TS]
    session_id = datetime.fromtimestamp(ts[0], UTC).strftime("%Y%m%dT%H%M%SZ")
    lap = lap_numbers(data)
    laps = lap_table(lap_starts(ts, lap))
    starts = laps["t_start"].to_numpy()

    def lap_of(t_abs: np.ndarray) -> np.ndarray:
        return laps["lap"].to_numpy()[np.searchsorted(starts, t_abs, side="right") - 1]

    head = pl.DataFrame({"source": SOURCE, "session_id": session_id, "lap": lap})
    raw = pl.concat([head, data], how="horizontal").with_columns(
        t=pl.col(TS) - pl.col("lap").replace_strict(laps["lap"], laps["t_start"])
    ).select(*JOIN_KEYS, *data.columns)

    fix = data.select(TS, f"latitude|{g}", f"longitude|{g}", f"speed|{g}").drop_nulls()
    x, y = to_enu(fix[f"latitude|{g}"].to_numpy(), fix[f"longitude|{g}"].to_numpy(), lat0, lon0)
    d = derive(fix[TS].to_numpy(), x, y, fix[f"speed|{g}"].to_numpy())

    t_abs = d["t"].to_numpy()
    lap_g = lap_of(t_abs)
    t_start = laps["t_start"].to_numpy()[np.searchsorted(laps["lap"].to_numpy(), lap_g)]
    timed = d.with_columns(
        *(pl.lit(None, dtype=pl.Float64).alias(c) for c in ("steer", "throttle", "brake")),
        lap=pl.Series(lap_g, dtype=pl.Int64),
        t=pl.Series(t_abs - t_start),
    ).select(TIMED_COLUMNS)
    return Timed(
        timed=timed,
        raw=raw,
        laps=laps,
        session_id=session_id,
        course_dir=course_dir,
        n_duplicates=rc.n_duplicates,
        n_no_fix=data.height - fix.height,
    )


def ingest(path: Path, track_dir: Path = TRACK_DIR) -> Session:
    tm = ingest_timed(path, track_dir)
    common, laps, dropped = to_common(
        tm.timed, tm.laps, load_refline(tm.course_dir), SOURCE, tm.session_id
    )
    return Session(
        common=common,
        timed=tm.timed,
        raw=tm.raw,
        laps=laps,
        n_duplicates=tm.n_duplicates,
        n_no_fix=tm.n_no_fix,
        dropped=dropped,
    )


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: python -m ingest.racechrono <csv> <outdir>")
    s = ingest(Path(argv[0]))
    paths = write_session(s.common, s.raw, Path(argv[1]))
    print(f"重複行 {s.n_duplicates} 行を除去、位置・速度が欠けた行 {s.n_no_fix} 行は派生に不使用")
    print(f"距離の格子に使わなかった 10 Hz の点: {s.dropped}")
    with pl.Config(tbl_rows=-1):
        print(s.laps)
    for p in paths:
        print(p)


if __name__ == "__main__":
    main(sys.argv[1:])
