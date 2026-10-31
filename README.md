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

## collector の実行（Windows）

```bat
collector.exe -out D:\Sync\lapdiff-inbox
```

- `-out` は Syncthing の送信フォルダを指定する。既定値 `%USERPROFILE%\Sync\lapdiff-inbox` は仮のもので、フォルダが無ければ collector は起動時に止まる。Syncthing では `*.part` を無視させる
- AC より先に起動してよい。AC が共有メモリを作るまで待つ
- Ctrl-C かコンソールを閉じると、書きかけのファイルを確定してから終わる
- ファイル形式は [collector/FORMAT.md](collector/FORMAT.md) を参照
