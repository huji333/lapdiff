"""AC 共有メモリ構造体の期待オフセット表を、ローカルの C++ コンパイラで生成する。

ヘッダはライセンスが不明なのでリポジトリに置かない。SOURCES の URL から手で取得し、
バージョンごとにパスを渡す::

    curl -sSfL -o /tmp/ac17.h <SOURCES["1.7"]["header_url"]>
    uv run python ingest/tests/expected/regen_ac_offsets.py 1.7=/tmp/ac17.h

Windows（MSVC x64）と同じ配置にするため、次を前提にする。

- `#pragma pack(4)` はヘッダ自身が持つ（無ければ失敗させる）。
- `wchar_t` は 2 バイト。`-fshort-wchar` を付け、static_assert で確かめる。
- 列挙型は 4 バイトの int（ヘッダは typedef int）。これも static_assert で確かめる。

フィールド名と型はヘッダの宣言から読み、オフセットとサイズはコンパイラの
offsetof / sizeof から取る。手で数えた値は使わない。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_FULL = HERE / "ac_offsets.json"
OUT_COLLECTOR = HERE / "ac_collector_offsets.json"

STRUCTS = ("SPageFilePhysics", "SPageFileGraphic", "SPageFileStatic")

# smVersion ごとの出典。ヘッダは sha256 で固定し、取り直して中身が変わっていたら失敗させる。
SOURCES: dict[str, dict] = {
    "1.7": {
        "header_url": (
            "https://raw.githubusercontent.com/Shibodd/assetto_corsa_driver/"
            "b173eb620776c769dde2117fa5e47e99cb3fbf72/AC_SHM_Driver/shared_file_out.h"
        ),
        "header_sha256": "28bf42168586e7489cbcdb2f983c4cd9d27c69f869935a8982446631c31a7bcf",
        "retrieved": "2026-10-31",
        "note": (
            "Kunos の公式ドキュメント（AC Shared Memory Documentation）を C++ に起こした"
            "ミラー。フィールドの順序・型・配列長は公式ドキュメントの写しと ACJP LABS の"
            "リファレンスに一致する（下記 cross_checked_against）。綴りの違い 1 件は"
            "name_fixes で公式の綴りに戻す。"
        ),
        "cross_checked_against": [
            (
                "https://raw.githubusercontent.com/DhruvGoswami10/AtlasRacing-Product/"
                "adede74e81d904a3dcb8ed29594a6591747a9031/dashboard/resources/"
                "AC%20Shared%20Memory.txt (Kunos AC Shared Memory Documentation のテキスト写し)"
            ),
            "https://labs.assettocorsa.jp/documents/reference/shared_memory",
            (
                "https://raw.githubusercontent.com/ac-custom-shaders-patch/acc-extension-apps/"
                "1e87293bddf0500ec4b7e6a44b4229b8d8060a19/apps/python/AccExtHelper/sim_info.py"
            ),
            (
                "https://raw.githubusercontent.com/thesupersupersigma/rustyAC/"
                "ad37213e5b56173cdc20cfce8ebc641c6ec5e6db/docs/map/telemetry.md"
                " (acs.exe 1.16 の解析。smVersion のリテラル \"1.7\" とオフセット)"
            ),
        ],
        # ヘッダ上の名前 -> 公式ドキュメントの名前
        "name_fixes": {"SPageFileStatic": {"trackSplineLength": "trackSPlineLength"}},
    },
}

# collector が読むフィールド。AC1 の SPageFileGraphic に sessionIndex は無い（ACC のみ）。
COLLECTOR_FIELDS = (
    ("physics", "SPageFilePhysics", "packetId"),
    ("graphics", "SPageFileGraphic", "packetId"),
    ("graphics", "SPageFileGraphic", "status"),
    ("graphics", "SPageFileGraphic", "session"),
    ("graphics", "SPageFileGraphic", "sessionIndex"),
    ("graphics", "SPageFileGraphic", "completedLaps"),
    ("static", "SPageFileStatic", "smVersion"),
)
BLOCK_OF = {"SPageFilePhysics": "physics", "SPageFileGraphic": "graphics",
            "SPageFileStatic": "static"}

KIND_OF_TYPE = {
    "int": "int32",
    "AC_STATUS": "int32",
    "AC_SESSION_TYPE": "int32",
    "AC_FLAG_TYPE": "int32",
    "float": "float32",
    "wchar_t": "utf16",
}
ELEM_SIZE = {"int32": 4, "float32": 4, "utf16": 2}

FIELD_RE = re.compile(
    r"^\s*(?P<type>[A-Za-z_]\w*)\s+(?P<name>[A-Za-z_]\w*)(?P<dims>(?:\s*\[\s*\d+\s*\])*)"
    r"\s*(?:=[^;]*)?;\s*$"
)


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", src)


def parse_struct(src: str, name: str) -> list[dict]:
    """構造体本体の宣言を 1 行ずつ読む。読めない行があれば失敗させる。"""
    m = re.search(r"struct\s+" + name + r"\s*\{(.*?)\};", src, re.DOTALL)
    if m is None:
        raise SystemExit(f"struct {name} が見つからない")
    fields = []
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        fm = FIELD_RE.match(line)
        if fm is None:
            raise SystemExit(f"{name}: 解釈できない行: {line!r}")
        ctype = fm.group("type")
        if ctype not in KIND_OF_TYPE:
            raise SystemExit(f"{name}.{fm.group('name')}: 未知の型 {ctype}")
        dims = [int(d) for d in re.findall(r"\d+", fm.group("dims"))]
        fields.append({"cname": fm.group("name"), "ctype": ctype, "shape": dims})
    return fields


def build_probe(header: Path, src: str, parsed: dict[str, list[dict]]) -> str:
    ns = re.search(r"namespace\s+(\w+)\s*\{", src)
    lines = [
        "#include <cstddef>",
        "#include <cstdio>",
        f'#include "{header}"',
    ]
    if ns:
        lines.append(f"using namespace {ns.group(1)};")
    lines += [
        'static_assert(sizeof(wchar_t) == 2, "wchar_t must be 2 bytes (-fshort-wchar)");',
        'static_assert(sizeof(int) == 4, "int must be 4 bytes");',
        'static_assert(sizeof(float) == 4, "float must be 4 bytes");',
    ]
    for t in ("AC_STATUS", "AC_SESSION_TYPE", "AC_FLAG_TYPE"):
        if re.search(r"\b" + t + r"\b", src):
            lines.append(f'static_assert(sizeof({t}) == 4, "{t} must be 4 bytes");')
    lines.append("int main() {")
    for s, fields in parsed.items():
        lines.append(f'  std::printf("S {s} %zu\\n", sizeof({s}));')
        for f in fields:
            c = f["cname"]
            lines.append(
                f'  std::printf("F {s} {c} %zu %zu\\n", offsetof({s}, {c}), sizeof({s}::{c}));'
            )
    lines += ["  return 0;", "}"]
    return "\n".join(lines) + "\n"


def compile_and_run(probe: str, cxx: str) -> tuple[str, str]:
    flags = ["-std=c++17", "-fshort-wchar", "-Wall", "-Werror"]
    with tempfile.TemporaryDirectory() as td:
        cpp = Path(td) / "probe.cpp"
        exe = Path(td) / "probe"
        cpp.write_text(probe)
        subprocess.run([cxx, *flags, str(cpp), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
    ver = subprocess.run([cxx, "--version"], check=True, capture_output=True, text=True)
    return out, f"{ver.stdout.splitlines()[0]} | flags: {' '.join(flags)}"


def generate(version: str, header: Path, cxx: str) -> tuple[dict, str]:
    meta = SOURCES[version]
    raw = header.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != meta["header_sha256"]:
        raise SystemExit(f"{header}: sha256 {digest} が SOURCES[{version!r}] と一致しない")
    src = strip_comments(raw.decode("utf-8"))
    if not re.search(r"#pragma\s+pack\s*\(\s*(push\s*,\s*)?4\s*\)", src):
        raise SystemExit(f"{header}: #pragma pack(4) が無い")
    parsed = {s: parse_struct(src, s) for s in STRUCTS}
    out, compiler = compile_and_run(build_probe(header.resolve(), src, parsed), cxx)

    sizes: dict[str, int] = {}
    measured: dict[tuple[str, str], tuple[int, int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if parts[0] == "S":
            sizes[parts[1]] = int(parts[2])
        else:
            measured[(parts[1], parts[2])] = (int(parts[3]), int(parts[4]))

    fixes = meta.get("name_fixes", {})
    structs = {}
    for s, fields in parsed.items():
        rows = []
        for f in fields:
            kind = KIND_OF_TYPE[f["ctype"]]
            offset, size = measured[(s, f["cname"])]
            expected = ELEM_SIZE[kind] * math.prod(f["shape"])
            if size != expected:
                raise SystemExit(f"{s}.{f['cname']}: sizeof={size}, 型から {expected}")
            rows.append({
                "name": fixes.get(s, {}).get(f["cname"], f["cname"]),
                "offset": offset,
                "kind": kind,
                "shape": f["shape"],
                "size": size,
            })
        structs[s] = {"size": sizes[s], "fields": rows}
    source = {k: v for k, v in meta.items()}
    return {"source": source, "structs": structs}, compiler


def collector_table(versions: dict) -> dict:
    table = {}
    for ver, body in versions.items():
        entry = {"sizes": {BLOCK_OF[s]: body["structs"][s]["size"] for s in STRUCTS}}
        offsets = {}
        for block, s, name in COLLECTOR_FIELDS:
            hit = [f for f in body["structs"][s]["fields"] if f["name"] == name]
            offsets[f"{block}.{name}"] = (
                {"offset": hit[0]["offset"], "kind": hit[0]["kind"], "size": hit[0]["size"]}
                if hit else None
            )
        entry["offsets"] = offsets
        table[ver] = entry
    return table


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("headers", nargs="+", metavar="VERSION=PATH")
    ap.add_argument("--cxx", default=os.environ.get("CXX", "c++"))
    args = ap.parse_args()

    pairs = dict(h.split("=", 1) for h in args.headers)
    if set(pairs) != set(SOURCES):
        raise SystemExit(f"全バージョンのヘッダが要る: {sorted(SOURCES)}（渡されたのは {sorted(pairs)}）")

    versions, compilers = {}, set()
    for ver in sorted(pairs):
        versions[ver], comp = generate(ver, Path(pairs[ver]), args.cxx)
        compilers.add(comp)

    generated_by = {
        "script": "ingest/tests/expected/regen_ac_offsets.py",
        "compiler": sorted(compilers),
        "target_equivalent": "MSVC x64: #pragma pack(4), 2-byte wchar_t (UTF-16LE), 4-byte enums",
    }
    OUT_FULL.write_text(
        json.dumps({"generated_by": generated_by, "versions": versions},
                   ensure_ascii=False, indent=1) + "\n"
    )
    OUT_COLLECTOR.write_text(
        json.dumps({
            "generated_by": generated_by,
            "note": "null はその smVersion の構造体に無いフィールド。"
                    "sessionIndex は ACC にしか無く、AC1 の SPageFileGraphic には無い。",
            "versions": collector_table(versions),
        }, ensure_ascii=False, indent=1) + "\n"
    )
    print(f"wrote {OUT_FULL} and {OUT_COLLECTOR}", file=sys.stderr)


if __name__ == "__main__":
    main()
