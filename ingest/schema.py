"""共通スキーマ（SPEC「共通スキーマ（Parquet）」）と Parquet への書き出し。"""

from pathlib import Path

import polars as pl

from ingest.derive import by_distance
from ingest.laps import with_validity
from ingest.track import Refline

COMMON_SCHEMA = pl.Schema(
    {
        "source": pl.String,
        "session_id": pl.String,
        "lap": pl.Int64,
        "lap_valid": pl.Boolean,
        "t": pl.Float64,
        "s": pl.Float64,
        "dist_norm": pl.Float64,
        "x": pl.Float64,
        "y": pl.Float64,
        "speed": pl.Float64,
        "ax": pl.Float64,
        "ay": pl.Float64,
        "kappa": pl.Float64,
        "steer": pl.Float64,
        "throttle": pl.Float64,
        "brake": pl.Float64,
    }
)

# 生チャンネルの Parquet が共通スキーマと結合するためのキー
JOIN_KEYS = ["source", "session_id", "lap", "t"]


# 周ごとの時刻格子の列。`to_common` の入力
TIMED_COLUMNS = ["lap", "t", "x", "y", "speed", "ax", "ay", "kappa", "steer", "throttle", "brake"]


# 周を比べてよいとみなす条件（`to_common`）
COVER_TOL = 5.0  # 計測線から計測線まで覆っているとみなす s の端の余裕 [m]
MAX_GAP = 10.0  # 使った点どうしの s の間隔の上限 [m]。2026-10-12 の有効な周では最大 4.5 m


def to_common(
    timed: pl.DataFrame,
    laps: pl.DataFrame,
    line: Refline,
    source: str,
    session_id: str,
    usable: pl.Expr | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, int]]:
    """周ごとの時刻格子を、基準線の距離の格子にして共通スキーマにする（実車と AC で共通）。

    `lap_valid` はここで判定し直す（`ingest.laps.with_validity`）。比べてよい周は次をすべて満たすもの。
    - 基準線を `0〜全長` まで覆っている（端の余裕 `COVER_TOL`）
    - 使った点どうしの `s` の間隔が `MAX_GAP` 以下（GPS の欠けや捨てた点を補間で埋めていない）
    - ソースが渡す `usable`（AC では自身のラップタイムとの一致）
    返り値は共通スキーマの表、`covered`・`max_gap_m` を足した周の表、使わなかった点の件数。
    """
    if timed.columns != TIMED_COLUMNS:
        raise ValueError(f"列は {TIMED_COLUMNS}: {timed.columns}")
    with_abs = timed.join(
        laps.select("lap", "t_start"), on="lap", how="left", maintain_order="left"
    ).with_columns(t_abs=pl.col("t") + pl.col("t_start")).drop("t_start")
    d, dropped, stats = by_distance(with_abs, line)
    laps = laps.join(stats, on="lap", how="left", maintain_order="left").with_columns(
        covered=(pl.col("s_first") <= COVER_TOL) & (pl.col("s_last") >= line.length - COVER_TOL),
    ).drop("s_first", "s_last")
    ok = pl.col("covered") & (pl.col("max_gap_m") <= MAX_GAP)
    laps = with_validity(laps, ok if usable is None else ok & usable)
    common = (
        d.with_columns(
            source=pl.lit(source),
            session_id=pl.lit(session_id),
            lap=pl.col("lap").cast(pl.Int64),
        )
        .join(laps.select("lap", "lap_valid"), on="lap", how="left", maintain_order="left")
        .select(COMMON_SCHEMA.names())
    )
    check_common(common)
    return common, laps, dropped


def check_common(df: pl.DataFrame) -> None:
    """列名・順序・型が共通スキーマと一致しなければ失敗させる。"""
    if df.schema != COMMON_SCHEMA:
        raise ValueError(f"共通スキーマと一致しない:\n期待 {COMMON_SCHEMA}\n実際 {df.schema}")


def check_raw(df: pl.DataFrame) -> None:
    """生チャンネルの先頭が結合キーで、型が共通スキーマと同じでなければ失敗させる。"""
    head = {k: df.schema.get(k) for k in JOIN_KEYS}
    if df.columns[: len(JOIN_KEYS)] != JOIN_KEYS or any(
        head[k] != COMMON_SCHEMA[k] for k in JOIN_KEYS
    ):
        raise ValueError(f"生チャンネルの結合キーが不正: {head}")


def write_parquet(df: pl.DataFrame, path: Path) -> None:
    """`.part` に書いてから rename する。書きかけのファイルを読ませない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    df.write_parquet(part)
    part.replace(path)


def write_session(common: pl.DataFrame, raw: pl.DataFrame, outdir: Path) -> tuple[Path, Path]:
    """`<outdir>/<source>/<session_id>/{telemetry,raw}.parquet` に書き出す。"""
    check_common(common)
    check_raw(raw)
    keys = common.select("source", "session_id").unique()
    if keys.height != 1:
        raise ValueError(f"1セッション分ではない: {keys.rows()}")
    source, session_id = keys.row(0)
    d = outdir / source / session_id
    paths = d / "telemetry.parquet", d / "raw.parquet"
    write_parquet(common, paths[0])
    write_parquet(raw, paths[1])
    return paths
