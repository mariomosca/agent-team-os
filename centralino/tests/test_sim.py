import pytest

from centralino.sim import SCENARI, SCENARI_ATTIVI, esegui

TUTTI = SCENARI + SCENARI_ATTIVI


@pytest.mark.parametrize("scenario", TUTTI, ids=[s.__name__ for s in TUTTI])
def test_scenario(scenario, tmp_path):
    esegui(scenario, base=tmp_path)
