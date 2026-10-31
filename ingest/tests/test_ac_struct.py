"""AC 構造体の dtype を、C++ コンパイラで生成した期待オフセット表と照合する。

期待値は ingest/tests/expected/ac_offsets.json（regen_ac_offsets.py で生成）から読み、
dtype 自身からは導かない。
"""

import json
from pathlib import Path

import numpy as np
import pytest

from ingest import ac_struct

DATA = Path(__file__).parent / "expected"
TABLE = json.loads((DATA / "ac_offsets.json").read_text())["versions"]
COLLECTOR = json.loads((DATA / "ac_collector_offsets.json").read_text())["versions"]

BLOCK_STRUCT = {
    "physics": "SPageFilePhysics",
    "graphics": "SPageFileGraphic",
    "static": "SPageFileStatic",
}
KIND = {"int32": np.dtype("<i4"), "float32": np.dtype("<f4"), "utf16": np.dtype("<u2")}
PAGE = 4096


def test_versions_match_table():
    assert set(ac_struct.LAYOUTS) == set(TABLE)


CASES = [(v, b) for v in sorted(TABLE) for b in BLOCK_STRUCT]


@pytest.mark.parametrize(("version", "block"), CASES)
def test_dtype_matches_compiled_layout(version, block):
    expected = TABLE[version]["structs"][BLOCK_STRUCT[block]]
    dt = getattr(ac_struct.LAYOUTS[version], block)

    assert dt.itemsize == expected["size"]
    assert list(dt.names) == [f["name"] for f in expected["fields"]]
    for f in expected["fields"]:
        sub, offset = dt.fields[f["name"]][:2]
        assert offset == f["offset"], f["name"]
        assert sub.itemsize == f["size"], f["name"]
        base = sub.base if sub.subdtype else sub
        assert base == KIND[f["kind"]], f["name"]
        assert list(sub.shape) == f["shape"], f["name"]


def test_collector_table_is_projection_of_full_table():
    for version, entry in COLLECTOR.items():
        structs = TABLE[version]["structs"]
        for block, s in BLOCK_STRUCT.items():
            assert entry["sizes"][block] == structs[s]["size"]
        for key, want in entry["offsets"].items():
            block, name = key.split(".")
            hit = [f for f in structs[BLOCK_STRUCT[block]]["fields"] if f["name"] == name]
            if want is None:
                assert hit == []
            else:
                assert (hit[0]["offset"], hit[0]["kind"], hit[0]["size"]) == (
                    want["offset"], want["kind"], want["size"]
                )


def _offset(version, block, name):
    fields = TABLE[version]["structs"][BLOCK_STRUCT[block]]["fields"]
    return next(f["offset"] for f in fields if f["name"] == name)


def _put_wstr(buf: bytearray, offset: int, text: str) -> None:
    raw = text.encode("utf-16-le")
    buf[offset : offset + len(raw)] = raw


def test_select_and_parse_page_rounded_blocks():
    """ページ単位に切り上げたブロック（4096 バイト）を合成して読む。"""
    v = "1.7"
    static = bytearray(PAGE)
    _put_wstr(static, _offset(v, "static", "smVersion"), "1.7")
    _put_wstr(static, _offset(v, "static", "track"), "okayama")
    static[-4:] = b"\xff\xff\xff\xff"  # 末尾の余りは読まれない

    graphics = bytearray(PAGE)
    for name, value in (("packetId", 41), ("status", 2), ("completedLaps", 7)):
        o = _offset(v, "graphics", name)
        graphics[o : o + 4] = np.int32(value).tobytes()

    physics = bytearray(PAGE)
    o = _offset(v, "physics", "speedKmh")
    physics[o : o + 4] = np.float32(123.5).tobytes()

    sm = ac_struct.read_sm_version(bytes(static))
    assert sm == "1.7"
    dts = ac_struct.select_dtypes(sm, {"physics": PAGE, "graphics": PAGE, "static": PAGE})
    assert dts.graphics.itemsize == PAGE

    s = ac_struct.parse_block(bytes(static), dts.static)
    g = ac_struct.parse_block(bytes(graphics), dts.graphics)
    p = ac_struct.parse_block(bytes(physics), dts.physics)
    assert ac_struct.decode_wstr(s["track"]) == "okayama"
    assert (g["packetId"], g["status"], g["completedLaps"]) == (41, 2, 7)
    assert p["speedKmh"] == np.float32(123.5)

    # ブロック長 = レコードの stride として、連続したレコードも読める
    two = np.frombuffer(bytes(graphics) * 2, dtype=dts.graphics)
    assert list(two["completedLaps"]) == [7, 7]


def test_select_accepts_exact_struct_size():
    sizes = {b: TABLE["1.7"]["structs"][s]["size"] for b, s in BLOCK_STRUCT.items()}
    dts = ac_struct.select_dtypes("1.7", sizes)
    assert dts.physics.itemsize == sizes["physics"]


@pytest.mark.parametrize(
    ("sm_version", "lengths"),
    [
        ("1.9", {"physics": PAGE, "graphics": PAGE, "static": PAGE}),  # ACC
        ("", {"physics": PAGE, "graphics": PAGE, "static": PAGE}),
        ("1.7", {"physics": 100, "graphics": PAGE, "static": PAGE}),
        ("1.7", {"physics": PAGE, "graphics": PAGE}),
        ("1.7", {"physics": PAGE, "graphics": PAGE, "static": PAGE, "extra": PAGE}),
    ],
)
def test_select_rejects_mismatch(sm_version, lengths):
    with pytest.raises(ac_struct.AcLayoutError):
        ac_struct.select_dtypes(sm_version, lengths)


def test_parse_block_rejects_short_block():
    with pytest.raises(ac_struct.AcLayoutError):
        ac_struct.parse_block(b"\x00" * 10, ac_struct.PHYSICS_1_7)
