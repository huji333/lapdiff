"""周の表（開始時刻・タイム・`lap_valid`）。実車と AC で同じ規則を使う。

- 周回が閉じている（`closed`）：開始と終了の両方で計測線の通過を観測した周。
  開始は `start_seen`（ソースごとに判定して渡す）、終了は次の周があることで判定する。
  閉じていない周の `lap_time` は null。
- `lap_valid`：閉じた `lap >= 1` の周のうち、タイムが最速周の 107% 以内のもの。
  アウトラップ、最後の周（インラップか途中の周）、記録の途中から始まった周、極端に遅い周は false。
"""

import polars as pl

SLOW_FACTOR = 1.07


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
    timed = (pl.col("lap") >= 1) & pl.col("closed")
    best = laps.filter(timed)["lap_time"].min()
    valid = timed & (pl.col("lap_time") <= SLOW_FACTOR * best) if best is not None else pl.lit(False)
    return laps.select("lap", "t_start", "lap_time", "closed", lap_valid=valid)
