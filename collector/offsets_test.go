package main

import (
	"encoding/json"
	"os"
	"testing"
)

// offsets.go の値が ingest 側の照合表（C コンパイラで生成）と一致することを確かめる。
// 同じオフセットが Go と Python の2か所にあるため、ずれたらここで落とす。
func TestOffsetsMatchIngestTable(t *testing.T) {
	raw, err := os.ReadFile("../ingest/tests/expected/ac_collector_offsets.json")
	if err != nil {
		t.Fatal(err)
	}
	var table struct {
		Versions map[string]struct {
			Offsets map[string]*struct {
				Offset int `json:"offset"`
			} `json:"offsets"`
		} `json:"versions"`
	}
	if err := json.Unmarshal(raw, &table); err != nil {
		t.Fatal(err)
	}
	v, ok := table.Versions["1.7"]
	if !ok {
		t.Fatal("ac_collector_offsets.json に smVersion 1.7 がない")
	}
	want := map[string]int{
		"physics.packetId":       offPhysicsPacketID,
		"graphics.packetId":      offGraphicsPacketID,
		"graphics.status":        offGraphicsStatus,
		"graphics.session":       offGraphicsSession,
		"graphics.completedLaps": offGraphicsCompletedLaps,
	}
	for name, got := range want {
		e := v.Offsets[name]
		if e == nil {
			t.Errorf("%s: 照合表にない", name)
			continue
		}
		if e.Offset != got {
			t.Errorf("%s: Go=%d, 照合表=%d", name, got, e.Offset)
		}
	}
}
