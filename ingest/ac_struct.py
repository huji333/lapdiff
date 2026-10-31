"""Assetto Corsa（AC1）共有メモリの構造体を numpy の構造化 dtype で表す。

定義の出典は Kunos の公式ドキュメント（SharedFileOut.h の各構造体）。
配置は Windows x64 の `#pragma pack(4)` に合わせる。

- `wchar_t` は 2 バイト（UTF-16LE）なので `<u2` の配列で持ち、`decode_wstr` で文字列にする。
- 列挙型（AC_STATUS など）は 4 バイトの int。
- 含まれる型は 4 バイトと 2 バイトだけなので、numpy の `align=True`（自然境界）が
  pack(4) と同じ配置になる。`wchar_t[33]`（66 バイト）の直後には 2 バイトの詰め物が入る。

期待値（sizeof / offsetof）は `ingest/tests/expected/ac_offsets.json` にあり、テストで照合する。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

BLOCKS = ("physics", "graphics", "static")

_I4 = "<i4"
_F4 = "<f4"
_WC = "<u2"


def _struct(fields: list[tuple]) -> np.dtype:
    return np.dtype(fields, align=True)


# --- smVersion "1.7"（AC 1.16 系の最終版。公式ドキュメントの範囲） ---

PHYSICS_1_7 = _struct([
    ("packetId", _I4),
    ("gas", _F4),
    ("brake", _F4),
    ("fuel", _F4),
    ("gear", _I4),
    ("rpms", _I4),
    ("steerAngle", _F4),
    ("speedKmh", _F4),
    ("velocity", _F4, (3,)),
    ("accG", _F4, (3,)),
    ("wheelSlip", _F4, (4,)),
    ("wheelLoad", _F4, (4,)),
    ("wheelsPressure", _F4, (4,)),
    ("wheelAngularSpeed", _F4, (4,)),
    ("tyreWear", _F4, (4,)),
    ("tyreDirtyLevel", _F4, (4,)),
    ("tyreCoreTemperature", _F4, (4,)),
    ("camberRAD", _F4, (4,)),
    ("suspensionTravel", _F4, (4,)),
    ("drs", _F4),
    ("tc", _F4),
    ("heading", _F4),
    ("pitch", _F4),
    ("roll", _F4),
    ("cgHeight", _F4),
    ("carDamage", _F4, (5,)),
    ("numberOfTyresOut", _I4),
    ("pitLimiterOn", _I4),
    ("abs", _F4),
    ("kersCharge", _F4),
    ("kersInput", _F4),
    ("autoShifterOn", _I4),
    ("rideHeight", _F4, (2,)),
    ("turboBoost", _F4),
    ("ballast", _F4),
    ("airDensity", _F4),
    ("airTemp", _F4),
    ("roadTemp", _F4),
    ("localAngularVel", _F4, (3,)),
    ("finalFF", _F4),
    ("performanceMeter", _F4),
    ("engineBrake", _I4),
    ("ersRecoveryLevel", _I4),
    ("ersPowerLevel", _I4),
    ("ersHeatCharging", _I4),
    ("ersIsCharging", _I4),
    ("kersCurrentKJ", _F4),
    ("drsAvailable", _I4),
    ("drsEnabled", _I4),
    ("brakeTemp", _F4, (4,)),
    ("clutch", _F4),
    ("tyreTempI", _F4, (4,)),
    ("tyreTempM", _F4, (4,)),
    ("tyreTempO", _F4, (4,)),
    ("isAIControlled", _I4),
    ("tyreContactPoint", _F4, (4, 3)),
    ("tyreContactNormal", _F4, (4, 3)),
    ("tyreContactHeading", _F4, (4, 3)),
    ("brakeBias", _F4),
    ("localVelocity", _F4, (3,)),
])

GRAPHICS_1_7 = _struct([
    ("packetId", _I4),
    ("status", _I4),  # AC_STATUS: 0 OFF, 1 REPLAY, 2 LIVE, 3 PAUSE
    ("session", _I4),  # AC_SESSION_TYPE: -1 UNKNOWN, 0 PRACTICE, ... 6 DRAG
    ("currentTime", _WC, (15,)),
    ("lastTime", _WC, (15,)),
    ("bestTime", _WC, (15,)),
    ("split", _WC, (15,)),
    ("completedLaps", _I4),
    ("position", _I4),
    ("iCurrentTime", _I4),
    ("iLastTime", _I4),
    ("iBestTime", _I4),
    ("sessionTimeLeft", _F4),
    ("distanceTraveled", _F4),
    ("isInPit", _I4),
    ("currentSectorIndex", _I4),
    ("lastSectorTime", _I4),
    ("numberOfLaps", _I4),
    ("tyreCompound", _WC, (33,)),
    ("replayTimeMultiplier", _F4),
    ("normalizedCarPosition", _F4),
    ("carCoordinates", _F4, (3,)),
    ("penaltyTime", _F4),
    ("flag", _I4),  # AC_FLAG_TYPE
    ("idealLineOn", _I4),
    ("isInPitLane", _I4),
    ("surfaceGrip", _F4),
    ("mandatoryPitDone", _I4),
    ("windSpeed", _F4),
    ("windDirection", _F4),
])

STATIC_1_7 = _struct([
    ("smVersion", _WC, (15,)),
    ("acVersion", _WC, (15,)),
    ("numberOfSessions", _I4),
    ("numCars", _I4),
    ("carModel", _WC, (33,)),
    ("track", _WC, (33,)),
    ("playerName", _WC, (33,)),
    ("playerSurname", _WC, (33,)),
    ("playerNick", _WC, (33,)),
    ("sectorCount", _I4),
    ("maxTorque", _F4),
    ("maxPower", _F4),
    ("maxRpm", _I4),
    ("maxFuel", _F4),
    ("suspensionMaxTravel", _F4, (4,)),
    ("tyreRadius", _F4, (4,)),
    ("maxTurboBoost", _F4),
    ("deprecated_1", _F4),
    ("deprecated_2", _F4),
    ("penaltiesEnabled", _I4),
    ("aidFuelRate", _F4),
    ("aidTireRate", _F4),
    ("aidMechanicalDamage", _F4),
    ("aidAllowTyreBlankets", _I4),
    ("aidStability", _F4),
    ("aidAutoClutch", _I4),
    ("aidAutoBlip", _I4),
    ("hasDRS", _I4),
    ("hasERS", _I4),
    ("hasKERS", _I4),
    ("kersMaxJ", _F4),
    ("engineBrakeSettingsCount", _I4),
    ("ersPowerControllerCount", _I4),
    ("trackSPlineLength", _F4),
    ("trackConfiguration", _WC, (33,)),
    ("ersMaxJ", _F4),
    ("isTimedRace", _I4),
    ("hasExtraLap", _I4),
    ("carSkin", _WC, (33,)),
    ("reversedGridPositions", _I4),
    ("PitWindowStart", _I4),
    ("PitWindowEnd", _I4),
])


@dataclass(frozen=True)
class AcDtypes:
    """1 つの smVersion に対応する 3 ブロックの dtype。"""

    sm_version: str
    physics: np.dtype
    graphics: np.dtype
    static: np.dtype


# smVersion -> 構造体の組。ここに無い smVersion は解釈しない。
LAYOUTS: dict[str, AcDtypes] = {
    "1.7": AcDtypes("1.7", PHYSICS_1_7, GRAPHICS_1_7, STATIC_1_7),
}

# smVersion は全バージョンで static の先頭にある wchar_t[15]。
SM_VERSION_DTYPE = np.dtype((_WC, (15,)))


class AcLayoutError(ValueError):
    """smVersion やブロック長が既知の構造体に合わない。"""


def decode_wstr(units: np.ndarray) -> str:
    """wchar_t 配列（UTF-16LE の符号単位）を最初の NUL までの文字列にする。"""
    raw = np.asarray(units, dtype=_WC).tobytes()
    text = raw.decode("utf-16-le")
    return text.split("\x00", 1)[0]


def read_sm_version(static_block: bytes) -> str:
    """static ブロックの先頭から smVersion を読む。"""
    if len(static_block) < SM_VERSION_DTYPE.itemsize:
        raise AcLayoutError(
            f"static ブロックが {len(static_block)} バイトしかなく smVersion を読めない"
        )
    units = np.frombuffer(static_block, dtype=_WC, count=15)
    return decode_wstr(units)


def pad_dtype(dtype: np.dtype, block_length: int) -> np.dtype:
    """末尾に詰め物を足し、itemsize を block_length にした dtype を返す。

    collector はページ単位に切り上げた長さ（VirtualQuery の値）でブロックを書くので、
    レコード列をこの dtype で読めば末尾のバイトは無視される。
    """
    if block_length < dtype.itemsize:
        raise AcLayoutError(f"ブロック長 {block_length} < 構造体のサイズ {dtype.itemsize}")
    fields = dtype.fields
    assert fields is not None
    names = list(dtype.names or ())
    return np.dtype({
        "names": names,
        "formats": [fields[n][0] for n in names],
        "offsets": [fields[n][1] for n in names],
        "itemsize": block_length,
    })


def select_dtypes(sm_version: str, block_lengths: Mapping[str, int]) -> AcDtypes:
    """smVersion とブロック長から dtype の組を選ぶ。

    返す dtype の itemsize は各ブロック長に揃えてある（末尾は詰め物）。
    smVersion が未知、ブロック長の欠け・余り、構造体より短いブロックはすべて失敗させる。
    """
    layout = LAYOUTS.get(sm_version)
    if layout is None:
        raise AcLayoutError(f"未知の smVersion {sm_version!r}（既知: {sorted(LAYOUTS)}）")
    if set(block_lengths) != set(BLOCKS):
        raise AcLayoutError(f"ブロック長のキーは {BLOCKS} が要る: {sorted(block_lengths)}")
    padded = {}
    for block in BLOCKS:
        base: np.dtype = getattr(layout, block)
        length = int(block_lengths[block])
        if length < base.itemsize:
            raise AcLayoutError(
                f"smVersion {sm_version}: {block} のブロック長 {length} が"
                f"構造体のサイズ {base.itemsize} より短い"
            )
        padded[block] = pad_dtype(base, length)
    return AcDtypes(sm_version, **padded)


def parse_block(block: bytes, dtype: np.dtype) -> np.void:
    """1 ブロックを 1 レコードとして読む。dtype より後ろのバイトは無視する。"""
    if len(block) < dtype.itemsize:
        raise AcLayoutError(f"ブロックが {len(block)} バイトで、{dtype.itemsize} バイトに足りない")
    return np.frombuffer(block, dtype=dtype, count=1)[0]
