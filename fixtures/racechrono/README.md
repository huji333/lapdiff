# RaceChrono fixtures

## okayama_laps1-2.csv

2026-10-12 のセッションの先頭（アウトラップ、lap 1、lap 2、lap 3 の途中）。

## okayama_lap10_best.csv

同じセッションの最速周（lap 10、131.468 s）を切り出したもの。

- 元ファイル `session_20261012_113238_okayama_international_v3.csv`（13 周、約 40 分）。リポジトリには入れていない
- 切り出し範囲：lap 10 の開始の 3 秒前から、lap 11 の開始の 3 秒後まで。メタデータ行と 3 行のヘッダは元のまま
- 最初の周（lap 9 の終わり）は途中から始まるので、閉じた周として扱われない
- `session_id` は最初の行の時刻から作るので、元ファイルとは値が違う

基準線（`track/okayama/refline.csv`）は元ファイルの有効な 10 周から作った。
