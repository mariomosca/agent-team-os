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


def test_agent_map_sul_bus_vero_legge_la_cache_di_roster(tmp_path, monkeypatch):
    """RS07-F4.10: sul bus vero vince la cache di Roster, poi il file a mano; AB_AGENT_MAP sopra tutto."""
    home = tmp_path / "casa"
    bus = home / ".agent-team-os"
    bus.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AB_AGENT_MAP", raising=False)
    monkeypatch.delenv("ROSTER_HOME", raising=False)

    def mappa(agente):
        return json.dumps({"rules": [{"pattern": "/x", "match": "prefix", "agent": agente}],
                           "agents": {agente: {}}})

    # niente cache, niente file: nessun agente, nessun crash
    assert AgentMap(bus).agent_for_cwd("/x/y") is None
    # file a mano (chi non usa Roster)
    (bus / "AGENT_MAP.json").write_text(mappa("vecchio"))
    assert AgentMap(bus).agent_for_cwd("/x/y") == "vecchio"
    # la cache di Roster vince sul file
    cache = home / ".roster" / "cache"
    cache.mkdir(parents=True)
    (cache / "agent-map.json").write_text(mappa("roster"))
    assert AgentMap(bus).agent_for_cwd("/x/y") == "roster"
    # ROSTER_HOME sposta la cache
    altro = tmp_path / "altro"
    (altro / "cache").mkdir(parents=True)
    (altro / "cache" / "agent-map.json").write_text(mappa("altro"))
    monkeypatch.setenv("ROSTER_HOME", str(altro))
    assert AgentMap(bus).agent_for_cwd("/x/y") == "altro"
    # cache malformata: l'ultima buona non c'è, nessun crash e l'errore è dichiarato
    (altro / "cache" / "agent-map.json").write_text("{rotto")
    m = AgentMap(bus)
    assert m.agent_for_cwd("/x/y") is None and m.errore
    # AB_AGENT_MAP sopra tutto
    f = tmp_path / "esplicita.json"
    f.write_text(mappa("esplicita"))
    monkeypatch.setenv("AB_AGENT_MAP", str(f))
    assert AgentMap(bus).agent_for_cwd("/x/y") == "esplicita"
    # un AB_HOME di prova non guarda mai la cache vera
    monkeypatch.delenv("AB_AGENT_MAP")
    prova = tmp_path / "prova"
    prova.mkdir()
    assert AgentMap(prova).agent_for_cwd("/x/y") is None
