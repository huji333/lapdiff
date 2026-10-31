// collector は Assetto Corsa（AC1）の共有メモリを生バイナリのまま .acraw.gz に記録する。
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

// version はビルド時に -ldflags "-X main.version=..." で埋める（16 バイトまで）。
var version = "dev"

var errUnsupported = errors.New("AC shared memory is only available on Windows")

// defaultOutDir は Syncthing の送信フォルダの仮の既定値。実際の送信フォルダに合わせて -out で指定する。
func defaultOutDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return filepath.Join("Sync", "lapdiff-inbox")
	}
	return filepath.Join(home, "Sync", "lapdiff-inbox")
}

func main() {
	out := flag.String("out", defaultOutDir(), `output directory (placeholder default: %USERPROFILE%\Sync\lapdiff-inbox; must exist)`)
	poll := flag.Duration("poll", time.Millisecond, "polling interval")
	flag.Parse()
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	if err := run(*out, *poll); err != nil {
		log.Fatal(err)
	}
}

func run(dir string, poll time.Duration) error {
	if len(version) > versionFieldSz {
		return fmt.Errorf("version %q is longer than %d bytes", version, versionFieldSz)
	}
	// 出力先が無いときは作らずに止める。Syncthing の設定違いを黙って見逃さないため
	if fi, err := os.Stat(dir); err != nil || !fi.IsDir() {
		return fmt.Errorf("output directory %s does not exist (create it or pass -out)", dir)
	}
	// Ctrl-C と、コンソールを閉じたとき（Windows では SIGTERM として届く）に書きかけを確定する
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	restore, err := setupTimer()
	if err != nil {
		return err
	}
	defer restore()

	log.Printf("collector %s, writing to %s", version, dir)
	src, err := waitSource(ctx)
	if err != nil || src == nil {
		return err
	}
	defer src.Close()
	log.Printf("shared memory opened: static=%d physics=%d graphics=%d bytes",
		src.Len(BlockStatic), src.Len(BlockPhysics), src.Len(BlockGraphics))

	rec := newRecorder(src, dir, version, log.Printf)
	for ctx.Err() == nil {
		if err := rec.Poll(); err != nil {
			return errors.Join(err, rec.Close())
		}
		time.Sleep(poll)
	}
	return rec.Close()
}

// waitSource は AC が共有メモリを作るまで待つ。中断されたら (nil, nil) を返す。
func waitSource(ctx context.Context) (Source, error) {
	var lastErr string
	for {
		src, err := openSource()
		if err == nil {
			return src, nil
		}
		if errors.Is(err, errUnsupported) {
			return nil, err
		}
		if err.Error() != lastErr {
			log.Printf("waiting for Assetto Corsa: %v", err)
			lastErr = err.Error()
		}
		select {
		case <-ctx.Done():
			return nil, nil
		case <-time.After(2 * time.Second):
		}
	}
}
