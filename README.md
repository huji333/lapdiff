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
```

## 分析

```sh
uv run python -m ingest.racechrono <csv> data/      # data/<source>/<session_id>/telemetry.parquet
LAPDIFF_DATA=data uv run marimo edit analysis/view1.py
```

- データの場所は環境変数 `LAPDIFF_DATA` で指定する。指定しなければリポジトリ直下の `data/` を読む。ノートブックの「データの場所」の欄でも変えられる
- `data/` と `*.parquet` は git に入れない（`.gitignore`）
- ビュー 1（`analysis/view1.py`）：コース図と、距離×速度・距離×タイム差（基準ラップとの累積タイム差）。既定では有効な周を全部重ね、基準ラップは実車の有効な周のうち最速の周にする。コース図で矩形を、距離のグラフで横の範囲をドラッグすると、もう一方の同じ区間が強調される

## collector の実行（Windows）

```bat
collector.exe -out D:\Sync\lapdiff-inbox
```

- `-out` は Syncthing の送信フォルダを指定する。既定値 `%USERPROFILE%\Sync\lapdiff-inbox` は仮のもので、フォルダが無ければ collector は起動時に止まる。Syncthing では `*.part` を無視させる
- AC より先に起動してよい。AC が共有メモリを作るまで待つ
- Ctrl-C かコンソールを閉じると、書きかけのファイルを確定してから終わる
- ファイル形式は [collector/FORMAT.md](collector/FORMAT.md) を参照
