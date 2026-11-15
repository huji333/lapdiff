"""周の表（開始時刻・タイム・`lap_valid`）。実車と AC で同じ規則を使う。

- 周回が閉じている（`closed`）：開始と終了の両方で計測線の通過を観測した周。
  開始は `start_seen`（ソースごとに判定して渡す）、終了は次の周があることで判定する。
  閉じていない周の `lap_time` は null。
- `lap_valid`：閉じた `lap >= 1` の周で、`usable` が true のもののうち、
  タイムがその中の最速周の 107% 以内のもの。アウトラップ、最後の周（インラップか途中の周）、
  記録の途中から始まった周、極端に遅い周は false。
- `usable`：ソースや後段の検査で、比べてよいと分かった周。`lap_table` の時点では全周 true で、
  距離の格子を作ったあと（`ingest.schema.to_common`）に、基準線を覆っていない周や
  欠けの大きい周を false にして `with_validity` で判定し直す。
  基準を「usable な周の中の最速」にするのは、計測線の誤検出などで異常に短い周が 1 つあっても、
  正常な周がまとめて無効にならないようにするため。
- `agrees_with_own_timing`：シムの周は、ラップタイムがシム自身の計時（`lap_time_own`）と
  `OWN_TIME_TOL` 以上違えば比べない（ポーズを含む周など）。自身の計時がない周（null）も比べない。
  AC と iRacing で同じ関数を使う。
"""

import warnings

import polars as pl

SLOW_FACTOR = 1.07
OWN_TIME_TOL = 0.05  # シム自身の計時との差の上限 [s]。AC の fixture では 2 ms


def lap_table(starts: pl.DataFrame) -> pl.DataFrame:
    """`lap, t_start, start_seen` の表（周の順）に `lap_time, closed, lap_valid` を足す。"""
    if starts.columns != ["lap", "t_start", "start_seen"]:
        raise ValueError(f"列は lap, t_start, start_seen: {starts.columns}")
    if not (starts["lap"].diff().drop_nulls() > 0).all():
        raise ValueError("lap が狭義単調増加でない")
    laps = starts.with_columns(t_next=pl.col("t_start").shift(-1)).with_columns(
        closed=pl.col("start_seen") & pl.col("t_next").is_not_null(),
    ).with_columns(
        # 閉じていない周の差分はラップタイムではないので出さない
        lap_time=pl.when("closed").then(pl.col("t_next") - pl.col("t_start")),
    )
    return with_validity(laps.select("lap", "t_start", "lap_time", "closed"), pl.lit(True))


def with_validity(laps: pl.DataFrame, usable: pl.Expr) -> pl.DataFrame:
    """`lap_valid` を（付け直して）返す。`usable` は周ごとの bool の式。"""
    timed = (pl.col("lap") >= 1) & pl.col("closed") & usable.fill_null(False)
    best = laps.filter(timed)["lap_time"].min()
    valid = timed & (pl.col("lap_time") <= SLOW_FACTOR * best) if best is not None else pl.lit(False)
    return laps.drop("lap_valid", strict=False).with_columns(lap_valid=valid)


def agrees_with_own_timing(laps: pl.DataFrame, sim: str) -> pl.Expr:
    """`lap_time_own` の列を持つ周の表について、自身の計時と合う周を true にする式を返す。

    合わない閉じた周は、警告に並べてから false にする（`to_common` の `usable` に渡す）。
    """
    diff = (pl.col("lap_time") - pl.col("lap_time_own")).abs()
    bad = laps.filter(pl.col("closed") & (diff.is_null() | (diff > OWN_TIME_TOL)))
    if bad.height:
        warnings.warn(
            f"{sim} の計時と {OWN_TIME_TOL} s 以上違うか、計時がない周を無効にした: "
            f"{bad.select('lap', 'lap_time', 'lap_time_own').rows()}",
            stacklevel=2,
        )
    return diff <= OWN_TIME_TOL
