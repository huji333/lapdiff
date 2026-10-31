package main

import (
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

// 実機のリージョンサイズに近い長さ。偽物なので中身の構造は先頭のフィールドだけ
const (
	testStaticLen = 4096
	testPhysLen   = 4096
	testGfxLen    = 4096
)

type fakeSource struct {
	blocks [3][]byte
	onRead func(b Block) // Read の直後に呼ぶ。AC による書き換えの模擬
}

func newFakeSource() *fakeSource {
	s := &fakeSource{}
	s.blocks[BlockStatic] = make([]byte, testStaticLen)
	s.blocks[BlockPhysics] = make([]byte, testPhysLen)
	s.blocks[BlockGraphics] = make([]byte, testGfxLen)
	copy(s.blocks[BlockStatic], "static-marker")
	return s
}

func (s *fakeSource) Len(b Block) int { return len(s.blocks[b]) }
func (s *fakeSource) Read(b Block, dst []byte) {
	copy(dst, s.blocks[b])
	if s.onRead != nil {
		s.onRead(b)
	}
}
func (s *fakeSource) Int32(b Block, off int) int32 { return le32(s.blocks[b], off) }
func (s *fakeSource) Close() error                 { return nil }

func put32(b []byte, off int, v int32) { binary.LittleEndian.PutUint32(b[off:], uint32(v)) }

func (s *fakeSource) setPhys(pid int32) { put32(s.blocks[BlockPhysics], offPhysicsPacketID, pid) }

func (s *fakeSource) setGfx(gid, status, session, laps int32) {
	g := s.blocks[BlockGraphics]
	put32(g, offGraphicsPacketID, gid)
	put32(g, offGraphicsStatus, status)
	put32(g, offGraphicsSession, session)
	put32(g, offGraphicsCompletedLaps, laps)
}

type harness struct {
	t   *testing.T
	src *fakeSource
	rec *recorder
	dir string
	clk time.Time
	gid int32
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	h := &harness{t: t, src: newFakeSource(), dir: t.TempDir(), clk: time.Unix(1_800_000_000, 0)}
	h.rec = newRecorder(h.src, h.dir, "test", t.Logf)
	h.rec.now = func() time.Time { return h.clk }
	// 起動直後の1回目は基準を取るだけで書かない
	h.src.setGfx(0, statusPause, 0, 0)
	if err := h.rec.Poll(); err != nil {
		t.Fatal(err)
	}
	return h
}

// step は physics を pid にし、graphics を更新して 3 ms 進め、Poll する。
func (h *harness) step(pid, status, session, laps int32) {
	h.t.Helper()
	h.gid++
	h.src.setPhys(pid)
	h.src.setGfx(h.gid, status, session, laps)
	h.clk = h.clk.Add(3 * time.Millisecond)
	if err := h.rec.Poll(); err != nil {
		h.t.Fatal(err)
	}
}

func (h *harness) files(ext string) []string {
	h.t.Helper()
	m, err := filepath.Glob(filepath.Join(h.dir, "*"+ext))
	if err != nil {
		h.t.Fatal(err)
	}
	sort.Strings(m)
	return m
}

type capture struct {
	hdr     header
	static  []byte
	records [][]byte // host_time_ns を含むレコード全体
}

// decode は .acraw.gz を読む。partial なら Flush 済みの分だけ読む（gzip の末尾がない）。
func decode(t *testing.T, path string, partial bool) capture {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	zr, err := gzip.NewReader(f)
	if err != nil {
		t.Fatal(err)
	}
	data, err := io.ReadAll(zr)
	if err != nil && !(partial && err == io.ErrUnexpectedEOF) {
		t.Fatalf("read %s: %v", path, err)
	}
	hdr, err := parseHeader(data)
	if err != nil {
		t.Fatal(err)
	}
	c := capture{hdr: hdr}
	rest := data[headerSize:]
	c.static = rest[:hdr.StaticLen]
	rest = rest[hdr.StaticLen:]
	recLen := 8 + int(hdr.PhysicsLen) + int(hdr.GraphicsLen)
	if len(rest)%recLen != 0 {
		t.Fatalf("trailing %d bytes is not a multiple of record length %d", len(rest), recLen)
	}
	for len(rest) > 0 {
		c.records = append(c.records, rest[:recLen])
		rest = rest[recLen:]
	}
	return c
}

func recPid(rec []byte) int32 { return le32(rec, 8+offPhysicsPacketID) }

func TestHeaderRoundTrip(t *testing.T) {
	h := header{CollectorVersion: "v1.2.3", StaticLen: 1, PhysicsLen: 2, GraphicsLen: 3, StartUnixNs: -5}
	b, err := h.marshal()
	if err != nil {
		t.Fatal(err)
	}
	if len(b) != headerSize || !bytes.Equal(b[:8], []byte("ACRAW\x00\x00\x00")) {
		t.Fatalf("unexpected header bytes %x", b)
	}
	got, err := parseHeader(b)
	if err != nil {
		t.Fatal(err)
	}
	if got != h {
		t.Fatalf("got %+v, want %+v", got, h)
	}
	if _, err := (header{CollectorVersion: strings.Repeat("x", 17)}).marshal(); err == nil {
		t.Fatal("expected error for long version")
	}
}

func TestStaleMemoryAtStartupNotRecorded(t *testing.T) {
	src := newFakeSource()
	src.setPhys(500)
	src.setGfx(7, statusLive, 0, 3) // 前回の AC が LIVE のまま残した内容
	rec := newRecorder(src, t.TempDir(), "test", t.Logf)
	for range 3 {
		if err := rec.Poll(); err != nil {
			t.Fatal(err)
		}
	}
	if rec.w != nil {
		t.Fatal("opened a file for unchanged memory")
	}
}

func TestRecordsOnlyOnPacketChangeAndLive(t *testing.T) {
	h := newHarness(t)
	h.step(1, statusLive, 0, 0)
	h.step(1, statusLive, 0, 0) // 変化なし：書かない
	h.step(2, statusLive, 0, 0)
	h.step(3, statusPause, 0, 0) // PAUSE：書かない
	h.step(4, statusReplay, 0, 0)
	h.step(5, statusLive, 0, 0) // 再開：分割しない
	if err := h.rec.Close(); err != nil {
		t.Fatal(err)
	}
	if p := h.files(partExt); len(p) != 0 {
		t.Fatalf(".part left: %v", p)
	}
	fs := h.files(finalExt)
	if len(fs) != 1 {
		t.Fatalf("want 1 file, got %v", fs)
	}
	c := decode(t, fs[0], false)
	if c.hdr.StaticLen != testStaticLen || c.hdr.PhysicsLen != testPhysLen || c.hdr.GraphicsLen != testGfxLen {
		t.Fatalf("lengths %+v", c.hdr)
	}
	if c.hdr.CollectorVersion != "test" || c.hdr.StartUnixNs != time.Unix(1_800_000_000, 3_000_000).UnixNano() {
		t.Fatalf("header %+v", c.hdr)
	}
	if !bytes.HasPrefix(c.static, []byte("static-marker")) {
		t.Fatal("static block not written")
	}
	var pids []int32
	for _, r := range c.records {
		pids = append(pids, recPid(r))
	}
	if want := []int32{1, 2, 5}; !equal(pids, want) {
		t.Fatalf("pids %v, want %v", pids, want)
	}
	// host_time_ns は記録開始からの経過
	t0 := int64(binary.LittleEndian.Uint64(c.records[0]))
	t1 := int64(binary.LittleEndian.Uint64(c.records[1]))
	if t0 != c.hdr.StartUnixNs || t1-t0 != int64(6*time.Millisecond) {
		t.Fatalf("host times %d %d", t0, t1)
	}
}

func TestSplitConditions(t *testing.T) {
	h := newHarness(t)
	h.step(1, statusLive, 0, 0)
	h.step(2, statusLive, 0, 1)
	h.step(3, statusLive, 1, 0) // session が変わった
	h.step(4, statusLive, 1, 2)
	h.step(5, statusLive, 1, 0) // completedLaps が減った（リスタート）
	h.step(6, statusOff, 1, 0)  // AC_OFF で閉じる
	h.step(7, statusLive, 1, 0) // AC_OFF から LIVE
	if err := h.rec.Close(); err != nil {
		t.Fatal(err)
	}
	fs := h.files(finalExt)
	var got [][]int32
	for _, f := range fs {
		var pids []int32
		for _, r := range decode(t, f, false).records {
			pids = append(pids, recPid(r))
		}
		got = append(got, pids)
	}
	want := [][]int32{{1, 2}, {3, 4}, {5}, {7}}
	if len(got) != len(want) {
		t.Fatalf("files %v, want %v", got, want)
	}
	for i := range want {
		if !equal(got[i], want[i]) {
			t.Fatalf("files %v, want %v", got, want)
		}
	}
}

func TestGapCounting(t *testing.T) {
	h := newHarness(t)
	h.step(10, statusLive, 0, 0)
	h.step(11, statusLive, 0, 0)
	h.step(14, statusLive, 0, 0) // 12, 13 を取りこぼし
	h.step(15, statusPause, 0, 0)
	h.step(16, statusLive, 0, 0) // ポーズ中に見た packetId との差は飛びではない
	w := h.rec.w
	if w.records != 4 || w.gaps != 2 {
		t.Fatalf("records=%d gaps=%d", w.records, w.gaps)
	}
	if r := w.gapRatio(); r != 2.0/6.0 {
		t.Fatalf("ratio %v", r)
	}
	// 新しいファイルの最初のレコードより前の飛びは数えない
	h.step(30, statusLive, 1, 0)
	if h.rec.w.gaps != 0 {
		t.Fatalf("gaps carried into new file: %d", h.rec.w.gaps)
	}
}

func TestTornReadRetry(t *testing.T) {
	h := newHarness(t)
	h.src.setPhys(1)
	h.src.setGfx(1, statusLive, 0, 0)
	// 最初のコピーの直後に AC が physics を書き換えたことにする
	torn := true
	h.src.onRead = func(b Block) {
		if b == BlockPhysics && torn {
			torn = false
			h.src.setPhys(2)
			h.src.blocks[BlockPhysics][100] = 0xAA
		}
	}
	if err := h.rec.Poll(); err != nil {
		t.Fatal(err)
	}
	if h.rec.tornRetries != 1 {
		t.Fatalf("tornRetries %d", h.rec.tornRetries)
	}
	if err := h.rec.Close(); err != nil {
		t.Fatal(err)
	}
	c := decode(t, h.files(finalExt)[0], false)
	if len(c.records) != 1 || recPid(c.records[0]) != 2 || c.records[0][8+100] != 0xAA {
		t.Fatal("record is not the re-read snapshot")
	}
}

func TestTornReadGivesUp(t *testing.T) {
	h := newHarness(t)
	h.src.setGfx(1, statusLive, 0, 0)
	pid := int32(1)
	h.src.setPhys(pid)
	h.src.onRead = func(b Block) {
		if b == BlockPhysics {
			pid++
			h.src.setPhys(pid)
		}
	}
	if err := h.rec.Poll(); err != nil {
		t.Fatal(err)
	}
	if h.rec.w != nil || h.rec.tornRetries != maxTornRetries {
		t.Fatalf("w=%v tornRetries=%d", h.rec.w, h.rec.tornRetries)
	}
}

func TestPeriodicFlushAndStall(t *testing.T) {
	h := newHarness(t)
	h.rec.flushEvery = time.Second
	h.step(1, statusLive, 0, 0)
	h.step(2, statusLive, 0, 0)
	// Flush 前でも .part は存在し、rename されていない
	if len(h.files(partExt)) != 1 || len(h.files(finalExt)) != 0 {
		t.Fatal("expected only a .part file")
	}
	h.clk = h.clk.Add(time.Second)
	h.step(3, statusLive, 0, 0)
	c := decode(t, h.files(partExt)[0], true)
	if len(c.records) != 3 {
		t.Fatalf("flushed records %d, want 3", len(c.records))
	}

	// packetId が stallAfter の間動かなければ閉じる
	h.clk = h.clk.Add(h.rec.stallAfter)
	if err := h.rec.Poll(); err != nil {
		t.Fatal(err)
	}
	if h.rec.w != nil || len(h.files(finalExt)) != 1 || len(h.files(partExt)) != 0 {
		t.Fatal("stalled file was not finalized")
	}
}

func equal(a, b []int32) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
