# `.acraw.gz` の形式（format version 1）

collector が書くファイルの形式。ingest はこの文書に従って読む。形式を変えたら `format.go` の `formatVersion` を上げ、この文書も直す。

## 全体

ファイル全体が1本の gzip ストリームで、展開すると次の順に並ぶ。数値はすべて little-endian。

1. ヘッダ（48 バイト）
2. static ブロック（`static_len` バイト、1回だけ）
3. レコードの列。1レコードは `8 + physics_len + graphics_len` バイトで、ファイルの末尾まで続く。件数はヘッダに持たない

書き込み中のファイル名は `ac_<開始時刻>.acraw.gz.part`、閉じたあとは `ac_<開始時刻>.acraw.gz` になる。開始時刻は UTC のミリ秒までで、例は `ac_20261031T061000.123Z.acraw.gz`。

## ヘッダ

| オフセット | 型 | 名前 | 内容 |
|---|---|---|---|
| 0 | `[8]byte` | magic | `ACRAW\0\0\0` |
| 8 | `uint32` | format_version | `1` |
| 12 | `[16]byte` | collector_version | ASCII。16 バイトに満たない分は NUL で埋める |
| 28 | `uint32` | static_len | static ブロックのバイト長 |
| 32 | `uint32` | physics_len | physics ブロックのバイト長 |
| 36 | `uint32` | graphics_len | graphics ブロックのバイト長 |
| 40 | `int64` | start_unix_ns | 記録開始時刻（Unix ns、ホストの時計） |

## レコード

| オフセット | 型 | 内容 |
|---|---|---|
| 0 | `int64` | host_time_ns。コピーした時刻（Unix ns） |
| 8 | `physics_len` バイト | `Local\acpmf_physics` の内容 |
| 8 + physics_len | `graphics_len` バイト | `Local\acpmf_graphics` の内容 |

host_time_ns は `start_unix_ns` に、記録開始からの経過時間（単調時計）を足した値である。記録中に OS の時計が補正されても逆行しない。分解能は Windows のタイマ分解能（collector が `timeBeginPeriod(1)` で 1 ms に設定）で決まる。physics の更新間隔（約 3 ms）より細かい時間差は、`physics.packetId` の差から求める。

## ブロック長について

ブロック長は、collector が共有メモリのビューを `VirtualQuery` で調べたリージョンサイズで、ページ単位（4096 バイト）に切り上がっている。構造体の大きさとは一致しない。ingest は各ブロックの先頭から構造体の大きさだけを読み、残りは無視する。

各ブロックは共有メモリの内容をそのまま写したもので、collector は中身を解釈しない。構造体の定義は Kunos の `SharedFileOut.h`（`#pragma pack(4)`、`wchar_t` は 2 バイト）に従う。

## 記録される範囲

- `physics.packetId` が前回と変わったときだけ1レコードを書く。collector の起動時に共有メモリに残っていた内容は書かない。取りこぼした packetId の件数は、ファイルを閉じたときに collector のログへ出す
- `graphics.status` が LIVE（2）のレコードだけを含む
- 次のときにファイルを分ける：`graphics.status` が AC_OFF（0）になったとき、`graphics.session` が変わったとき、`graphics.completedLaps` が減ったとき、physics と graphics の packetId がどちらも 30 秒動かなかったとき。ポーズからの再開では分けない
- `.part` のまま残ったファイルは、collector が異常終了したことを表す。gzip の末尾がないので、展開は途中のエラーで止まる。直前の Flush（約 3 秒ごと）までのデータは読める。最後のレコードは途中で切れていることがある
