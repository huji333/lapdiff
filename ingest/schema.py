"""共通スキーマ（SPEC「共通スキーマ（Parquet）」）と Parquet への書き出し。"""

from pathlib import Path

import polars as pl

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
