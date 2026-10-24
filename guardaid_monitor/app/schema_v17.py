"""Keep incident decisions independent of notification scheduling revisions."""
from functools import lru_cache
from pathlib import Path
import sqlite3

from . import schema_v15 as base
from . import schema_v16 as previous

VERSION = 17
MIGRATION = Path(__file__).resolve().parents[1] / 'migrations/017_workflow_decision_revision.sql'


@lru_cache(maxsize=1)
def expected_fingerprint():
    with sqlite3.connect(':memory:') as connection:
        base.create_schema(connection)
        connection.executescript(previous.MIGRATION.read_text(encoding='utf-8'))
        connection.executescript(MIGRATION.read_text(encoding='utf-8'))
        return base.fingerprint(connection)


def validate(connection):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'schema_migrations' not in tables:
        return previous.validate(connection)
    version = connection.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]
    if version != VERSION:
        return previous.validate(connection)
    if base.fingerprint(connection) != expected_fingerprint():
        raise RuntimeError('v17 schema fingerprint mismatch')
    return version


def initialize(connection):
    version = validate(connection)
    if version in {0, 15}:
        previous.initialize(connection)
        version = 16
    if version == 16:
        try:
            connection.executescript(MIGRATION.read_text(encoding='utf-8'))
        except Exception:
            connection.rollback()
            raise
    validate(connection)
