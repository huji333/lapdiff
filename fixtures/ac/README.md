# AC fixtures

## okayama_mx5nd_lap2.acraw.gz

2026-11-14 に collector（dev ビルド）で録った生キャプチャから、ベストラップだけを切り出したもの。

- AC 1.16.4、smVersion 1.7、`ks_mazda_mx5_nd` @ `okayama` / `circuit_gp`、Practice
- 元ファイル `ac_20261114T033002.436Z.acraw.gz`（3 周 + アウトラップ、444 s、148,045 レコード、68 MB）
- 切り出し範囲：`graphics.completedLaps == 2` のレコードと、その前後 1 秒。43,292 レコード、129.9 s
- ヘッダと static ブロックは元のまま。`start_unix_ns` は元の記録開始時刻なので、最初のレコードの `host_time_ns` とは一致しない
- packetId の飛び：元ファイル全体で 66 件（gap_ratio 0.0004）、この範囲で 2 件
- ラップタイム：`iLastTime` が lap 1 = 129413 ms、lap 2 = 127888 ms で、`okayama_mx5nd_race_out.json` と一致する
- ポーズとセッションのリスタートは含まない（記録分割の条件はまだ実物で確かめていない）

## okayama_mx5nd_race_out.json

同じセッションの AC の `Documents\Assetto Corsa\out\race_out.json`。ラップとセクタータイムの答え合わせ用。
