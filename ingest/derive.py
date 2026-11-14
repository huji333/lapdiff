"""派生チャンネルの計算（SPEC「派生チャンネルの計算」の手順1〜4）。

ソースに依存しない。実車も AC も `(t, x, y, speed)` を渡して同じ関数で計算する。

- 再標本化：絶対時刻で 1/rate_hz 秒の倍数に揃えた格子へ線形補間する。
  `max_gap_s` を超える時刻の飛び（AC のポーズなど）では区間を分け、飛びをまたいで補間しない。
- 平滑化：Savitzky-Golay（窓 `window_s`、3次）。位置・速度とその微分を同じフィルタで求める。
  ゼロ位相なので時間の遅れは出ないが、窓の幅だけ G の変化がなまる。
  窓 1.5 s は 2026-10-12 の実データで決めた。1.1 s では GPS 速度の段差で `ax` が -2 G を超えた。
  1.5 s では、各周の `ax` の最大・最小と `|ay|` の上位1%点が、RaceChrono の `calc` 列と 0.1 G 以内で一致する。
- `ax` [G]：平滑化した `speed` の時間微分。位置の2階微分より雑音が小さい。
- `kappa` [1/m]：平滑化した軌跡の符号付き曲率 (x'y'' - y'x'') / (x'^2 + y'^2)^1.5。
- `ay` [G]：speed^2 * kappa / g。
- 符号：x を東、y を北にとる右手系で、左旋回（反時計回り）を正とする。
  `kappa > 0` と `ay > 0` はどちらも左旋回。RaceChrono の `lateral_acc|calc` と同じ向き。
- `speed < min_speed` の標本では `kappa` と `ay` を null にする（停止中は曲率が定まらない）。
- `derive` は手順4までで、`s` と `dist_norm` を null で出す。
- 手順5は `by_distance`：周ごとに基準線へ射影して `s` を求め、距離 `STEP` [m] 刻みの格子に
  ほかの列（`t` を含む）を線形補間する。計測線をまたぐ所は前後の周の点も使う。
  次の点は使わず、件数を返す。
  - 基準線から `MAX_OFFSET` [m] より離れた点（ピットレーンなど）
  - それまでの最大の `s` を超えない点（停止、後退）
  `dist_norm` は `s / 基準線の全長`。
"""

import numpy as np
import polars as pl
from scipy.signal import savgol_filter

from ingest.track import STEP, Refline, project

G = 9.80665

DERIVED_COLUMNS = ["t", "x", "y", "speed", "ax", "ay", "kappa", "s", "dist_norm"]
MAX_OFFSET = 30.0


def derive(
    t: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    speed: np.ndarray,
    *,
    rate_hz: float = 10.0,
    window_s: float = 1.5,
    polyorder: int = 3,
    max_gap_s: float = 0.5,
    min_speed: float = 2.0,
) -> pl.DataFrame:
    """`(t [s], x [m], y [m], speed [m/s])` から 10 Hz の派生チャンネルを返す。

    `t` は狭義単調増加であること。返す `t` は入力と同じ時刻原点の格子時刻。
    窓より短い区間では微分を出せないため、`ax`、`ay`、`kappa` を null にする。
    """
    t, x, y, speed = (np.asarray(a, dtype=np.float64) for a in (t, x, y, speed))
    n = len(t)
    if not (len(x) == len(y) == len(speed) == n):
        raise ValueError(f"入力の長さが揃っていない: t={n} x={len(x)} y={len(y)} speed={len(speed)}")
    if n == 0:
        raise ValueError("入力が空")
    for name, a in (("t", t), ("x", x), ("y", y), ("speed", speed)):
        if not np.all(np.isfinite(a)):
            raise ValueError(f"{name} に NaN/inf がある")
    if np.any(np.diff(t) <= 0):
        raise ValueError("t が狭義単調増加でない")

    dt = 1.0 / rate_hz
    window = round(window_s * rate_hz) | 1  # 奇数にする
    if window <= polyorder:
        raise ValueError(f"窓 {window} 点が多項式の次数 {polyorder} 以下")

    cuts = np.flatnonzero(np.diff(t) > max_gap_s) + 1
    bounds = zip(np.r_[0, cuts], np.r_[cuts, n])
    parts = [
        _derive_segment(t[a:b], x[a:b], y[a:b], speed[a:b], dt, window, polyorder, min_speed)
        for a, b in bounds
    ]
    out = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}

    return pl.DataFrame(out).with_columns(
        pl.col("ax", "ay", "kappa").fill_nan(None),
        pl.lit(None, dtype=pl.Float64).alias("s"),
        pl.lit(None, dtype=pl.Float64).alias("dist_norm"),
    ).select(DERIVED_COLUMNS)


def _derive_segment(t, x, y, speed, dt, window, polyorder, min_speed) -> dict[str, np.ndarray]:
    # 格子は絶対時刻で dt の倍数に揃える。区間やソースが違っても標本時刻が一致する
    k0, k1 = np.ceil(t[0] / dt - 1e-9), np.floor(t[-1] / dt + 1e-9)
    tg = np.arange(k0, k1 + 1) * dt
    xg, yg, vg = (np.interp(tg, t, a) for a in (x, y, speed))
    nan = np.full(len(tg), np.nan)
    if len(tg) < window:
        return {"t": tg, "x": xg, "y": yg, "speed": vg, "ax": nan, "ay": nan, "kappa": nan}

    def sg(a, deriv=0):
        return savgol_filter(a, window, polyorder, deriv=deriv, delta=dt)

    xs, ys, vs = sg(xg), sg(yg), sg(vg)
    dx, dy, ddx, ddy = sg(xg, 1), sg(yg, 1), sg(xg, 2), sg(yg, 2)
    ax = sg(vg, 1) / G
    with np.errstate(divide="ignore", invalid="ignore"):
        kappa = (dx * ddy - dy * ddx) / (dx**2 + dy**2) ** 1.5
    kappa = np.where(vs >= min_speed, kappa, np.nan)
    ay = vs**2 * kappa / G
    return {"t": tg, "x": xs, "y": ys, "speed": vs, "ax": ax, "ay": ay, "kappa": kappa}


def by_distance(
    timed: pl.DataFrame, line: Refline, max_gap_s: float = 0.5
) -> tuple[pl.DataFrame, dict[str, int], pl.DataFrame]:
    """周ごとの時刻格子を、基準線の距離 1 m 刻みに取り直す。

    `timed` は `lap`、`t`（周の開始からの秒）、`t_abs`（全周で共通の時刻）、`x`、`y` とほかの数値列。
    返り値は `s`、`dist_norm` を足した表（`t_abs` は除く）、使わなかった点の件数、
    周ごとの `s_first`、`s_last`（使った点の最初と最後の `s`）と `max_gap_m`（使った点どうしの
    `s` の最大の間隔）。格子はこの間隔を線形補間で埋めているので、大きければその区間は信用できない。

    - 計測線の付近を補間できるよう、前後の周の隣の点（時刻の飛びが `max_gap_s` 以下）も使う。
    - 周の `s` は、`[0, 全長]` から外れる点が最も少なくなる周回に置く。
      格子は `[0, 全長)` の範囲だけ作る。範囲の外は前後の周の区間である。
    """
    timed = timed.sort("t_abs")
    cols = [c for c in timed.columns if c not in ("lap", "t_abs", "s", "dist_norm")]
    lap_arr = timed["lap"].to_numpy()
    t_abs = timed["t_abs"].to_numpy()
    xy_all = np.c_[timed["x"], timed["y"]]
    values = {c: timed[c].cast(pl.Float64).fill_null(np.nan).to_numpy() for c in cols}
    n = len(lap_arr)
    L = line.length

    parts = []
    stats = []
    dropped = {"off_line": 0, "not_advancing": 0}
    bounds = np.flatnonzero(np.diff(lap_arr)) + 1
    for a, b in zip(np.r_[0, bounds], np.r_[bounds, n], strict=True):
        lap = lap_arr[a]
        t_start = t_abs[a] - values["t"][a]
        lo = a - 1 if a > 0 and t_abs[a] - t_abs[a - 1] <= max_gap_s else a
        hi = b + 1 if b < n and t_abs[b] - t_abs[b - 1] <= max_gap_s else b
        own = np.zeros(hi - lo, dtype=bool)
        own[a - lo : b - lo] = True

        s, d = project(xy_all[lo:hi], line)
        on = d <= MAX_OFFSET
        dropped["off_line"] += int(np.sum(~on & own))
        idx = np.flatnonzero(on)
        if len(idx) == 0:
            continue
        s = s[idx]
        adv = np.r_[True, s[1:] > np.maximum.accumulate(s)[:-1]]
        dropped["not_advancing"] += int(np.sum(~adv & own[idx]))
        s, idx = s[adv], idx[adv] + lo

        k = min(range(-2, 3), key=lambda k: (np.sum((s + k * L < -5) | (s + k * L > L + 5)),
                                             abs(s[0] + k * L)))
        s = s + k * L
        gap = float(np.max(np.diff(s))) if len(s) > 1 else np.inf
        stats.append((lap, float(s[0]), float(s[-1]), gap))
        grid = np.arange(np.ceil(max(s[0], 0) / STEP), np.floor(min(s[-1], L) / STEP) + 1) * STEP
        grid = grid[grid < L] + 0.0  # -0.0 を 0.0 にする
        if len(grid) == 0:
            continue
        out = {"lap": np.full(len(grid), lap), "s": grid}
        for c in cols:
            v = t_abs[idx] - t_start if c == "t" else values[c][idx]
            out[c] = np.interp(grid, s, v)
        parts.append(pl.DataFrame(out))
    df = pl.concat(parts).with_columns(
        pl.col(cols).fill_nan(None),
        dist_norm=pl.col("s") / L,
    )
    lap_stats = pl.DataFrame(
        stats, schema=["lap", "s_first", "s_last", "max_gap_m"], orient="row"
    ).with_columns(pl.col("lap").cast(pl.Int64))
    return df, dropped, lap_stats
