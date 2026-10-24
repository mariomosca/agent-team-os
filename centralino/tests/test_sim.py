import pytest

from centralino.sim import SCENARI, esegui


@pytest.mark.parametrize("scenario", SCENARI, ids=[s.__name__ for s in SCENARI])
def test_scenario(scenario, tmp_path):
    esegui(scenario, base=tmp_path)
