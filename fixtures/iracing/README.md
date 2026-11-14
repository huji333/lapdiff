# iRacing fixtures

## okayama_mx5cup_lap9_best.ibt

2026-11-15 のテスト走行（岡山 Full Course、Mazda MX-5 Cup、baseline セットアップ）の最速周（lap 9、113.172 s）を切り出したもの。

- 元ファイル `mx5 mx52016_okayama full 2026-11-15 01-58-38.ibt`（約 9 分、31968 記録、60 Hz）。リポジトリには入れていない
- 周の番号は `LapCompleted`（終えた周の数）。lap 9 は `LapCompleted` が 8→9 になった記録（SessionTime 1007.25 s）から 9→10 になった記録（1120.42 s）まで
- 切り出し範囲：lap 9 の開始の 3 秒前から、lap 10 の開始の 3 秒後まで（7150 記録）。前後の周は途中から始まる・途中で終わるので、閉じた周として扱われない
- ヘッダ、変数ヘッダ、セッション情報は元のまま。ディスク用ヘッダの開始・終了時刻、周の数、記録数だけを切り出した範囲に合わせて書き換えた
- セッション情報の `UserName`、`TeamName`、`UserID`、`DriverUserID` は、同じバイト長のまま `Driver` と `0` に置き換えた（オフセットは変わらない）
- `LapLastLapTime` は周の数が変わった記録ではまだ前の周の値で、lap 9 のタイムに変わるのは 62 記録（約 1 s）あと

## okayama_mx5cup_reset.ibt

同じ日の別のセッション（元ファイル `mx5 mx52016_okayama full 2026-11-15 01-49-12.ibt`）から、ピットへのリセットの前後を切り出したもの（SessionTime 290.00〜317.98 s、1680 記録）。切り出し方と名前・ID の置き換えは上と同じ。

- 294.70 s：コース外（`PlayerTrackSurface` 0）からリセット。`LapCompleted` が 1→0→1 と 1 記録だけ 0 になり、`PlayerTrackSurface` は -1 を経てピットストール（1）になる
- 314.77 s：ピットレーン（`PlayerTrackSurface` 2）で計測線を越え、`LapCompleted` が 1→2。27 記録あとに `LapLastLapTime` が 131.801 から -1 に変わる
