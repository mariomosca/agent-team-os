import json

import pytest

from centralino.indirizzi import AgentMap, ErroreIndirizzo, parse_addr, slug_for_path


@pytest.fixture
def amap(tmp_path):
    (tmp_path / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [
            {"pattern": "/w/work-hub", "agent": "alita"},
            {"pattern": "/w/Projects", "agent": "kai"},
            {"pattern": "/w/Projects/06-Brandart/", "agent": "nico"},
        ],
        "agents": {"alita": {"workspace_root": "/w/work-hub"}, "kai": {"host": "mini"}, "nico": {}},
        "hosts": {"laptop": {"hub": True}, "mini": {"ssh": "mini"}},
        "cc_awareness": {"hub": "alita"},
    }))
    return AgentMap(tmp_path)


def test_regola_piu_lunga(amap):
    assert amap.agent_for_cwd("/w/Projects/06-Brandart/x") == "nico"
    assert amap.agent_for_cwd("/w/Projects/onda") == "kai"
    assert amap.agent_for_cwd("/w/Projects/06-Brandart") == "nico"  # radice di una regola con "/" finale


def test_confine_di_cartella(amap):
    assert amap.agent_for_cwd("/w/work-hub") == "alita"
    assert amap.agent_for_cwd("/w/work-hub/sub") == "alita"
    assert amap.agent_for_cwd("/w/work-hub-x") is None  # bus.py di oggi lo darebbe ad alita


@pytest.mark.parametrize("addr,exp", [
    ("kai", ("kai", None, None)), ("kai/crewmoor", ("kai", "crewmoor", None)),
    ("kai@mini/crewmoor", ("kai", "crewmoor", "mini")), ("kai/crewmoor@mini", ("kai", "crewmoor", "mini")),
    ("kai@mini", ("kai", None, "mini")),
])
def test_parse_addr(addr, exp):
    assert parse_addr(addr) == exp


def test_slug_come_oggi(tmp_path):
    p = tmp_path / "Agent Team_OS.v2"
    p.mkdir()
    assert slug_for_path(p) == "agent-team_os.v2"


def test_host(amap, monkeypatch):
    monkeypatch.delenv("AB_HOST", raising=False)
    assert amap.host_self() == "laptop"
    assert amap.resolve_host("kai") == "mini"
    assert amap.resolve_host("alita") is None  # hub sull'hub: locale
    assert amap.resolve_host("nico", "laptop") is None
    with pytest.raises(ErroreIndirizzo):
        amap.resolve_host("nico", "luna")


def test_identify(amap):
    i = amap.identify({"cwd": "/w/Projects/onda"})
    assert (i.agent, i.slug) == ("kai", "onda")
    i = amap.identify({"cwd": "/altrove", "agent": "alita", "slug": "x"})
    assert (i.agent, i.slug) == ("alita", "x")
    with pytest.raises(ErroreIndirizzo) as e:
        amap.identify({"cwd": "/altrove"})
    assert e.value.code == "no_identity"


def test_agent_map_malformato_tiene_l_ultima_buona(amap, tmp_path):
    assert amap.agent_exists("kai")
    import os, time
    (tmp_path / "AGENT_MAP.json").write_text("{rotto")
    os.utime(tmp_path / "AGENT_MAP.json", (time.time() + 5, time.time() + 5))
    assert amap.agent_exists("kai") and amap.errore
