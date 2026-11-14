# lapdiff

実車（RaceChrono）と Assetto Corsa の走行データを、同じ処理で比較するための基盤。仕様は [SPEC.md](SPEC.md) を参照。

## 開発

```sh
uv sync
uv run pytest
uv run ruff check .

cd collector
GOOS=windows GOARCH=amd64 go build -o collector.exe .
```

## 取り込み

```sh
uv run python -m ingest.racechrono <csv> <outdir>
uv run python -m ingest.acraw <acraw.gz> <outdir>

# コースごとに一度。基準線を作ってから、AC の座標変換をそれに合わせる
uv run python -m ingest.track <racechrono csv> <course>          # track/<course>/refline.csv
uv run python -m ingest.ac_transform <acraw.gz> <course>          # track/<course>/ac_transform.toml
uv run python -m ingest.edges <course>                            # track/<course>/edges.csv（コースの端。地理院タイルを取得する）
```

## 分析

```sh
uv run python -m ingest.racechrono <csv> data/      # data/<source>/<session_id>/telemetry.parquet
LAPDIFF_DATA=data uv run marimo edit analysis/view1.py
```

- データの場所は環境変数 `LAPDIFF_DATA` で指定する。指定しなければリポジトリ直下の `data/` を読む。ノートブックの「データの場所」の欄でも変えられる
- `data/` と `*.parquet` は git に入れない（`.gitignore`）
- ビュー 1 の試作（`analysis/view1.py`）：コース図と、距離×速度・距離×タイム差（基準ラップとの累積タイム差）。既定では有効な周を全部重ね、基準ラップは実車の有効な周のうち最速の周にする。コース図で矩形を、距離のグラフで横の範囲をドラッグすると、もう一方の同じ区間が強調される

## web view

ビューは `web/`（Vite、TypeScript、Observable Plot）で作る。Python（`analysis.export`）がコースごとの JSON を `web/public/data/` に書き出し、web view はそれを描くだけにする。

```sh
cd web
npm ci
npm run data      # fixtures を一時ディレクトリに取り込み、web/public/data/okayama.json に書き出す
npm run dev       # 表示された URL をブラウザで開く
npm run typecheck
npm test
npm run build     # web/dist/ に静的ファイルを書く（public/data も含む）
```

fixtures 以外のデータは、リポジトリ直下で `uv run python -m analysis.export <data_dir> <course> web/public/data` で書き出す。

- ビュー 1：コース図と、距離×速度・距離×タイム差。既定は「基準ラップ」と「比較」の 2 周を描く（基準は青、比較は橙）。基準ラップの既定は marimo の試作と同じ。比較の既定は、基準と別のソース（実車と AC）の有効で欠けのない最速周。「複数の周を重ねる」を入れると、周の一覧のチェックで選んだ周を重ねる。コース図で矩形を、距離のグラフで横の範囲をドラッグすると、もう一方の同じ区間が赤く強調される。距離のグラフにカーソルを置くと、その s での各周の速度と Δt を表示し、コース図に各周の位置を点で示す
- 配置：左にコース図を画面の高さいっぱいに描く。右は上に距離×速度・距離×タイム差（全長）、下に G-G 図と、距離×前後G（`ax`）・距離×横G（`ay`）を置く。前後G・横G のグラフは一部の距離だけ見せて横にスクロールし、ほかのグラフで区間を選ぶとその区間まで送る。G-G 図は横軸を左右反転し、左旋回の点が左に出るようにした。区間を選ぶとその区間の軌跡を濃く描き、距離のグラフでカーソルを当てた s の点を打つ。G-G-V 包絡はまだ描かない（包絡を作る統計が SPEC で未決のため）
- 全体のビューとコーナーのビューは URL を分けた（`#overview`、`#corner/T3`）。ブラウザのタブを分けて開ける
- コーナーのライン：選んだコーナーの前後 80 m を拡大する。拡大図（各周の走行ラインとボトム速度の位置）、距離×横のずれ（基準線から左が正）、距離×速度、G-G 図、区間タイム・始点・ボトム・終点の速度の表を並べる。横のずれと区間の値は `analysis.export` が出す（`offset`、`corner_stats`）。ブレーキ開始点（`ax` が仮のしきい値 -0.3 G を下回り始めた点）を拡大図に ▼、距離のグラフに縦の破線で示し、距離×前後G も描く。ペダルを記録している周（AC）は、距離×ペダル（アクセルを上、ブレーキを下）も描く。全体のコース図には、各コーナーの記号の下に重ねる周の最低通過速度を周の色で並べる。コーナーの記号のボタンで、拡大するコーナーを選ぶ
- `web/public/data/` は git に入れない（`.gitignore`）

## collector の実行（Windows）

```bat
collector.exe -out D:\Sync\lapdiff-inbox
```

- `-out` は Syncthing の送信フォルダを指定する。既定値 `%USERPROFILE%\Sync\lapdiff-inbox` は仮のもので、フォルダが無ければ collector は起動時に止まる。Syncthing では `*.part` を無視させる
- AC より先に起動してよい。AC が共有メモリを作るまで待つ
- Ctrl-C かコンソールを閉じると、書きかけのファイルを確定してから終わる
- ファイル形式は [collector/FORMAT.md](collector/FORMAT.md) を参照
