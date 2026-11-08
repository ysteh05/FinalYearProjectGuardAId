"""Incremental MiniCPM upgrade; retain the authoritative historical v15 schema."""
from functools import lru_cache
from pathlib import Path
import sqlite3

from . import schema_v15 as previous

VERSION = 16
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "016_minicpm_scene_points.sql"


@lru_cache(maxsize=1)
def expected_fingerprint():
    with sqlite3.connect(":memory:") as connection:
        previous.create_schema(connection)
        connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        return previous.fingerprint(connection)


def validate(connection):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if not tables:
        return 0
    if "schema_migrations" not in tables:
        raise RuntimeError("Unknown database: migration ledger missing")
    version = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
    if version not in {15,16}:
        raise RuntimeError(f"Database schema v{version}; upgrade older databases with app.migrate_v15 first")
    expected = previous.expected_fingerprint() if version == 15 else expected_fingerprint()
    if previous.fingerprint(connection) != expected:
        raise RuntimeError(f"v{version} schema fingerprint mismatch")
    return version


def initialize(connection):
    version = validate(connection)
    if version == 0:
        previous.initialize(connection)
        version = 15
    if version == 15:
        try:
            connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        except Exception:
            connection.rollback()
            raise
    validate(connection)
