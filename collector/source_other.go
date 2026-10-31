//go:build !windows

package main

// Windows 以外では共有メモリを開けない。ロジックはテストの偽 Source で確かめる。
func openSource() (Source, error) { return nil, errUnsupported }

func setupTimer() (func(), error) { return func() {}, nil }
