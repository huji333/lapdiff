import numpy as np
import polars as pl
import pytest

from analysis import data
from analysis.tests.helpers import LENGTH, S


def test_open_db_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="がない"):
        data.open_db(tmp_path / "nope")
    with pytest.raises(FileNotFoundError, match="1 つもない"):
        data.open_db(tmp_path)


def test_lap_list(db):
    laps = data.lap_list(db)
    assert laps["label"].to_list() == [
        "ac ac1 #2",
        "racechrono rc1 #1",
        "racechrono rc1 #2",
        "racechrono rc1 #3",
        "racechrono rc2 #5",
    ]
    row = laps.filter(pl.col("label") == "racechrono rc1 #1").row(0, named=True)
    assert row["t_end"] == pytest.approx(10.0)
    assert row["length"] == pytest.approx(LENGTH)
    assert row["complete"]
    assert not laps.filter(pl.col("lap") == 3)["complete"][0]
    data.check_length(laps, LENGTH)
    with pytest.raises(ValueError, match="全長"):
        data.check_length(laps, LENGTH + 1)
    sessions = db.sql("SELECT * FROM sessions ORDER BY source, session_id").pl()
    assert sessions["n_laps"].to_list() == [1, 3, 1]
    assert sessions["n_valid"].to_list() == [1, 2, 1]


def test_default_ref(db):
    laps = data.lap_list(db)
    # AC の周と断片の周は、t_end が小さくても選ばない
    assert data.default_ref(laps) == "racechrono rc1 #2"
    assert data.default_ref(laps.filter(pl.col("source") == "ac")) is None


def test_default_ref_iracing_only():
    """実車の周がなければ iRacing の有効で欠けのない最速周。AC は代わりにしない。"""
    laps = pl.DataFrame({
        "label": ["ac a #1", "iracing i #1", "iracing i #2", "iracing i #3", "iracing i #4"],
        "source": ["ac", "iracing", "iracing", "iracing", "iracing"],
        "lap_valid": [True, True, True, False, True],
        "complete": [True, True, True, True, False],
        "t_end": [100.0, 113.0, 112.9, 112.0, 50.0],
    })
    assert data.default_ref(laps) == "iracing i #2"


def test_time_delta(db):
    labels = ["racechrono rc1 #1", "racechrono rc1 #2", "racechrono rc1 #3"]
    tel = data.time_delta(data.load_laps(db, labels), "racechrono rc1 #2")
    slow = tel.filter(pl.col("label") == "racechrono rc1 #1").sort("s")
    np.testing.assert_allclose(slow["dt"], S / 10 - S / 20)
    assert tel.filter(pl.col("label") == "racechrono rc1 #2")["dt"].abs().max() == 0
    assert tel.filter(pl.col("label") == "racechrono rc1 #3")["dt"].null_count() == 30
    final = data.final_delta(tel)
    assert final.rows() == [
        ("racechrono rc1 #1", 100.0, pytest.approx(5.0)),
        ("racechrono rc1 #2", 100.0, 0.0),
    ]


def test_time_delta_errors(db):
    tel = data.load_laps(db, ["racechrono rc1 #1", "racechrono rc1 #3"])
    with pytest.raises(ValueError, match="読み込んだ周にない"):
        data.time_delta(tel, "racechrono rc1 #2")
    with pytest.raises(ValueError, match="complete でない"):
        data.time_delta(tel, "racechrono rc1 #3")
    with pytest.raises(ValueError, match="Parquet にない周"):
        data.load_laps(db, ["racechrono rc1 #9"])
