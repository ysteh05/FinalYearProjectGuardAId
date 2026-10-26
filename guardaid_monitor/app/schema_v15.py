"""Authoritative schema bootstrap and UTC persistence helpers."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from functools import lru_cache

VERSION = 15
SCHEMA = Path(__file__).resolve().parents[1] / 'migrations/015_targeted_refactor.sql'
STATUS = {'awaiting_confirmation':'open','confirmed_awaiting_handler':'in_review','in_progress':'in_review',
          'legacy_in_review':'in_review','resolved':'resolved','dismissed':'dismissed','legacy_escalated':'escalated'}


def utc(value=None):
    value = datetime.now(timezone.utc) if value is None else value
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None:raise ValueError('Timezone is required at persistence boundary')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


def create_schema(connection, *, commit=True):
    sql=SCHEMA.read_text(encoding='utf-8')
    connection.executescript(sql if commit else sql.rsplit('COMMIT;',1)[0])


def insert(connection, table, values):
    keys=list(values)
    connection.execute(f'INSERT INTO {table} ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})',[values[k] for k in keys])


def initialize(connection):
    tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables:
        if 'schema_migrations' not in tables:raise RuntimeError('Unknown database: migration ledger missing')
        version=connection.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]
        if version!=VERSION:raise RuntimeError(f'Database schema v{version}; run python -m app.migrate_v15 against a backup before starting v15')
        if fingerprint(connection)!=expected_fingerprint():raise RuntimeError('v15 schema fingerprint mismatch')
        return
    create_schema(connection,commit=False)
    now=utc()
    roles=[('role_admin','admin','Administrator','moderate',0),('role_reviewer','reviewer','Reviewer','moderate',0),
        ('role_principal','principal','Principal','moderate',0),('role_security_staff','security_staff','Security Staff','moderate',0),
        ('role_school_administrator','school_administrator','School Administrator','moderate',1),
        ('role_security_personnel','security_personnel','Security Personnel','sensitive',1),('role_teacher','teacher','Teacher','certain',1)]
    for key,name,display,sensitivity,system in roles:
        admin=int(name in ('admin','school_administrator'))
        insert(connection,'roles',dict(id=key,name=name,display_name=display,sensitivity_mode=sensitivity,is_system=system,
            can_manage_roles=admin,can_manage_users=admin,can_manage_borderline_settings=admin,
            borderline_default_enabled=int(name in ('school_administrator','security_personnel')),
            compat_borderline_selected=int(name in ('school_administrator','security_personnel')),
            borderline_updated_at=now,created_at=now,updated_at=now))
    insert(connection,'mobile_settings',dict(id='global',updated_at=now))
    insert(connection,'schema_migrations',dict(version=VERSION,name='targeted_refactor_v15_fresh',applied_at=now))
    connection.commit()


def fingerprint(connection):
    return tuple(tuple(row) for row in connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND type IN ('table','index','trigger') ORDER BY type,name"))


@lru_cache(maxsize=1)
def expected_fingerprint():
    with sqlite3.connect(':memory:') as connection:
        create_schema(connection)
        return fingerprint(connection)
