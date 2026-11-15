"""共通 Parquet の読み込み、周の一覧、基準ラップとのタイム差（SPEC「analysis」）。

- データの場所：`<data_dir>/<source>/<session_id>/telemetry.parquet`（ingest が書く）。
  `data_dir` の既定は環境変数 `LAPDIFF_DATA`、なければリポジトリ直下の `data/`。
- 一覧：DuckDB のビュー `telemetry`（全 Parquet）、`laps`（周ごと）、`sessions`（セッションごと）で導出する。
  別のテーブルには保存しない（SPEC「セッションとラップの一覧」）。
- 周の識別子 `label` は `<source> <session_id> #<lap>`。
- `complete`：周の `s` が格子の始点から終点までそろっている（両端の欠けが `COMPLETE_GAP` [m] 以下）。
  計測線の前後だけの断片（アウトラップの末尾など）は false になる。
- `t_end`：周の最後の格子点（`s` が最大の行）での `t`。全長の格子は `floor(全長)` で終わるので、
  ラップタイムより最後の 1 m 弱の分だけ短い。周どうしの比較にだけ使う。
- タイム差：`dt(s) = t_lap(s) - t_ref(s)`。同じ `s` の格子の行どうしで引く。
  `complete` でない周は `t` の原点（周の開始）が比べられないため、`dt` を null にする。
  web view（`web/`）は基準ラップを画面で選ぶので、同じ計算を TypeScript でも行う
  （書き出しは `analysis.export`。全組み合わせを先に計算すると周の数の 2 乗になるため）。
  web view に写した処理の一覧は `analysis/export.py` の docstring にある。変えたらそちらも合わせる。
"""

import os
from pathlib import Path

import duckdb
import polars as pl

REPO_DIR = Path(__file__).resolve().parents[1]
COMPLETE_GAP = 5.0
# `telemetry.parquet` の全長（s / dist_norm）と基準線の全長の差の許容 [m]
LENGTH_TOL = 0.1

LAPS_SQL = f"""
CREATE VIEW laps AS
SELECT
    source || ' ' || session_id || ' #' || lap AS label,
    source,
    session_id,
    lap,
    bool_and(lap_valid) AS lap_valid,
    count(*) AS n,
    min(s) AS s_min,
    max(s) AS s_max,
    arg_max(t, s) AS t_end,
    max(s / dist_norm) FILTER (WHERE s > 0) AS length,
    min(s) <= {COMPLETE_GAP} AND max(s) >= length - 1 - {COMPLETE_GAP} AS complete
FROM telemetry
GROUP BY source, session_id, lap
"""

SESSIONS_SQL = """
CREATE VIEW sessions AS
SELECT
    source,
    session_id,
    count(*) AS n_laps,
    count(*) FILTER (WHERE lap_valid AND complete) AS n_valid,
    min(t_end) FILTER (WHERE lap_valid AND complete) AS best_t_end
FROM laps
GROUP BY source, session_id
"""


def default_data_dir() -> Path:
    env = os.environ.get("LAPDIFF_DATA")
    return Path(env) if env else REPO_DIR / "data"


def open_db(data_dir: Path) -> duckdb.DuckDBPyConnection:
    """`data_dir` の下の `telemetry.parquet` 全部をビュー `telemetry`、`laps`、`sessions` にする。"""
    if not data_dir.is_dir():
        raise FileNotFoundError(f"{data_dir} がない")
    files = sorted(data_dir.glob("*/*/telemetry.parquet"))
    if not files:
        raise FileNotFoundError(
            f"{data_dir}/<source>/<session_id>/telemetry.parquet が 1 つもない"
            "（python -m ingest.racechrono か ingest.acraw で作る）"
        )
    con = duckdb.connect()
    paths = ", ".join(f"'{p.as_posix()}'" for p in files)
    con.execute(f"CREATE VIEW telemetry AS SELECT * FROM read_parquet([{paths}])")
    con.execute(LAPS_SQL)
    con.execute(SESSIONS_SQL)
    return con


def lap_list(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    return con.sql("SELECT * FROM laps ORDER BY source, session_id, lap").pl()


def check_length(laps: pl.DataFrame, length: float) -> None:
    """周の全長が基準線の全長と違えば失敗させる（別のコースや古い基準線で作った Parquet）。"""
    bad = laps.filter((pl.col("length") - length).abs() > LENGTH_TOL)
    if bad.height:
        raise ValueError(
            f"基準線の全長 {length:.3f} m と違う全長で作った周がある:\n"
            f"{bad.select('label', 'length')}"
        )


def default_ref(laps: pl.DataFrame) -> str | None:
    """既定の基準ラップ：実車の有効で欠けのない周のうち `t_end` が最小の周。なければ None。"""
    cand = laps.filter(
        (pl.col("source") == "racechrono") & pl.col("lap_valid") & pl.col("complete")
    )
    if cand.height == 0:
        return None
    return cand.sort("t_end")["label"][0]


def load_laps(con: duckdb.DuckDBPyConnection, labels: list[str]) -> pl.DataFrame:
    """`labels` の周の行を、`label` と `complete` を付けて返す。ない周があれば失敗させる。"""
    found = con.sql(
        "SELECT label FROM laps WHERE list_contains(?, label)", params=[labels]
    ).pl()["label"]
    missing = sorted(set(labels) - set(found))
    if missing:
        raise ValueError(f"Parquet にない周: {missing}")
    return con.sql(
        """
        SELECT l.label, l.complete, t.*
        FROM telemetry t
        JOIN laps l USING (source, session_id, lap)
        WHERE list_contains(?, l.label)
        ORDER BY l.label, t.s
        """,
        params=[labels],
    ).pl()


def time_delta(tel: pl.DataFrame, ref: str) -> pl.DataFrame:
    """`tel`（`load_laps` の返り値）に、基準ラップ `ref` とのタイム差 `dt` [s] を足す。"""
    ref_rows = tel.filter(pl.col("label") == ref)
    if ref_rows.height == 0:
        raise ValueError(f"基準ラップ {ref} が読み込んだ周にない")
    if not ref_rows["complete"][0]:
        raise ValueError(f"基準ラップ {ref} は s が欠けている周（complete でない）")
    t_ref = ref_rows.select("s", t_ref="t")
    return (
        tel.join(t_ref, on="s", how="left", maintain_order="left")
        .with_columns(
            dt=pl.when(pl.col("complete")).then(pl.col("t") - pl.col("t_ref")),
        )
        .drop("t_ref")
    )


def final_delta(tel: pl.DataFrame) -> pl.DataFrame:
    """周ごとの、`dt` が出ている最後の `s` での `dt`（`time_delta` の返り値から）。

    web view に写してある（一覧は `analysis/export.py` の docstring）。
    """
    return (
        tel.filter(pl.col("dt").is_not_null())
        .group_by("label", maintain_order=True)
        .agg(s=pl.col("s").max(), dt=pl.col("dt").sort_by("s").last())
    )
