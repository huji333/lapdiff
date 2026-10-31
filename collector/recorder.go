package main

import (
	"encoding/binary"
	"fmt"
	"time"
)

// maxTornRetries は1回の Poll で読み直す上限。超えたら次の Poll に回す。
const maxTornRetries = 10

// recorder は Source をポーリングし、記録の開始・分割・書き込みを決める。OS に依存しない。
type recorder struct {
	src        Source
	dir        string
	version    string
	now        func() time.Time
	logf       func(format string, args ...any)
	flushEvery time.Duration
	// stallAfter の間 physics と graphics の packetId がどちらも動かなければ、
	// AC が止まったとみなしてファイルを閉じる（AC_OFF にならずに終了した場合の備え）。
	stallAfter time.Duration

	static, phys, gfx []byte

	w       *fileWriter
	session int32 // 開いているファイルの graphics.session
	laps    int32 // 直前のレコードの graphics.completedLaps

	seen        bool
	lastPid     int32
	lastGid     int32
	lastChange  time.Time
	tornRetries int64 // 起動からの読み直しの累計
}

func newRecorder(src Source, dir, version string, logf func(string, ...any)) *recorder {
	return &recorder{
		src:        src,
		dir:        dir,
		version:    version,
		now:        time.Now,
		logf:       logf,
		flushEvery: 3 * time.Second,
		stallAfter: 30 * time.Second,
		static:     make([]byte, src.Len(BlockStatic)),
		phys:       make([]byte, src.Len(BlockPhysics)),
		gfx:        make([]byte, src.Len(BlockGraphics)),
	}
}

func le32(b []byte, off int) int32 { return int32(binary.LittleEndian.Uint32(b[off:])) }

// Poll は1回分の判定と書き込みを行う。書き込み・Flush・rename の失敗はそのまま返す。
func (r *recorder) Poll() error {
	now := r.now()
	if r.src.Int32(BlockGraphics, offGraphicsStatus) == statusOff {
		return r.closeFile("status AC_OFF")
	}
	pid := r.src.Int32(BlockPhysics, offPhysicsPacketID)
	if !r.seen {
		// 起動時に残っている内容は前回の AC のものかもしれないので書かない。packetId が動いてから書く
		r.seen = true
		r.lastPid = pid
		r.lastGid = r.src.Int32(BlockGraphics, offGraphicsPacketID)
		r.lastChange = now
		return nil
	}
	if pid == r.lastPid {
		if gid := r.src.Int32(BlockGraphics, offGraphicsPacketID); gid != r.lastGid {
			r.lastGid = gid
			r.lastChange = now
		} else if r.w != nil && now.Sub(r.lastChange) >= r.stallAfter {
			return r.closeFile(fmt.Sprintf("no packet for %s", r.stallAfter))
		}
		return r.flush(now)
	}

	if !r.snapshot() {
		return r.flush(now)
	}
	pid = le32(r.phys, offPhysicsPacketID)
	var gap int64
	if pid > r.lastPid+1 {
		gap = int64(pid) - int64(r.lastPid) - 1
	}
	r.lastPid = pid
	r.lastGid = le32(r.gfx, offGraphicsPacketID)
	r.lastChange = now

	// PAUSE・REPLAY の間は書かない。ファイルは閉じないので、ポーズからの再開では分割しない。
	if le32(r.gfx, offGraphicsStatus) != statusLive {
		return r.flush(now)
	}
	session := le32(r.gfx, offGraphicsSession)
	laps := le32(r.gfx, offGraphicsCompletedLaps)
	if r.w != nil {
		var reason string
		switch {
		case session != r.session:
			reason = fmt.Sprintf("session %d -> %d", r.session, session)
		case laps < r.laps:
			reason = fmt.Sprintf("completedLaps %d -> %d", r.laps, laps)
		}
		if reason != "" {
			if err := r.closeFile(reason); err != nil {
				return err
			}
		}
	}
	if r.w == nil {
		if err := r.openFile(now, session); err != nil {
			return err
		}
		gap = 0 // ファイルの最初のレコードより前の飛びは、このファイルの取りこぼしではない
	}
	r.laps = laps
	r.w.gaps += gap
	hostNs := r.w.start.UnixNano() + now.Sub(r.w.start).Nanoseconds()
	if err := r.w.WriteRecord(hostNs, r.phys, r.gfx); err != nil {
		return err
	}
	return r.flush(now)
}

// snapshot は physics と graphics をコピーする。コピーの前後で両方の packetId を読み、
// 違えば AC が書き換えている最中だったとみなして読み直す。
func (r *recorder) snapshot() bool {
	for range maxTornRetries {
		p1 := r.src.Int32(BlockPhysics, offPhysicsPacketID)
		g1 := r.src.Int32(BlockGraphics, offGraphicsPacketID)
		r.src.Read(BlockPhysics, r.phys)
		r.src.Read(BlockGraphics, r.gfx)
		p2 := r.src.Int32(BlockPhysics, offPhysicsPacketID)
		g2 := r.src.Int32(BlockGraphics, offGraphicsPacketID)
		if p1 == p2 && g1 == g2 {
			return true
		}
		r.tornRetries++
	}
	return false
}

func (r *recorder) openFile(now time.Time, session int32) error {
	r.src.Read(BlockStatic, r.static)
	h := header{
		CollectorVersion: r.version,
		StaticLen:        uint32(len(r.static)),
		PhysicsLen:       uint32(len(r.phys)),
		GraphicsLen:      uint32(len(r.gfx)),
		StartUnixNs:      now.UnixNano(),
	}
	w, err := createFile(r.dir, now, h, r.static)
	if err != nil {
		return err
	}
	r.w = w
	r.session = session
	r.logf("opened %s (session=%d)", w.partPath, session)
	return nil
}

// closeFile は開いているファイルを確定し、件数と飛びの割合をログに出す。
func (r *recorder) closeFile(reason string) error {
	if r.w == nil {
		return nil
	}
	w := r.w
	r.w = nil
	err := w.Close()
	r.logf("closed %s (%s): records=%d gaps=%d gap_ratio=%.4f torn_retries_total=%d",
		w.finalPath, reason, w.records, w.gaps, w.gapRatio(), r.tornRetries)
	if err != nil {
		return fmt.Errorf("finalize %s: %w", w.partPath, err)
	}
	return nil
}

func (r *recorder) flush(now time.Time) error {
	if r.w == nil {
		return nil
	}
	return r.w.MaybeFlush(now, r.flushEvery)
}

// Close は終了時に呼ぶ。書きかけのファイルを確定する。
func (r *recorder) Close() error { return r.closeFile("shutdown") }
