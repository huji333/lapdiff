"""collector の `.acraw.gz`（collector/FORMAT.md）を共通スキーマと生チャンネルの Parquet にする。

使い方：`python -m ingest.acraw <acraw> <outdir>`

- 読み取り：ヘッダの magic と format_version を確かめ、static の smVersion とブロック長から
  dtype を選ぶ（`ingest.ac_struct.select_dtypes`）。
- `.part`（collector の異常終了）は、gzip の末尾がなくても読めたところまで使う。
  最後の途中で切れたレコードは捨てる。`.part` 以外で末尾が欠けていたら失敗させる。
- 時刻：`host_time_ns` を正とする。packetId の 3 ms 刻みは使わない。
- ラップ：`graphics.completedLaps` の値を `lap` とする（0 はアウトラップ）。
  値が変わったレコードを計測線の通過とし、周の開始時刻はその時刻から `graphics.iCurrentTime` を引いて求める。
  通過はレコードの間（最大 3 ms 程度）で起きるので、こうすると AC の計時とほぼ一致する。
  ファイルの先頭の周は記録の途中から始まっているので閉じた周として扱わない。
  `completedLaps` が減ったり 2 以上増えたりしたら失敗させる（collector がファイルを分けているはず）。
- `lap_time_own`：次の周の開始レコードの `graphics.iLastTime`。`lap_time` と合わない周（ポーズを含む周など）は、
  警告を出して無効にする（`ingest.laps.agrees_with_own_timing`）。
- 局所座標：`track/<course>/ac_transform.toml`（`ingest.ac_transform`）で水平面を ENU に移す。
- 派生チャンネル：`physics.speedKmh` と変換後の位置を `ingest.derive.derive` に渡す（実車と同じ関数）。
  derive が 10 Hz の格子に補間するので、これが SPEC の「約10 Hz に間引く」に当たる。
- `steer`、`throttle`、`brake`：`physics.steerAngle`（単位は未確認）、`gas`、`brake` を格子時刻に線形補間する。
- 生チャンネル：physics と graphics の全フィールドを `physics.<名前>`、`graphics.<名前>` の列にする。
  配列は `<名前>_<添字>` に展開する。wchar の文字列（`currentTime` など）は整数の列と重複するので除く。
- `session_id`：ヘッダの記録開始時刻（UTC）を `%Y%m%dT%H%M%SZ` で表したもの。
"""

import struct
import sys
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from ingest import ac_struct
from ingest.ac_transform import TRACK_DIR, ac_plane, load_transform
from ingest.derive import derive
from ingest.laps import agrees_with_own_timing, lap_table
from ingest.schema import JOIN_KEYS, TIMED_COLUMNS, to_common, write_session
from ingest.track import load_refline

SOURCE = "ac"
MAGIC = b"ACRAW\0\0\0"
SUPPORTED_FORMATS = {1}
HEADER = struct.Struct("<8sI16sIIIq")  # FORMAT.md の「ヘッダ」
assert HEADER.size == 48


@dataclass(frozen=True)
class AcCapture:
    format_version: int
    collector_version: str
    start_unix_ns: int
    dtypes: ac_struct.AcDtypes
    static: np.void
    host_time_ns: np.ndarray
    physics: np.ndarray
    graphics: np.ndarray
    truncated: bool  # gzip の末尾がない（`.part`）
    n_dropped_bytes: int  # 途中で切れた最後のレコードのバイト数

    def static_str(self, name: str) -> str:
        return ac_struct.decode_wstr(self.static[name])

    @property
    def t(self) -> np.ndarray:
        """ホストの時刻 [s]（Unix）。"""
        return self.host_time_ns / 1e9

    @property
    def n_packet_gaps(self) -> int:
        """取りこぼした physics の packetId の件数。"""
        d = np.diff(self.physics["packetId"].astype(np.int64))
        return int(np.sum(d[d > 1] - 1))


def _decompress(path: Path) -> tuple[bytes, bool]:
    d = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        data = d.decompress(path.read_bytes())
    except zlib.error as e:
        raise ValueError(f"{path}: gzip として読めない: {e}") from e
    if d.unused_data:
        raise ValueError(f"{path}: gzip ストリームの後ろに余分なデータがある")
    truncated = not d.eof
    if truncated and not path.name.endswith(".part"):
        raise ValueError(f"{path}: gzip の末尾がない（.part 以外では失敗させる）")
    return data, truncated


def read_acraw(path: Path) -> AcCapture:
    data, truncated = _decompress(path)
    if len(data) < HEADER.size:
        raise ValueError(f"{path}: ヘッダ {HEADER.size} バイトに足りない（{len(data)} バイト）")
    magic, fmt, cver, static_len, physics_len, graphics_len, start_ns = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ValueError(f"{path}: magic が {MAGIC!r} でない: {magic!r}")
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"{path}: 未対応の format_version {fmt}（対応: {SUPPORTED_FORMATS}）")

    body = HEADER.size + static_len
    if len(data) < body:
        raise ValueError(f"{path}: static ブロックが途中で切れている")
    static_block = data[HEADER.size : body]
    dtypes = ac_struct.select_dtypes(
        ac_struct.read_sm_version(static_block),
        {"static": static_len, "physics": physics_len, "graphics": graphics_len},
    )
    rec = np.dtype([
        ("host_time_ns", "<i8"),
        ("physics", dtypes.physics),
        ("graphics", dtypes.graphics),
    ])
    n, rest = divmod(len(data) - body, rec.itemsize)
    if rest and not truncated:
        raise ValueError(f"{path}: レコード列の末尾に {rest} バイトの余りがある")
    recs = np.frombuffer(data, dtype=rec, count=n, offset=body)
    if n and np.any(np.diff(recs["host_time_ns"]) <= 0):
        raise ValueError(f"{path}: host_time_ns が狭義単調増加でない")
    return AcCapture(
        format_version=fmt,
        collector_version=cver.split(b"\0", 1)[0].decode("ascii"),
        start_unix_ns=start_ns,
        dtypes=dtypes,
        static=ac_struct.parse_block(static_block, dtypes.static),
        host_time_ns=recs["host_time_ns"],
        physics=recs["physics"],
        graphics=recs["graphics"],
        truncated=truncated,
        n_dropped_bytes=rest,
    )


def lap_starts(cap: AcCapture) -> pl.DataFrame:
    """周ごとの開始時刻（`ingest.laps.lap_table` の入力）。"""
    cl = cap.graphics["completedLaps"].astype(np.int64)
    if len(cl) == 0:
        raise ValueError("レコードがない")
    step = np.diff(cl)
    if np.any(step < 0) or np.any(step > 1):
        bad = np.flatnonzero((step < 0) | (step > 1))[0]
        raise ValueError(
            f"completedLaps が {cl[bad]} から {cl[bad + 1]} に変わった（1 ずつ増える以外は想定しない）"
        )
    ch = np.flatnonzero(step) + 1
    t = cap.t
    cur = cap.graphics["iCurrentTime"][ch] / 1000.0
    return pl.DataFrame({
        "lap": np.r_[cl[0], cl[ch]],
        "t_start": np.r_[t[0], t[ch] - cur],
        "start_seen": np.r_[False, np.ones(len(ch), dtype=bool)],
    })


def closed_laps(cap: AcCapture, laps: pl.DataFrame) -> np.ndarray:
    """閉じた周に属するレコードの添字。"""
    closed = lap_table(laps).filter("closed")["lap"].to_numpy()
    return np.flatnonzero(np.isin(cap.graphics["completedLaps"], closed))


def _flatten(prefix: str, recs: np.ndarray) -> dict[str, np.ndarray]:
    cols = {}
    for name in recs.dtype.names:
        a = recs[name]
        if a.dtype.base == np.dtype("<u2"):  # wchar の文字列
            continue
        if a.ndim == 1:
            cols[f"{prefix}.{name}"] = a
            continue
        for idx in np.ndindex(a.shape[1:]):
            cols[f"{prefix}.{name}_{'_'.join(map(str, idx))}"] = a[(slice(None), *idx)]
    return cols


@dataclass(frozen=True)
class Session:
    common: pl.DataFrame
    timed: pl.DataFrame  # 周ごとの 10 Hz の格子（`ingest.schema.TIMED_COLUMNS`）
    raw: pl.DataFrame
    laps: pl.DataFrame
    capture: AcCapture
    dropped: dict[str, int]  # 距離の格子に使わなかった点（`ingest.derive.by_distance`）


def ingest(path: Path, track_dir: Path = TRACK_DIR) -> Session:
    cap = read_acraw(path)
    course_dir, sim = load_transform(
        cap.static_str("track"), cap.static_str("trackConfiguration"), track_dir
    )
    session_id = datetime.fromtimestamp(cap.start_unix_ns / 1e9, UTC).strftime("%Y%m%dT%H%M%SZ")

    starts = lap_starts(cap)
    laps = lap_table(starts)
    cl = cap.graphics["completedLaps"].astype(np.int64)
    ch = np.flatnonzero(np.diff(cl)) + 1
    ac_time = pl.DataFrame({
        "lap": cl[ch] - 1,
        "lap_time_own": cap.graphics["iLastTime"][ch] / 1000.0,
    })
    laps = laps.join(ac_time, on="lap", how="left", maintain_order="left")
    t_start = laps["t_start"].to_numpy()
    lap_ids = laps["lap"].to_numpy()

    def lap_of(t_abs: np.ndarray) -> np.ndarray:
        # 最初の周の開始より前（iCurrentTime を引いた分）は最初の周に入れる
        return lap_ids[np.maximum(np.searchsorted(t_start, t_abs, side="right") - 1, 0)]

    t = cap.t
    lap = lap_of(t)
    raw = pl.DataFrame({
        "source": SOURCE,
        "session_id": session_id,
        "lap": pl.Series(lap, dtype=pl.Int64),
        "t": t - t_start[np.searchsorted(lap_ids, lap)],
        "host_time_ns": cap.host_time_ns,
        **_flatten("physics", cap.physics),
        **_flatten("graphics", cap.graphics),
    })

    xy = sim.apply(ac_plane(cap.graphics["carCoordinates"]))
    p = cap.physics
    d = derive(t, xy[:, 0], xy[:, 1], p["speedKmh"].astype(np.float64) / 3.6)
    tg = d["t"].to_numpy()
    lap_g = lap_of(tg)
    timed = d.with_columns(
        lap=pl.Series(lap_g, dtype=pl.Int64),
        t=pl.Series(tg - t_start[np.searchsorted(lap_ids, lap_g)]),
        steer=pl.Series(np.interp(tg, t, p["steerAngle"])),
        throttle=pl.Series(np.interp(tg, t, p["gas"])),
        brake=pl.Series(np.interp(tg, t, p["brake"])),
    ).select(TIMED_COLUMNS)
    # ポーズ中の時間は host_time_ns に入るが、AC の計時に入るかはまだ確かめていない（issue #2）。
    # どちらであっても、自身の計時と合わない周は比べられないので無効にする
    agrees = agrees_with_own_timing(laps, "AC")
    common, laps, dropped = to_common(
        timed, laps, load_refline(course_dir), SOURCE, session_id, usable=agrees
    )
    return Session(common=common, timed=timed, raw=raw.select(*JOIN_KEYS, pl.exclude(JOIN_KEYS)),
                   laps=laps, capture=cap, dropped=dropped)


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: python -m ingest.acraw <acraw> <outdir>")
    s = ingest(Path(argv[0]))
    c = s.capture
    paths = write_session(s.common, s.raw, Path(argv[1]))
    print(
        f"{c.static_str('carModel')} @ {c.static_str('track')}/{c.static_str('trackConfiguration')}、"
        f"AC {c.static_str('acVersion')}、smVersion {c.dtypes.sm_version}、"
        f"{len(c.host_time_ns)} レコード、packetId の飛び {c.n_packet_gaps} 件"
    )
    print(f"距離の格子に使わなかった 10 Hz の点: {s.dropped}")
    if c.truncated:
        print(f"gzip の末尾がない（.part）。途中で切れたレコード {c.n_dropped_bytes} バイトを捨てた")
    with pl.Config(tbl_rows=-1):
        print(s.laps)
    for path in paths:
        print(path)


if __name__ == "__main__":
    main(sys.argv[1:])
