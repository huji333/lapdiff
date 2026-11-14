import math

import numpy as np
import pytest

from ingest import edges
from ingest.racechrono import to_enu

ASPHALT = (90, 95, 105)  # 暗く、青みがある
GRASS = (90, 120, 60)  # 暗いが、青みがない
PAINT = (240, 240, 240)  # 明るい（白線・縁石）
OFFS = np.arange(-edges.REACH, edges.REACH + edges.STEP / 2, edges.STEP)


def profile(spans):
    """法線上の色の並び。`spans` は (始点 [m], 終点 [m], 色) で、後のものが上書きする。"""
    rgb = np.tile(np.array(GRASS, dtype=float), (len(OFFS), 1))
    for a, b, color in spans:
        rgb[(OFFS >= a) & (OFFS <= b)] = color
    return rgb


def test_road_mask():
    img = np.array([[ASPHALT, GRASS], [PAINT, (60, 60, 60)]], dtype=np.uint8)
    np.testing.assert_array_equal(edges.road_mask(img), [[True, False], [False, False]])


def test_band_signed():
    # 路面は基準線の右 2 m 〜 左 8 m（基準線は路面の上）
    left, right = edges.band(edges.road_mask(profile([(-2, 8, ASPHALT)])), OFFS)
    assert (left, right) == (8.0, 2.0)
    # 路面が基準線の左 1〜11 m（基準線は路面の外。右の端は負）
    left, right = edges.band(edges.road_mask(profile([(1, 11, ASPHALT)])), OFFS)
    assert (left, right) == (11.0, -1.0)


def test_band_no_road_near():
    left, right = edges.band(edges.road_mask(profile([(6, 12, ASPHALT)])), OFFS)
    assert math.isnan(left) and math.isnan(right)


def test_band_gap():
    # GAP_MAX 以下の切れ目は越え、それより広い切れ目（ピットレーンとの白線を想定）で止まる
    narrow = profile([(-5, 5, ASPHALT), (2, 2 + edges.GAP_MAX - edges.STEP, PAINT)])
    assert edges.band(edges.road_mask(narrow), OFFS) == (5.0, 5.0)
    wide = profile([(-5, 12, ASPHALT), (3, 3 + edges.GAP_MAX + edges.STEP, PAINT)])
    assert edges.band(edges.road_mask(wide), OFFS)[0] == pytest.approx(3 - edges.STEP)


def test_clean_fills_short_gaps_and_rejects_outliers():
    n = 200
    left, right = np.full(n, 4.0), np.full(n, 6.0)
    left[50:60] = np.nan  # 取れなかった所
    right[100:105] = 18.0  # 幅 22 m（外れ値）
    left[150:155] = -6.0  # 片側が SIDE_MIN 未満
    lo, ro = edges.clean(left, right)
    np.testing.assert_allclose(lo, 4.0)
    np.testing.assert_allclose(ro, 6.0)


def test_clean_fails_on_long_gap():
    n = 200
    left, right = np.full(n, 4.0), np.full(n, 6.0)
    left[50 : 50 + 2 * edges.SMOOTH + 1] = np.nan
    with pytest.raises(ValueError, match="幅が取れない"):
        edges.clean(left, right)


def test_clean_wraps_around_start():
    # 閉じた線なので、s = 0 の前後の欠けは最後の点の側からも埋まる
    n = 200
    left, right = np.full(n, 4.0), np.full(n, 6.0)
    left[:8] = np.nan
    left[-8:] = np.nan
    lo, _ = edges.clean(left, right)
    np.testing.assert_allclose(lo, 4.0)


def test_from_enu_roundtrip():
    lat0, lon0 = 34.9145, 134.2210
    x, y = np.array([-800.0, 0.0, 650.0]), np.array([900.0, 0.0, -1200.0])
    lat, lon = edges.from_enu(x, y, lat0, lon0)
    ex, ey = to_enu(lat, lon, lat0, lon0)
    np.testing.assert_allclose(ex, x, atol=1e-3)
    np.testing.assert_allclose(ey, y, atol=1e-3)


def test_tile_xy():
    # 経度 0・緯度 0 はズーム 1 でタイルの境（1, 1）
    tx, ty = edges.tile_xy(0.0, 0.0, zoom=1)
    assert (float(tx), float(ty)) == pytest.approx((1.0, 1.0))
