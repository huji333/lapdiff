import polars as pl

from ingest.laps import lap_table, with_validity


def laps_of(times):
    t = [0.0]
    for x in times:
        t.append(t[-1] + x)
    starts = pl.DataFrame({
        "lap": list(range(len(t))),
        "t_start": t,
        "start_seen": [False] + [True] * (len(t) - 1),
    })
    return lap_table(starts)


def test_short_lap_does_not_invalidate_others():
    # 計測線の誤検出などで 40 s の周がある。基準線を覆っていない（usable でない）ので比べない
    # lap 0 はアウトラップ、lap 3 が 40 s、最後の lap 5 は閉じていない
    laps = laps_of([20, 130, 131, 40, 130])
    usable = pl.col("lap") != 3
    assert with_validity(laps, usable)["lap_valid"].to_list() == [
        False, True, True, False, True, False,
    ]
    # usable で除かないと、40 s の周を基準に正常な周がすべて無効になる
    assert with_validity(laps, pl.lit(True))["lap_valid"].to_list() == [
        False, False, False, True, False, False,
    ]


def test_single_closed_lap_is_valid():
    laps = laps_of([20, 130])
    assert with_validity(laps, pl.lit(True))["lap_valid"].to_list() == [False, True, False]


def test_no_usable_lap():
    laps = laps_of([20, 130])
    assert not with_validity(laps, pl.lit(False))["lap_valid"].any()
