package main

// Block は共有メモリの3つのブロック。
type Block int

const (
	BlockStatic Block = iota
	BlockPhysics
	BlockGraphics
)

// Source は共有メモリの読み取り口。Windows では実際のマッピング、テストでは偽物を使う。
// 共有メモリにはロックがないので、Read の最中にも内容は書き換わりうる。
type Source interface {
	// Len はブロックのバイト長。構造体の知識を使わずに決めた値（Windows ではリージョンサイズ）。
	Len(b Block) int
	// Read はブロック全体を dst（長さ Len(b)）にコピーする。
	Read(b Block, dst []byte)
	// Int32 はブロックの off バイト目から little-endian の int32 を1つ読む。
	Int32(b Block, off int) int32
	Close() error
}
