import pytest

from analysis import data
from analysis.tests.helpers import S, lap_rows, write


@pytest.fixture
def db(tmp_path):
    write(
        tmp_path,
        # 実車：rc1 は有効な 2 周（遅い周と速い周）と s = 0〜29 だけの断片、rc2 は有効な 1 周
        lap_rows("racechrono", "rc1", 1, True, S, S / 10),
        lap_rows("racechrono", "rc1", 2, True, S, S / 20),
        lap_rows("racechrono", "rc1", 3, False, S[:30], S[:30] / 40),
        lap_rows("racechrono", "rc2", 5, True, S, S / 15),
        # AC：実車のどの周より速い有効な周
        lap_rows("ac", "ac1", 2, True, S, S / 30),
    )
    return data.open_db(tmp_path)
