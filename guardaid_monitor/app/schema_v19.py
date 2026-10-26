"""Additive scene-generation recovery state and diagnostic summaries."""
from functools import lru_cache
from pathlib import Path
import sqlite3
from . import schema_v15 as base, schema_v16 as v16, schema_v17 as v17, schema_v18 as previous

VERSION = 19
MIGRATION = Path(__file__).resolve().parents[1] / 'migrations/019_scene_diagnostics.sql'

@lru_cache(maxsize=1)
def expected_fingerprint():
    with sqlite3.connect(':memory:') as connection:
        base.create_schema(connection)
        for path in (v16.MIGRATION, v17.MIGRATION, previous.MIGRATION, MIGRATION):
            connection.executescript(path.read_text(encoding='utf-8'))
        return base.fingerprint(connection)

def validate(connection):
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'schema_migrations' not in tables or connection.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] != VERSION:
        return previous.validate(connection)
    if base.fingerprint(connection) != expected_fingerprint():
        raise RuntimeError('v19 schema fingerprint mismatch')
    return VERSION

def initialize(connection):
    if validate(connection) != VERSION:
        previous.initialize(connection)
        connection.executescript(MIGRATION.read_text(encoding='utf-8'))
    validate(connection)
