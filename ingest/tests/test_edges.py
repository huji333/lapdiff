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


# --- 白線と縁石 ---
# 色は okayama の写真から取った値（T5 の縁石の赤は薄い）
KERB_RED = (200, 187, 184)  # R - G = 13、R - B = 16
LINE_WHITE = (232, 226, 228)
RUNOFF = (214, 211, 205)  # 舗装した退避帯（T1・T5 の外）。明るく、色がほとんどない
DUSTY = (150, 150, 154)  # くすんだアスファルト・白線のぼけた縁


def masks(spans):
    return edges.paint_masks(profile(spans))


def test_paint_masks_classes():
    white, red, grey = edges.paint_masks(np.array([KERB_RED, LINE_WHITE, RUNOFF, DUSTY, GRASS, ASPHALT, (171, 166, 146)]))
    np.testing.assert_array_equal(red, [True, False, False, False, False, False, False])
    np.testing.assert_array_equal(white, [False, True, True, False, False, False, False])
    # 黄みの土（R - B = 25）は白にも灰色にもしない
    np.testing.assert_array_equal(grey, [False, False, False, True, False, True, False])


def test_rim_white_line_only():
    # アスファルトの端 5 m、白線 5.25〜5.5 m、外は芝。路面の端は白線の外側
    w, r, g = masks([(-5, 5, ASPHALT), (5.25, 5.5, LINE_WHITE)])
    assert edges.rim(w, r, g, OFFS, 5.0, 1) == (5.5, pytest.approx(math.nan, nan_ok=True))
    # 右へも同じ（右の端は負の位置）
    w, r, g = masks([(-5, 5, ASPHALT), (-5.5, -5.25, LINE_WHITE)])
    line, kerb = edges.rim(w, r, g, OFFS, -5.0, -1)
    assert line == -5.5 and math.isnan(kerb)


def test_rim_line_capped_on_white_runoff():
    # 白線の外に白い舗装が続いても、路面に含めるのは白の並びの始まりから LINE_MAX まで
    w, r, g = masks([(-5, 5, ASPHALT), (5.25, 9, RUNOFF)])
    line, kerb = edges.rim(w, r, g, OFFS, 5.0, 1)
    assert line == pytest.approx(5.25 + edges.LINE_MAX) and math.isnan(kerb)


def test_rim_crosses_grey_to_reach_line():
    # アスファルトの外側 1 m がくすんだ灰色でも、その先の白線まで読む
    w, r, g = masks([(-5, 5, ASPHALT), (5.25, 6.25, DUSTY), (6.5, 6.75, LINE_WHITE)])
    assert edges.rim(w, r, g, OFFS, 5.0, 1)[0] == 6.75
    # LINE_SEARCH より遠い白は白線とみなさない
    far = 5 + edges.LINE_SEARCH + 0.5
    w, r, g = masks([(-5, 5, ASPHALT), (5.25, far - 0.25, DUSTY), (far, far + 0.25, LINE_WHITE)])
    assert edges.rim(w, r, g, OFFS, 5.0, 1)[0] == 5.0


def test_rim_kerb_outside_line():
    # 白線 0.5 m、赤い縁石 1.5 m、その外は白い退避帯。縁石の外の線は最も外の赤
    w, r, g = masks([(-5, 5, ASPHALT), (5.25, 5.5, LINE_WHITE), (5.75, 7.25, KERB_RED), (7.5, 10, RUNOFF)])
    assert edges.rim(w, r, g, OFFS, 5.0, 1) == (5.5, 7.25)


def test_rim_kerb_limits():
    # 芝で切れた先の赤（看板など）は縁石にしない
    w, r, g = masks([(-5, 5, ASPHALT), (5.25, 5.5, LINE_WHITE), (7, 7.5, KERB_RED)])
    assert math.isnan(edges.rim(w, r, g, OFFS, 5.0, 1)[1])
    # 白線の外から KERB_MAX を超える赤は数えない
    w, r, g = masks([(-5, 5, ASPHALT), (5.25, 5.5, LINE_WHITE), (5.75, 9, KERB_RED)])
    assert edges.rim(w, r, g, OFFS, 5.0, 1)[1] == pytest.approx(5.5 + edges.KERB_MAX)
    # アスファルトの端が取れていなければ何も読まない
    line, kerb = edges.rim(w, r, g, OFFS, math.nan, 1)
    assert math.isnan(line) and math.isnan(kerb)


def test_kerb_widths_fills_stripes_and_drops_strays():
    n = 100
    paved = np.full(n, 5.0)
    kerb = np.full(n, np.nan)
    # 縁石 40〜55：白い縞で 1 点おきに赤が抜ける
    kerb[40:56:2] = 6.5
    # はぐれた赤 1 点
    kerb[80] = 7.0
    w = edges.kerb_widths(paved, kerb)
    assert w[80] == 0.0 and w[70] == 0.0
    np.testing.assert_allclose(w[42:54], 1.5)
    # 縁石は前後に KERB_WIN 点より広く伸びない
    assert w[40 - edges.KERB_WIN - 1] == 0.0 and w[55 + edges.KERB_WIN + 1] == 0.0


def test_kerb_widths_wraps_and_clips():
    n = 50
    paved = np.full(n, 5.0)
    kerb = np.full(n, np.nan)
    kerb[[0, 1, 48, 49]] = 6.0  # s = 0 をまたぐ縁石
    kerb[20:25] = 4.0  # 路面の端より内側の赤は幅 0
    w = edges.kerb_widths(paved, kerb)
    assert w[0] == 1.0 and w[49] == 1.0
    np.testing.assert_allclose(w[20:25], 0.0)
