package main

import (
	"compress/gzip"
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// ファイル形式は FORMAT.md を参照。変えたら formatVersion を上げて FORMAT.md も直す。
const (
	formatVersion  = 1
	headerSize     = 48
	versionFieldSz = 16
	finalExt       = ".acraw.gz"
	partExt        = ".acraw.gz.part"
)

var magic = [8]byte{'A', 'C', 'R', 'A', 'W', 0, 0, 0}

type header struct {
	CollectorVersion string
	StaticLen        uint32
	PhysicsLen       uint32
	GraphicsLen      uint32
	StartUnixNs      int64
}

func (h header) marshal() ([]byte, error) {
	if len(h.CollectorVersion) > versionFieldSz {
		return nil, fmt.Errorf("collector version %q is longer than %d bytes", h.CollectorVersion, versionFieldSz)
	}
	b := make([]byte, headerSize)
	copy(b[0:8], magic[:])
	binary.LittleEndian.PutUint32(b[8:12], formatVersion)
	copy(b[12:28], h.CollectorVersion)
	binary.LittleEndian.PutUint32(b[28:32], h.StaticLen)
	binary.LittleEndian.PutUint32(b[32:36], h.PhysicsLen)
	binary.LittleEndian.PutUint32(b[36:40], h.GraphicsLen)
	binary.LittleEndian.PutUint64(b[40:48], uint64(h.StartUnixNs))
	return b, nil
}

func parseHeader(b []byte) (header, error) {
	if len(b) < headerSize {
		return header{}, fmt.Errorf("header too short: %d bytes", len(b))
	}
	if [8]byte(b[0:8]) != magic {
		return header{}, fmt.Errorf("bad magic %q", b[0:8])
	}
	if v := binary.LittleEndian.Uint32(b[8:12]); v != formatVersion {
		return header{}, fmt.Errorf("unsupported format version %d", v)
	}
	return header{
		CollectorVersion: strings.TrimRight(string(b[12:28]), "\x00"),
		StaticLen:        binary.LittleEndian.Uint32(b[28:32]),
		PhysicsLen:       binary.LittleEndian.Uint32(b[32:36]),
		GraphicsLen:      binary.LittleEndian.Uint32(b[36:40]),
		StartUnixNs:      int64(binary.LittleEndian.Uint64(b[40:48])),
	}, nil
}

// fileName は記録開始時刻（UTC、ミリ秒まで）を含むファイル名の幹。
func fileName(start time.Time) string {
	return "ac_" + start.UTC().Format("20060102T150405.000Z")
}

// fileWriter は1本の .acraw.gz を書く。書いている間は .part で、Close で rename する。
type fileWriter struct {
	f         *os.File
	gz        *gzip.Writer
	partPath  string
	finalPath string
	start     time.Time
	physLen   int
	gfxLen    int
	recBuf    []byte
	lastFlush time.Time

	records int64
	gaps    int64
}

func createFile(dir string, start time.Time, h header, static []byte) (*fileWriter, error) {
	if len(static) != int(h.StaticLen) {
		return nil, fmt.Errorf("static block is %d bytes, header says %d", len(static), h.StaticLen)
	}
	hb, err := h.marshal()
	if err != nil {
		return nil, err
	}
	stem := filepath.Join(dir, fileName(start))
	w := &fileWriter{
		partPath:  stem + partExt,
		finalPath: stem + finalExt,
		start:     start,
		physLen:   int(h.PhysicsLen),
		gfxLen:    int(h.GraphicsLen),
		recBuf:    make([]byte, 8+int(h.PhysicsLen)+int(h.GraphicsLen)),
		lastFlush: start,
	}
	// O_EXCL：同名ファイルを黙って上書きしない
	w.f, err = os.OpenFile(w.partPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
	if err != nil {
		return nil, err
	}
	// 約 330 Hz × 数 KB を書くので、CPU を食わない BestSpeed にする
	w.gz, err = gzip.NewWriterLevel(w.f, gzip.BestSpeed)
	if err != nil {
		w.f.Close()
		return nil, err
	}
	if _, err := w.gz.Write(hb); err != nil {
		w.abort()
		return nil, err
	}
	if _, err := w.gz.Write(static); err != nil {
		w.abort()
		return nil, err
	}
	return w, nil
}

// WriteRecord は host_time_ns・physics・graphics を1レコードとして書く。
func (w *fileWriter) WriteRecord(hostNs int64, phys, gfx []byte) error {
	if len(phys) != w.physLen || len(gfx) != w.gfxLen {
		return fmt.Errorf("record block sizes %d/%d, header says %d/%d", len(phys), len(gfx), w.physLen, w.gfxLen)
	}
	binary.LittleEndian.PutUint64(w.recBuf[0:8], uint64(hostNs))
	copy(w.recBuf[8:], phys)
	copy(w.recBuf[8+w.physLen:], gfx)
	if _, err := w.gz.Write(w.recBuf); err != nil {
		return err
	}
	w.records++
	return nil
}

// MaybeFlush は前回から every 以上たっていれば gzip を Flush する。
// プロセスが落ちても、失うのは最後の every 程度にとどまる。
func (w *fileWriter) MaybeFlush(now time.Time, every time.Duration) error {
	if now.Sub(w.lastFlush) < every {
		return nil
	}
	w.lastFlush = now
	return w.gz.Flush()
}

// Close は gzip を閉じ、ディスクへ同期してから .part を外す。
func (w *fileWriter) Close() error {
	if err := w.gz.Close(); err != nil {
		w.f.Close()
		return err
	}
	if err := w.f.Sync(); err != nil {
		w.f.Close()
		return err
	}
	if err := w.f.Close(); err != nil {
		return err
	}
	return os.Rename(w.partPath, w.finalPath)
}

// abort は作成途中の失敗で .part を残さず消す。
func (w *fileWriter) abort() {
	w.f.Close()
	os.Remove(w.partPath)
}

// gapRatio は取りこぼした packetId の割合（gaps / (records + gaps)）。
func (w *fileWriter) gapRatio() float64 {
	if w.records+w.gaps == 0 {
		return 0
	}
	return float64(w.gaps) / float64(w.records+w.gaps)
}
