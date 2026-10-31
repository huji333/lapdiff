import numpy as np
import pytest

from ingest.derive import DERIVED_COLUMNS, G, derive


def circle(radius, v, ccw=True, duration=30.0, hz=50.0):
    t = np.arange(0, duration, 1 / hz)
    th = (1 if ccw else -1) * v * t / radius
    return t, radius * np.cos(th), radius * np.sin(th), np.full_like(t, v)


@pytest.mark.parametrize("ccw", [True, False])
def test_circle(ccw):
    r, v = 50.0, 20.0
    d = derive(*circle(r, v, ccw))
    sign = 1 if ccw else -1
    assert d.columns == DERIVED_COLUMNS
    # 平滑化で曲率は少し小さく出る（この半径・速度で約0.6%）。端は窓が片側になるので除く
    inner = slice(8, -8)
    np.testing.assert_allclose(d["kappa"].to_numpy()[inner], sign / r, rtol=1e-2)
    np.testing.assert_allclose(d["ay"].to_numpy()[inner], sign * v**2 / r / G, rtol=1e-2)
    np.testing.assert_allclose(d["ax"].to_numpy(), 0, atol=1e-9)
    assert d["s"].null_count() == d["dist_norm"].null_count() == d.height


def test_resample_grid_and_accel():
    t = np.arange(0.03, 20, 0.02)
    a = 5.0
    v = 10 + a * t
    d = derive(t, 0.5 * a * t**2 + 10 * t, np.zeros_like(t), v)
    tg = d["t"].to_numpy()
    np.testing.assert_allclose(np.diff(tg), 0.1, atol=1e-9)
    np.testing.assert_allclose(tg[0], 0.1)
    np.testing.assert_allclose(d["ax"].to_numpy(), a / G, rtol=1e-6)
    np.testing.assert_allclose(d["kappa"].to_numpy(), 0, atol=1e-9)


def test_gap_splits_segments():
    t1, x1, y1, v1 = circle(50, 20, duration=10)
    t = np.r_[t1, t1 + 100]
    d = derive(t, np.r_[x1, x1], np.r_[y1, y1], np.r_[v1, v1])
    tg = d["t"].to_numpy()
    assert not np.any((tg > 10) & (tg < 100))
    assert d["kappa"].null_count() == 0


def test_short_segment_and_low_speed_are_null():
    t = np.arange(0, 0.5, 0.05)
    d = derive(t, t, np.zeros_like(t), np.full_like(t, 1.0))
    assert d["ax"].null_count() == d["kappa"].null_count() == d.height
    t, x, y, _ = circle(50, 1.0)
    d = derive(t, x, y, np.full_like(t, 1.0))
    assert d["kappa"].null_count() == d["ay"].null_count() == d.height
    assert d["ax"].null_count() == 0


def test_rejects_bad_input():
    with pytest.raises(ValueError, match="単調"):
        derive([0, 1, 1], [0, 0, 0], [0, 0, 0], [0, 0, 0])
    with pytest.raises(ValueError, match="NaN"):
        derive([0, 1], [0, np.nan], [0, 0], [0, 0])
    with pytest.raises(ValueError, match="長さ"):
        derive([0, 1], [0], [0, 0], [0, 0])
