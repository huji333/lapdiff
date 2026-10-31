//go:build windows

package main

import (
	"errors"
	"fmt"
	"sync/atomic"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	kernel32             = windows.NewLazySystemDLL("kernel32.dll")
	procOpenFileMappingW = kernel32.NewProc("OpenFileMappingW")
	winmm                = windows.NewLazySystemDLL("winmm.dll")
	procTimeBeginPeriod  = winmm.NewProc("timeBeginPeriod")
	procTimeEndPeriod    = winmm.NewProc("timeEndPeriod")
)

var mappingNames = [...]string{
	BlockStatic:   `Local\acpmf_static`,
	BlockPhysics:  `Local\acpmf_physics`,
	BlockGraphics: `Local\acpmf_graphics`,
}

type mapping struct {
	h    windows.Handle
	addr uintptr
	mem  []byte
}

// openMapping は AC が作った名前付き共有メモリを読み取り専用で開く。
// 長さは構造体の知識を使わず、ビューのリージョンサイズ（ページ単位に切り上がる）を使う。
func openMapping(name string) (*mapping, error) {
	p, err := windows.UTF16PtrFromString(name)
	if err != nil {
		return nil, err
	}
	r1, _, e := procOpenFileMappingW.Call(windows.FILE_MAP_READ, 0, uintptr(unsafe.Pointer(p)))
	if r1 == 0 {
		return nil, fmt.Errorf("OpenFileMapping %s: %w", name, e)
	}
	h := windows.Handle(r1)
	addr, err := windows.MapViewOfFile(h, windows.FILE_MAP_READ, 0, 0, 0)
	if err != nil {
		windows.CloseHandle(h)
		return nil, fmt.Errorf("MapViewOfFile %s: %w", name, err)
	}
	var mbi windows.MemoryBasicInformation
	if err := windows.VirtualQuery(addr, &mbi, unsafe.Sizeof(mbi)); err != nil {
		windows.UnmapViewOfFile(addr)
		windows.CloseHandle(h)
		return nil, fmt.Errorf("VirtualQuery %s: %w", name, err)
	}
	// uintptr から unsafe.Pointer への変換を vet に咎められない形で行う
	ptr := *(*unsafe.Pointer)(unsafe.Pointer(&addr))
	return &mapping{h: h, addr: addr, mem: unsafe.Slice((*byte)(ptr), mbi.RegionSize)}, nil
}

func (m *mapping) close() error {
	return errors.Join(windows.UnmapViewOfFile(m.addr), windows.CloseHandle(m.h))
}

type shmSource struct{ m [3]*mapping }

// openSource は3つの共有メモリを開く。AC が起動していなければエラーを返し、呼び出し側が待って再試行する。
func openSource() (Source, error) {
	s := &shmSource{}
	for b, name := range mappingNames {
		m, err := openMapping(name)
		if err != nil {
			s.Close()
			return nil, err
		}
		s.m[b] = m
	}
	return s, nil
}

func (s *shmSource) Len(b Block) int          { return len(s.m[b].mem) }
func (s *shmSource) Read(b Block, dst []byte) { copy(dst, s.m[b].mem) }

// Int32 はアトミックに読む。オフセットは 4 の倍数で、ビューはページ境界から始まるので整列している。
func (s *shmSource) Int32(b Block, off int) int32 {
	return atomic.LoadInt32((*int32)(unsafe.Pointer(&s.m[b].mem[off])))
}

func (s *shmSource) Close() error {
	var errs []error
	for i, m := range s.m {
		if m != nil {
			errs = append(errs, m.close())
			s.m[i] = nil
		}
	}
	return errors.Join(errs...)
}

// setupTimer はタイマ分解能を 1 ms にする。Sleep の粒度と time.Now の更新間隔の両方に効く
// （既定の 15.6 ms では約 3 ms ごとの physics を取りこぼす）。戻り値で元に戻す。
func setupTimer() (func(), error) {
	if r, _, _ := procTimeBeginPeriod.Call(1); r != 0 {
		return nil, fmt.Errorf("timeBeginPeriod(1) failed: %d", r)
	}
	return func() { procTimeEndPeriod.Call(1) }, nil
}
