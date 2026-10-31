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
