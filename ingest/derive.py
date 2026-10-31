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
- 手順5（基準線への射影）は後のタスクで実装する。`s` と `dist_norm` はすべて null で出す。
"""

import numpy as np
import polars as pl
from scipy.signal import savgol_filter

G = 9.80665

DERIVED_COLUMNS = ["t", "x", "y", "speed", "ax", "ay", "kappa", "s", "dist_norm"]


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
