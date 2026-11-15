"""iRacing の `.ibt` を共通スキーマと生チャンネルの Parquet にする（SPEC「iRacing を後から足すための設計」）。

使い方：
- 取り込み：`python -m ingest.ibt <ibt> <outdir>`
- 相似変換の推定（`track/<course>/iracing_transform.toml` を書く）：`python -m ingest.ibt --fit <ibt> <course>`

- 読み取り：ヘッダ（irsdk_header）、ディスク用ヘッダ、変数ヘッダ、セッション情報、記録の列の順に並ぶ。
  記録の数はディスク用ヘッダの値を使い、ファイルの長さと合わなければ失敗させる。
  `SessionTime` が前の記録と同じ記録は落とし（警告を出す）、戻っていれば失敗させる。
- コース：セッション情報の `TrackName`、`TrackConfigName` で `iracing_transform.toml` を探す。
  セッション情報は YAML だが、使うのはこの 2 つだけなので行を直接読む。
- 局所座標：`Lat`、`Lon` をコースの `origin.toml` の原点で ENU に移し、相似変換で基準線に合わせる。
- ラップ：`LapCompleted` の値を `lap` とする（0 はアウトラップ）。
  - ピットへのリセットでは `LapCompleted` が少しの間 0 になって元の値に戻る。0 の連なりの前が 1 以上の値 v で、
    後ろが同じ値 v、記録の末尾、-1 のどれかなら、その記録を v の周に戻し、v の周を閉じていない周にする（警告を出す）。
  - -1 の記録（ガレージにいる間）はどの周にも入れず、件数を返す。
  - 1 増えた記録を計測線の通過とする。周の開始時刻は `lap_starts` で `LapCurrentLapTime` から求める
    （この値も更新が 20〜62 記録遅れる）。
  - 2 以上増えたら、警告を出し、その前後の周を閉じていない周にする。
  - 減ったら（リセット以外）失敗させる。周の番号が重なり、周を区別できなくなるため。
- `lap_time_own`：通過の記録のあとで `LapLastLapTime` が初めて変わった値（更新は 11〜62 記録遅れる）。
  次の通過までに変わらないか、-1（リセットのあと）なら null にし、その周は比べない
  （`ingest.laps.agrees_with_own_timing`）。
- 派生チャンネル：`Speed` と変換後の位置を `ingest.derive.derive` に 60 Hz のまま渡す。
  実車と比べないので、GPS にそろえて 10 Hz に間引かない。
- `ax`、`ay`：位置から求めた値ではなく、`LongAccel`、`LatAccel` を g で割り、窓 `G_WINDOW_S` の
  Savitzky-Golay（2次）で平滑化した値。縁石に乗ったときの 0.03 s ほどの跳ねを除く。窓の長さは仮の値。
  符号は共通スキーマと同じ（加速、左旋回が正）。
- `throttle`、`brake`：`Throttle`、`Brake`（0〜1）を格子時刻に線形補間する。
- `steer`：共通スキーマの単位が決まっていない（SPEC「共通スキーマの操作量の単位」）ので null。
  `SteeringWheelAngle`（rad）は生チャンネルに残る。
- 生チャンネル：全変数を `ibt.<名前>` の列にする。配列は `<名前>_<添字>` に展開する。
- `session_id`：ディスク用ヘッダのセッション開始時刻（UTC）を `%Y%m%dT%H%M%SZ` で表したもの。
"""

import re
import struct
import sys
import tomllib
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
from scipy.signal import savgol_filter

from ingest.ac_transform import TRACK_DIR, find_transform, fit_similarity, transform_toml
from ingest.cli import utf8_stdio
from ingest.derive import G, derive
from ingest.laps import agrees_with_own_timing, lap_table
from ingest.racechrono import to_enu
from ingest.schema import JOIN_KEYS, TIMED_COLUMNS, to_common, write_session
from ingest.track import load_refline

SOURCE = "iracing"
TRANSFORM_FILE = "iracing_transform.toml"
RATE_HZ = 60.0
G_WINDOW_S = 0.2

HEADER = struct.Struct("<10i")  # ver, status, tickRate, ..., numVars, varHeaderOffset, numBuf, bufLen
VAR_BUF = struct.Struct("<ii")  # varBuf[0] の tickCount, bufOffset（ヘッダの 48 バイト目から）
DISK = struct.Struct("<qddii")  # 開始日時、開始・終了時刻、周の数、記録の数（112 バイト目から）
VAR_HEADER = struct.Struct("<iii4x32s64s32s")
assert VAR_HEADER.size == 144
TYPES = {0: "u1", 1: "?", 2: "<i4", 3: "<u4", 4: "<f4", 5: "<f8"}


@dataclass(frozen=True)
class IbtVar:
    name: str
    dtype: str
    offset: int
    count: int
    unit: str
    desc: str


@dataclass(frozen=True)
class Ibt:
    tick_rate: int
    start_unix: int
    vars: dict[str, IbtVar]
    session_info: str
    records: np.ndarray  # 変数名をフィールドに持つ構造化配列

    def info(self, key: str) -> str:
        """セッション情報の `WeekendInfo` 直下の値（`TrackName` など）。"""
        # 値が空のとき（`TrackConfigName:`）はコロンの後ろに空白がない
        found = re.findall(rf"^ {key}:(.*)$", self.session_info, flags=re.MULTILINE)
        if len(found) != 1:
            raise ValueError(f"セッション情報の {key} が1つではない: {found}")
        return found[0].strip()

    def col(self, name: str) -> np.ndarray:
        return self.records[name]


def read_ibt(path: Path) -> Ibt:
    b = path.read_bytes()
    if len(b) < 144:
        raise ValueError(f"{path}: ヘッダに足りない（{len(b)} バイト）")
    ver, _, tick, _, si_len, si_off, n_vars, vh_off, _, buf_len = HEADER.unpack_from(b, 0)
    if ver != 2:
        raise ValueError(f"{path}: 未対応のヘッダの版 {ver}（対応: 2）")
    _, buf_off = VAR_BUF.unpack_from(b, 48)
    start_unix, _, _, _, n_rec = DISK.unpack_from(b, 112)

    fields: dict[str, IbtVar] = {}
    for i in range(n_vars):
        typ, off, count, name, desc, unit = VAR_HEADER.unpack_from(b, vh_off + i * VAR_HEADER.size)
        if typ not in TYPES:
            raise ValueError(f"{path}: 変数 {name!r} の型 {typ} を知らない")
        v = IbtVar(_cstr(name), TYPES[typ], off, count, _cstr(unit), _cstr(desc))
        fields[v.name] = v

    if n_rec <= 0:
        raise ValueError(f"{path}: ディスク用ヘッダの記録の数が {n_rec}（書き終えていない可能性がある）")
    expected = buf_off + n_rec * buf_len
    if len(b) != expected:
        raise ValueError(
            f"{path}: 記録 {n_rec} 件なら {expected} バイトのはずが {len(b)} バイト"
            "（書き終えていない可能性がある）"
        )
    dtype = np.dtype({
        "names": list(fields),
        "formats": [v.dtype if v.count == 1 else (v.dtype, (v.count,)) for v in fields.values()],
        "offsets": [v.offset for v in fields.values()],
        "itemsize": buf_len,
    })
    records = np.frombuffer(b, dtype=dtype, count=n_rec, offset=buf_off)
    dt = np.diff(records["SessionTime"])
    if np.any(dt < 0):
        raise ValueError(f"{path}: SessionTime が戻っている")
    # 実物では SessionTick は1ずつ進むのに、SessionTime が前の記録と同じ値になることがある
    # （1ファイルに数回。ほかの変数は進んでいる）。SessionTime は iRacing 自身の計時と一致するので
    # 時刻はこれを使い、同じ時刻の後ろの記録を落とす（SessionTick から時刻を作ると周のタイムが最大 1 tick ずれた）
    same = np.flatnonzero(dt == 0) + 1
    if same.size:
        warnings.warn(
            f"{path.name}: SessionTime が前と同じ記録 {same.size} 件を落とした", stacklevel=2
        )
        records = np.delete(records, same)
    return Ibt(
        tick_rate=tick,
        start_unix=start_unix,
        vars=fields,
        session_info=b[si_off : si_off + si_len].split(b"\0", 1)[0].decode("latin-1"),
        records=records,
    )


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1")


@dataclass(frozen=True)
class LapRecords:
    lap: np.ndarray  # 記録ごとの周の番号（リセットの 0 を直したもの）。-1 はガレージ
    resets: list[int]  # リセットを含む周
    broken_ends: list[int]  # 2 以上増える変化の直前の周（終わりを観測していない）


def lap_records(completed: np.ndarray) -> LapRecords:
    """`LapCompleted` から、リセットの 0 を戻した周の番号を求める。"""
    lap = completed.astype(np.int64).copy()
    n = len(lap)
    zero = np.r_[0, (lap == 0).astype(np.int8), 0]
    edges = np.flatnonzero(np.diff(zero))
    resets = []
    for a, b in zip(edges[::2], edges[1::2]):  # 0 の連なり [a, b)
        # 0 の後ろが同じ値、記録の末尾、ガレージ（-1）のどれかならリセット
        after = lap[b] if b < n else -1
        if 0 < a and lap[a - 1] >= 1 and after in (lap[a - 1], -1):
            lap[a:b] = lap[a - 1]
            resets.append(int(lap[a - 1]))
    kept = lap[lap >= 0]
    step = np.diff(kept)
    if np.any(step < 0):
        k = np.flatnonzero(step < 0)[0]
        raise ValueError(f"LapCompleted が {kept[k]} から {kept[k + 1]} に減った（リセット以外は想定しない）")
    broken = [int(kept[k]) for k in np.flatnonzero(step > 1)]
    return LapRecords(lap=lap, resets=resets, broken_ends=broken)


def own_lap_times(lap: np.ndarray, last: np.ndarray) -> pl.DataFrame:
    """1 増えた通過ごとに、終えた周と `LapLastLapTime` の更新後の値（なければ null）。"""
    changes = np.flatnonzero(np.diff(lap)) + 1
    ends = np.r_[changes[1:], len(lap)]  # 次の変化の記録（なければ末尾）
    rows = []
    for c, end in zip(changes, ends):
        if lap[c] - lap[c - 1] != 1:
            continue
        changed = np.flatnonzero(last[c:end] != last[c - 1])
        v = float(last[c + changed[0]]) if changed.size else None
        rows.append((int(lap[c - 1]), v if v is not None and v > 0 else None))
    return pl.DataFrame(rows, schema={"lap": pl.Int64, "lap_time_own": pl.Float64}, orient="row")


def lap_starts(
    t: np.ndarray, lap: np.ndarray, current: np.ndarray
) -> tuple[pl.DataFrame, list[int]]:
    """周ごとの開始時刻（`ingest.laps.lap_table` の入力）と、開始時刻を求められなかった周。

    `lap` は -1 を除いたもの。`LapCurrentLapTime` は周の数が変わった記録ではまだ前の周の経過時間で、
    あとで小さい値に戻る。戻った記録の時刻からその値を引いて開始時刻にする。
    次の変化までに戻らなければ（記録の末尾など）、開始時刻は周の数が変わった時刻で代え、
    その周と前の周を閉じていない周にする。
    """
    changes = np.flatnonzero(np.diff(lap)) + 1
    ends = np.r_[changes[1:], len(lap)]
    t_start, seen, unknown = [t[0]], [False], []
    for c, end in zip(changes, ends):
        k = c + np.flatnonzero(current[c:end] < current[c - 1:end - 1])
        if k.size:
            t_start.append(t[k[0]] - current[k[0]])
        else:
            t_start.append(t[c])
            unknown += [int(lap[c - 1]), int(lap[c])]
        seen.append(bool(k.size) and lap[c] - lap[c - 1] == 1)
    starts = pl.DataFrame({"lap": np.r_[lap[0], lap[changes]], "t_start": t_start, "start_seen": seen})
    return starts, unknown


def smooth_g(t: np.ndarray, accel: np.ndarray, tg: np.ndarray) -> np.ndarray:
    """加速度 [m/s²] を格子時刻 `tg` の G にし、窓 `G_WINDOW_S` で平滑化する。窓より短い区間は NaN。"""
    g = np.interp(tg, t, accel / G)
    window = round(G_WINDOW_S * RATE_HZ) | 1
    out = np.full_like(g, np.nan)
    cuts = np.flatnonzero(np.diff(tg) > 1.5 / RATE_HZ) + 1
    for a, b in zip(np.r_[0, cuts], np.r_[cuts, len(tg)]):
        if b - a >= window:
            out[a:b] = savgol_filter(g[a:b], window, 2)
    return out


def _flatten(ibt: Ibt, keep: np.ndarray) -> dict[str, np.ndarray]:
    cols = {}
    for v in ibt.vars.values():
        a = ibt.records[v.name][keep]
        if v.count == 1:
            cols[f"ibt.{v.name}"] = a
            continue
        for i in range(v.count):
            cols[f"ibt.{v.name}_{i}"] = a[:, i]
    return cols


def track_match(ibt: Ibt) -> dict[str, str]:
    """`iracing_transform.toml` と照合する値。"""
    return {"iracing_track": ibt.info("TrackName"), "iracing_config": ibt.info("TrackConfigName")}


def course_xy(ibt: Ibt, track_dir: Path = TRACK_DIR) -> tuple[Path, np.ndarray]:
    """コースのディレクトリと、全記録の局所座標（基準線に合わせたもの、N×2）。"""
    course_dir, sim = find_transform(TRANSFORM_FILE, track_match(ibt), track_dir)
    return course_dir, sim.apply(enu(ibt, course_dir))


def enu(ibt: Ibt, course_dir: Path) -> np.ndarray:
    origin = tomllib.loads((course_dir / "origin.toml").read_text(encoding="utf-8"))
    x, y = to_enu(ibt.col("Lat"), ibt.col("Lon"), origin["lat"], origin["lon"])
    return np.c_[x, y]


@dataclass(frozen=True)
class Laps:
    keep: np.ndarray  # 周に入れる記録（LapCompleted が -1 でない）。ibt の記録と同じ長さの bool
    t: np.ndarray  # keep した記録の SessionTime
    table: pl.DataFrame  # 周の表（`lap_table` の列と `lap_time_own`）

    def lap_of(self, t_abs: np.ndarray) -> np.ndarray:
        """時刻の周。開始時刻で振り分ける（LapCompleted の値ではない）。最初の周の開始より前は最初の周に入れる。"""
        t_start, ids = self.table["t_start"].to_numpy(), self.table["lap"].to_numpy()
        return ids[np.maximum(np.searchsorted(t_start, t_abs, side="right") - 1, 0)]

    def lap_time(self, t_abs: np.ndarray, lap: np.ndarray) -> np.ndarray:
        """周の開始からの時刻。"""
        t_start, ids = self.table["t_start"].to_numpy(), self.table["lap"].to_numpy()
        return t_abs - t_start[np.searchsorted(ids, lap)]


def split_laps(ibt: Ibt) -> Laps:
    """記録を周に分け、周の表を作る。閉じた周の規則はここだけに置く（`ingest` と `fit` で共用）。"""
    lr = lap_records(ibt.col("LapCompleted"))
    keep = lr.lap >= 0
    if not keep.any():
        raise ValueError("LapCompleted が 0 以上の記録がない（ガレージにいただけ）")
    t = ibt.col("SessionTime")[keep]
    lap = lr.lap[keep]
    if lr.resets:
        warnings.warn(f"ピットへのリセットを含む周を閉じていない周にした: {lr.resets}", stacklevel=2)
    if lr.broken_ends:
        warnings.warn(
            f"LapCompleted が 2 以上増えた。直前の周を閉じていない周にした: {lr.broken_ends}",
            stacklevel=2,
        )
    starts, unknown = lap_starts(t, lap, ibt.col("LapCurrentLapTime")[keep].astype(np.float64))
    if unknown:
        warnings.warn(f"開始時刻を求められなかった周の前後を閉じていない周にした: {unknown}", stacklevel=2)
    bad = pl.col("lap").is_in(lr.resets + lr.broken_ends + unknown)
    # lap_valid は to_common が判定し直すので、ここでは closed と lap_time だけを直す
    table = lap_table(starts).with_columns(closed=pl.col("closed") & ~bad).with_columns(
        lap_time=pl.when("closed").then("lap_time")
    ).join(
        own_lap_times(lap, ibt.col("LapLastLapTime")[keep]), on="lap", how="left",
        maintain_order="left",
    )
    return Laps(keep=keep, t=t, table=table)


@dataclass(frozen=True)
class Session:
    common: pl.DataFrame
    timed: pl.DataFrame  # 周ごとの 60 Hz の格子（`ingest.schema.TIMED_COLUMNS`）
    raw: pl.DataFrame
    laps: pl.DataFrame
    ibt: Ibt
    n_garage: int  # どの周にも入れなかった記録（LapCompleted が -1）
    dropped: dict[str, int]  # 距離の格子に使わなかった点（`ingest.derive.by_distance`）


def ingest(path: Path, track_dir: Path = TRACK_DIR) -> Session:
    ibt = read_ibt(path)
    if ibt.tick_rate != RATE_HZ:
        raise ValueError(f"{path}: 記録の周期が {ibt.tick_rate} Hz（{RATE_HZ} Hz だけを想定している）")
    course_dir, xy = course_xy(ibt, track_dir)
    session_id = datetime.fromtimestamp(ibt.start_unix, UTC).strftime("%Y%m%dT%H%M%SZ")

    laps = split_laps(ibt)
    keep, t = laps.keep, laps.t
    lap = laps.lap_of(t)
    raw = pl.DataFrame({
        "source": SOURCE,
        "session_id": session_id,
        "lap": pl.Series(lap, dtype=pl.Int64),
        "t": laps.lap_time(t, lap),
        **_flatten(ibt, keep),
    })

    d = derive(
        t, xy[keep, 0], xy[keep, 1], ibt.col("Speed")[keep].astype(np.float64), rate_hz=RATE_HZ
    )
    tg = d["t"].to_numpy()
    lap_g = laps.lap_of(tg)

    def interp(name: str) -> pl.Series:
        return pl.Series(np.interp(tg, t, ibt.col(name)[keep].astype(np.float64)))

    timed = d.with_columns(
        lap=pl.Series(lap_g, dtype=pl.Int64),
        t=pl.Series(laps.lap_time(tg, lap_g)),
        ax=pl.Series(smooth_g(t, ibt.col("LongAccel")[keep].astype(np.float64), tg)),
        ay=pl.Series(smooth_g(t, ibt.col("LatAccel")[keep].astype(np.float64), tg)),
        steer=pl.lit(None, dtype=pl.Float64),
        throttle=interp("Throttle"),
        brake=interp("Brake"),
    ).with_columns(pl.col("ax", "ay").fill_nan(None)).select(TIMED_COLUMNS)

    agrees = agrees_with_own_timing(laps.table, "iRacing")
    common, table, dropped = to_common(
        timed, laps.table, load_refline(course_dir), SOURCE, session_id, usable=agrees
    )
    return Session(
        common=common, timed=timed, raw=raw.select(*JOIN_KEYS, pl.exclude(JOIN_KEYS)), laps=table,
        ibt=ibt, n_garage=int((~keep).sum()), dropped=dropped,
    )


def fit(path: Path, course: str) -> None:
    """閉じた周の位置を基準線に合わせ、`track/<course>/iracing_transform.toml` を書く。"""
    ibt = read_ibt(path)
    course_dir = TRACK_DIR / course
    laps = split_laps(ibt)
    closed = laps.table.filter("closed")["lap"].to_numpy()
    if closed.size == 0:
        raise SystemExit(f"{path}: 閉じた周がない")
    in_closed = np.isin(laps.lap_of(laps.t), closed)
    src = enu(ibt, course_dir)[laps.keep][in_closed][::6]  # 10 Hz
    line = load_refline(course_dir)
    sim, dist = fit_similarity(src, np.r_[line.xy, line.xy[:1]])
    text = transform_toml(
        "# iRacing の Lat、Lon を origin.toml の原点で ENU に移した点から、基準線に合わせた局所座標への\n"
        "# 相似変換。ingest.ibt --fit が書く。p = scale * R(rotation_deg) @ (x, y) + (tx, ty)\n",
        track_match(ibt), sim, dist, path.name,
    )
    out = course_dir / TRANSFORM_FILE
    out.write_text(text, encoding="utf-8")
    print(text)
    print(out)


def main(argv: list[str]) -> None:
    if len(argv) == 3 and argv[0] == "--fit":
        fit(Path(argv[1]), argv[2])
        return
    if len(argv) != 2:
        raise SystemExit(
            "usage: python -m ingest.ibt <ibt> <outdir>\n"
            "       python -m ingest.ibt --fit <ibt> <course>"
        )
    s = ingest(Path(argv[0]))
    paths = write_session(s.common, s.raw, Path(argv[1]))
    print(
        f"{s.ibt.info('TrackName')} / {s.ibt.info('TrackConfigName')}、"
        f"{len(s.ibt.records)} 記録（ガレージの {s.n_garage} 記録はどの周にも入れていない）"
    )
    print(f"距離の格子に使わなかった 60 Hz の点: {s.dropped}")
    with pl.Config(tbl_rows=-1):
        print(s.laps)
    for path in paths:
        print(path)


if __name__ == "__main__":
    utf8_stdio()
    main(sys.argv[1:])
