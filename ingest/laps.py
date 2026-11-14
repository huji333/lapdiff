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
    return with_validity(laps.select("lap", "t_start", "lap_time", "closed"), pl.lit(True))


def with_validity(laps: pl.DataFrame, usable: pl.Expr) -> pl.DataFrame:
    """`lap_valid` を（付け直して）返す。`usable` は周ごとの bool の式。"""
    timed = (pl.col("lap") >= 1) & pl.col("closed") & usable.fill_null(False)
    best = laps.filter(timed)["lap_time"].min()
    valid = timed & (pl.col("lap_time") <= SLOW_FACTOR * best) if best is not None else pl.lit(False)
    return laps.drop("lap_valid", strict=False).with_columns(lap_valid=valid)
