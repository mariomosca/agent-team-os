"""Schema v3: un database della prova in ombra si riduce a quello che serve."""

import sqlite3

from centralino.db import SCHEMA_VERSION, Store


def test_v2_in_ombra_diventa_v3(tmp_path):
    p = tmp_path / "centralino.db"
    Store(p).close()
    c = sqlite3.connect(p)
    c.executescript("""
        INSERT OR REPLACE INTO meta VALUES ('schema_version', '2');
        INSERT INTO meta VALUES ('sveglia_log_offset', '99');
        CREATE TABLE shadow_diffs (id INTEGER PRIMARY KEY, at TEXT);
        ALTER TABLE wake_decisions ADD COLUMN modo TEXT NOT NULL DEFAULT 'ombra';
        INSERT INTO wake_decisions (at, giro, agent, slug, msg_id, regola, decisione, modo)
          VALUES ('2026-10-24T10:00:00Z', 1, 'kai', 's', 'm-ombra', 'delega', 'sveglia', 'ombra'),
                 ('2026-10-24T10:00:00Z', 2, 'kai', 's', 'm-attiva', 'delega', 'sveglia', 'attiva');
    """)
    c.commit()
    c.close()
    s = Store(p)
    r = s.read()
    assert r.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
    assert not r.execute("SELECT 1 FROM sqlite_master WHERE name='shadow_diffs'").fetchone()
    assert [x["msg_id"] for x in r.execute("SELECT msg_id FROM wake_decisions")] == ["m-attiva"]
    assert "modo" not in {x["name"] for x in r.execute("PRAGMA table_info(wake_decisions)")}
    assert not r.execute("SELECT 1 FROM meta WHERE key='sveglia_log_offset'").fetchone()
    s.close()
    Store(p).close()  # una seconda apertura non rifà niente e non fallisce
