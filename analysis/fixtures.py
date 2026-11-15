"""fixtures の実車・AC・iRacing のファイルを一時ディレクトリに取り込み、web view 用に書き出す。

使い方（リポジトリ直下で）：`python -m analysis.fixtures [outdir]`（既定 `web/public/data`）。
`npm run data` から呼ぶ。bash に頼らないので Windows でもそのまま動く。
"""

import sys
import tempfile
from pathlib import Path

from analysis import export
from ingest import acraw, ibt, racechrono
from ingest.cli import utf8_stdio
from ingest.track import TRACK_DIR

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
COURSE = "okayama"


def build(outdir: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        for csv in sorted((FIXTURES / "racechrono").glob("*.csv")):
            racechrono.main([str(csv), tmp])
        acraw.main([str(FIXTURES / "ac/okayama_mx5nd_lap2.acraw.gz"), tmp])
        for f in sorted((FIXTURES / "iracing").glob("*.ibt")):
            ibt.main([str(f), tmp])
        doc = export.export_course(Path(tmp), TRACK_DIR / COURSE)
    path = export.write_export(doc, outdir)
    print(f"{path}: {len(doc['laps'])} 周、既定の基準ラップ {doc['default_ref']}")
    return path


def main(argv: list[str]) -> None:
    if len(argv) > 1:
        raise SystemExit("usage: python -m analysis.fixtures [outdir]")
    build(Path(argv[0]) if argv else ROOT / "web/public/data")


if __name__ == "__main__":
    utf8_stdio()
    main(sys.argv[1:])
