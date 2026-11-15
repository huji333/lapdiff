"""iRacing の telemetry フォルダの `.ibt` を取り込み、web view 用に書き出す。

使い方（リポジトリ直下で）：
- 一度だけ：`python -m ingest.iracing_dir`
- 見張る：`python -m ingest.iracing_dir --watch`（新しい `.ibt` ができるたびに取り込んで書き出す）

- フォルダ：`--dir`、環境変数 `LAPDIFF_IRACING_DIR`、`ドキュメント/iRacing/telemetry` の順に探す
  （ドキュメントが OneDrive にあるときはそちらも見る）。
- Garage 61 から書き出した周の CSV（`Garage 61 - *.csv`）も、`--garage61-dir`（既定はダウンロード）から
  取り込む（`ingest.garage61`）。ほかの人の速い周を基準ラップにするため。`--no-garage61` で探さない。
- 取り込み先は `analysis.data.default_data_dir()`（`LAPDIFF_DATA`、既定はリポジトリ直下の `data/`）。
  ほかのソースと同じく `<data>/iracing/<session_id>/` に書く。
- 取り込んだファイルは `<data>/iracing/ingested.json` に大きさと更新時刻を残し、変わったものだけ取り込み直す。
  失敗したファイルも理由を残し、ファイルが変わるまで繰り返さない。
- 更新から `--settle` 秒（既定 30）たっていないファイルは、iRacing が書いている途中かもしれないので後回しにする。
- 取り込んだ周があるコースごとに `analysis.export` で書き出す（既定 `web/public/data`）。
  web view はブラウザを再読み込みすると新しい周を読む。
"""

import argparse
import json
import os
import sys
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from analysis import export
from analysis.data import default_data_dir
from ingest import garage61, ibt
from ingest.ac_transform import TRACK_DIR, find_transform
from ingest.cli import utf8_stdio
from ingest.schema import write_session

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = "ingested.json"
SETTLE_S = 30.0
WATCH_S = 10.0
GARAGE61_GLOB = "Garage 61 - *.csv"


def find_telemetry_dir() -> Path:
    env = os.environ.get("LAPDIFF_IRACING_DIR")
    if env:
        return Path(env)
    home = Path.home()
    candidates = [home / "Documents"]
    if os.environ.get("OneDrive"):
        od = Path(os.environ["OneDrive"])
        candidates += [od / "Documents", od / "ドキュメント"]
    for docs in candidates:
        d = docs / "iRacing" / "telemetry"
        if d.is_dir():
            return d
    raise SystemExit(
        "iRacing の telemetry フォルダが見つからない。--dir か LAPDIFF_IRACING_DIR で指定する"
        f"（探した場所: {', '.join(str(c / 'iRacing/telemetry') for c in candidates)}）"
    )


@dataclass
class State:
    path: Path
    files: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path) -> "State":
        path = data_dir / ibt.SOURCE / STATE_FILE
        files = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls(path, files)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.files, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )

    def courses(self) -> set[str]:
        return {v["course"] for v in self.files.values() if "course" in v}


def _stamp(st: os.stat_result) -> dict:
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def find_files(src: Path, g61_dir: Path | None) -> list[Path]:
    """取り込む候補：`src` の `.ibt` と、`g61_dir` の Garage 61 の CSV（古い順）。"""
    files = list(src.glob("*.ibt"))
    if g61_dir is not None:
        files += g61_dir.glob(GARAGE61_GLOB)
    return sorted(files, key=lambda p: p.stat().st_mtime)


def ingest_file(path: Path, data_dir: Path) -> dict:
    """1 ファイルを取り込み、状態に残す値（session_id、コース、周の数）を返す。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if path.suffix.lower() == ".csv":
            s = garage61.ingest(path)
            course_dir = s.course_dir
        else:
            s = ibt.ingest(path)
            course_dir, _ = find_transform(ibt.TRANSFORM_FILE, ibt.track_match(s.ibt), TRACK_DIR)
        write_session(s.common, s.raw, data_dir)
    for w in caught:
        print(f"  警告: {w.message}")
    session_id = s.common["session_id"][0]
    valid = int(s.laps["lap_valid"].sum())
    best = s.laps.filter("lap_valid")["lap_time"].min()
    best_s = "" if best is None else f"、最速 {best:.3f} s"
    print(f"  {course_dir.name} / {session_id}: {s.laps.height} 周（有効 {valid}{best_s}）")
    return {"session_id": session_id, "course": course_dir.name}


def scan(files: list[Path], data_dir: Path, settle_s: float, state: State, pending: set[str]) -> bool:
    """`files` のうち新しいか変わったファイルを取り込む。取り込めたファイルがあれば True。"""
    changed = False
    now = time.time()
    for path in files:
        st = path.stat()
        stamp = _stamp(st)
        prev = state.files.get(path.name)
        if prev is not None and all(prev.get(k) == v for k, v in stamp.items()):
            continue
        if now - st.st_mtime < settle_s:
            if path.name not in pending:
                print(f"後回し（書いている途中かもしれない）: {path.name}")
                pending.add(path.name)
            continue
        pending.discard(path.name)
        print(f"取り込み: {path.name}")
        try:
            entry = ingest_file(path, data_dir)
            changed = True
        except (ValueError, OSError) as e:  # 1 ファイルの失敗で全体を止めない。理由は状態に残す
            print(f"  失敗: {e}")
            entry = {"error": str(e)}
        state.files[path.name] = stamp | entry
        state.save()
    return changed


def export_all(data_dir: Path, courses: set[str], outdir: Path) -> None:
    for course in sorted(courses):
        try:
            doc = export.export_course(data_dir, TRACK_DIR / course)
        except ValueError as e:
            print(f"書き出しに失敗（{course}）: {e}")
            continue
        path = export.write_export(doc, outdir)
        print(f"書き出し: {path}（{len(doc['laps'])} 周）。ブラウザを再読み込みすると反映される")


def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(
        prog="python -m ingest.iracing_dir", description=__doc__.split("\n")[0]
    )
    p.add_argument("--dir", type=Path, help="iRacing の telemetry フォルダ")
    p.add_argument("--data", type=Path, help="取り込み先（既定は LAPDIFF_DATA か data/）")
    p.add_argument("--out", type=Path, default=ROOT / "web/public/data", help="書き出し先")
    p.add_argument("--watch", nargs="?", type=float, const=WATCH_S, metavar="秒",
                   help=f"見張り続ける（既定 {WATCH_S:g} 秒ごと）")
    p.add_argument("--settle", type=float, default=SETTLE_S, metavar="秒",
                   help=f"更新からこの秒数たったファイルだけ取り込む（既定 {SETTLE_S:g}）")
    p.add_argument("--garage61-dir", type=Path, default=Path.home() / "Downloads",
                   help="Garage 61 の CSV を探すフォルダ（既定はダウンロード）")
    p.add_argument("--no-garage61", action="store_true", help="Garage 61 の CSV を探さない")
    args = p.parse_args(argv)

    src = args.dir or find_telemetry_dir()
    if not src.is_dir():
        raise SystemExit(f"フォルダがない: {src}")
    g61 = None if args.no_garage61 or not args.garage61_dir.is_dir() else args.garage61_dir
    data_dir = args.data or default_data_dir()
    state = State.load(data_dir)
    print(f"iRacing: {src}\nGarage 61: {g61 or '（探さない）'}\n"
          f"取り込み先: {data_dir}\n書き出し先: {args.out}")

    def scan_all() -> bool:
        return scan(find_files(src, g61), data_dir, args.settle, state, pending)

    pending: set[str] = set()
    scan_all()
    # 最初は、新しいファイルがなくても書き出す（web/public/data が古いか、まだないことがある）
    export_all(data_dir, state.courses(), args.out)
    if args.watch is None:
        return
    print(f"見張っています（{args.watch:g} 秒ごと）。Ctrl-C で終わる")
    try:
        while True:
            time.sleep(args.watch)
            if scan_all():
                export_all(data_dir, state.courses(), args.out)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    utf8_stdio()
    main(sys.argv[1:])
