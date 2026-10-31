package main

// collector が読む AC1 共有メモリのフィールド。オフセットはバイト単位で、
// 記録の開始・分割の判定にだけ使う。構造体の解釈は ingest で行う（SPEC 設計原則1）。
//
// 出典は Kunos 公式の SharedFileOut.h（#pragma pack(4)、Windows の wchar_t は 2 バイト）。
//
//	公式配布: https://www.assettocorsa.net/forum/index.php?threads/shared-memory-reference.3352/
//	照合に使った転載: https://github.com/rodrigoangeloni/ACAppTutorialCompleto/blob/main/DOCUMENTACION%20AC%20APPS/SharedMemoryACS/ConsoleApplication1/SharedFileOut.h
//	v1.7 の末尾フィールドの照合: https://github.com/ac-custom-shaders-patch/acc-extension-apps/blob/master/apps/python/AccExtHelper/sim_info.py
//
// SPageFileGraphic の先頭は packetId, status, session（各 int）、続いて
// currentTime/lastTime/bestTime/split（各 wchar_t[15] = 30 バイト）、completedLaps の順。
// completedLaps = 12 + 30*4 = 132。
// AC1 の SPageFileGraphic には sessionIndex がない（ACC にだけあるフィールド）。
const (
	offPhysicsPacketID       = 0
	offGraphicsPacketID      = 0
	offGraphicsStatus        = 4
	offGraphicsSession       = 8
	offGraphicsCompletedLaps = 132
)

// graphics.status の値（AC_STATUS）。
const (
	statusOff    = 0
	statusReplay = 1
	statusLive   = 2
	statusPause  = 3
)
