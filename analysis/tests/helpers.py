"""テスト用に、共通スキーマの周を作って Parquet に書く。"""

from pathlib import Path

import numpy as np
import polars as pl

from ingest.schema import COMMON_SCHEMA, write_parquet

LENGTH = 100.5  # 格子は s = 0〜100


def lap_rows(source, session_id, lap, valid, s, t, length=LENGTH, xy=None):
    n = len(s)
    x, y = xy if xy is not None else (np.asarray(s, dtype=float), np.zeros(n))
    return pl.DataFrame(
        {
            "source": source,
            "session_id": session_id,
            "lap": lap,
            "lap_valid": valid,
            "t": np.asarray(t, dtype=float),
            "s": np.asarray(s, dtype=float),
            "dist_norm": np.asarray(s, dtype=float) / length,
            "x": x,
            "y": y,
            "speed": np.full(n, 40.0),
            "ax": np.zeros(n),
            "ay": np.zeros(n),
            "kappa": np.zeros(n),
            "steer": None,
            "throttle": None,
            "brake": None,
        },
        schema=COMMON_SCHEMA,
    )


def write(outdir: Path, *laps: pl.DataFrame) -> None:
    df = pl.concat(laps)
    for (source, session_id), part in df.group_by("source", "session_id"):
        write_parquet(part, outdir / source / session_id / "telemetry.parquet")


S = np.arange(101)
