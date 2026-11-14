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

書き出しの形には版（`analysis/export.py` と `web/src/data.ts` の `SCHEMA`）がある。版の違う JSON は読み込みで失敗するので、`npm run data` で書き出し直す。形を変えたら両方の版を上げ、`LAPDIFF_UPDATE_GOLDEN=1 uv run pytest analysis/tests/test_export_contract.py` で `web/src/fixtures/contract.json` を書き直す（vitest がこれを読み、Python と TS の写しを突き合わせる）。

- ビュー 1：コース図と、距離×速度・距離×タイム差。既定は「基準ラップ」と「比較」の 2 周を描く（基準は青、比較は橙）。基準ラップの既定は marimo の試作と同じ。比較の既定は、基準と別のソース（実車と AC）の有効で欠けのない最速周。基準と比較の周は、ツールバーのチップを押して開く一覧で選ぶ（無効な周も出すかの切り替えもここにある）。「重ねる」にすると、周の一覧のチェックで選んだ周を重ねる。コース図で矩形を、距離のグラフで横の範囲をドラッグすると、もう一方の同じ区間が紫で強調され、ツールバーに選んだ範囲のチップが出る。クリックかダブルクリック、Esc、チップの ✕ で解除する。距離のグラフにカーソルを置くと、その s での各周の速度と Δt を表示し、コース図に各周の位置を点で示す。2 周で比べるときは、コース図の各コーナーの記号の下に 2 周の最低通過速度を周の色で出す
- 自己ベストとの差：2 周で比べるときは、比較の周の区間タイムと、同じソースの有効な周（全セッション）の区間ごとの最速との差をパネルに並べ、コース図を 50 m ごとの同じ差で塗る。コーナーのビューのチップにも、そのコーナーの差を値と赤い点の濃さで添える（`min_diff_s` 未満は薄く）
- 配置：左にコース図を画面の高さいっぱいに描く。右は上に距離×速度・距離×タイム差（全長）、下に G-G 図を置く。G-G 図は横軸を左右反転し、左旋回の点が左に出るようにした。区間を選ぶとその区間の軌跡を濃く描き、距離のグラフでカーソルを当てた s の点を打つ。G-G-V 包絡はまだ描かない（包絡を作る統計が SPEC で未決のため）
- 全体のビューとコーナーのビューは URL を分けた（`#overview`、`#corner/T3`）。ブラウザのタブを分けて開ける。キーは 1 / 2 でビュー、← / → でコーナーを移る
- ツールバー：画面の上に貼り付け、スクロールしても残す。色はライトだけで、`web/src/style.css` の `:root` の変数にまとめた。周の線と損失の色の正は TS の定数（`charts.ts`、`sections.ts`）で、`main.ts` が同じ値を CSS の変数に書く
- コーナー：選んだコーナーの前後 80 m を拡大する。ブレーキ開始点がそれより手前の周があれば、ブレーキ開始点の 20 m 手前まで広げる。拡大図（各周の走行ラインとボトム速度の位置）、距離×横のずれ（基準線＝実車の有効な周の平均線から。軸の名前は「実車平均線」。コーナーの内側が正）、距離×速度、G-G 図、表を並べる。表は区間タイム、ブレーキ開始点、最大減速度、始点・ボトム・終点の速度で、基準ラップの行は値、ほかの行は基準ラップとの差を出す（基準にその値がない列は値のまま）。「重ねる」で基準ラップを重ねていなくても、表の先頭には基準ラップの行を出す。横のずれと区間の値は `analysis.export` が出す（`offset`、`section_times`、`corner_stats`）。コーナーの区間は、`corners.toml` の区間を、隣の区間との 10 m 以下の隙間を含めて延ばした区間（`sections`）で、区間タイムも速度もこの区間で求める。内側の向きは `corners.toml` の `r_apex` の符号から決める。ブレーキ開始点（`ax` が仮のしきい値 -0.3 G を下回り始めた点）を拡大図に進む向きを指す三角、距離のグラフに縦の破線で示し、距離×前後G も描く。ペダルを記録している周（AC）は、距離×ペダル（アクセルを上、ブレーキを下）も描く。ツールバーの 2 段目のコーナーのチップか ‹ ›、← / → で、拡大するコーナーを選ぶ
- 周の一覧：タイムは周の最後の格子点（s が最大）での t（`t_end`）。`complete` でない周（計測線の前後だけの切れ端など）は「断片」と出し、Δt も出さない（N/A）。「基準との差」は Δt が出ている最後の s での値で、両端の格子が欠けた周では `t_end` の差とずれることがある。無効な周（`lap_valid` が false）は灰色の文字で出す。一覧は右の列の末尾に小さめの文字で置く（コースの端の出典もその下）
- `web/public/data/` は git に入れない（`.gitignore`）

## collector の実行（Windows）

```bat
collector.exe -out D:\Sync\lapdiff-inbox
```

- `-out` は Syncthing の送信フォルダを指定する。既定値 `%USERPROFILE%\Sync\lapdiff-inbox` は仮のもので、フォルダが無ければ collector は起動時に止まる。Syncthing では `*.part` を無視させる
- AC より先に起動してよい。AC が共有メモリを作るまで待つ
- Ctrl-C かコンソールを閉じると、書きかけのファイルを確定してから終わる
- ファイル形式は [collector/FORMAT.md](collector/FORMAT.md) を参照
