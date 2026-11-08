"""Version one existing database's Borderline FPS without starting app workers."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import RuntimeConfig
from app.domain import VlmSettings, utc_now
from app.schema_v20 import validate, VERSION
from app.sqlite_repository import SQLiteRepository


def database_state(path: Path) -> dict[str, str]:
    """Hash logical table contents without logging account or incident data."""
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        result = {}
        for (name,) in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = sorted(json.dumps(tuple(row), default=str, ensure_ascii=True)
                          for row in connection.execute('SELECT * FROM ' + quoted))
            result[name] = sha256('\n'.join(rows).encode()).hexdigest()
        return result


def update_borderline_sampling(database: Path, fps: float = 4.0) -> dict:
    # Validate before opening the target for any write or creating a backup.
    VlmSettings(input_fps=fps)
    database = database.resolve(strict=True)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        if validate(connection) != VERSION:
            raise RuntimeError(f'This settings-only operation requires an existing v{VERSION} database.')
    before_tables = database_state(database)
    # Maintenance mode skips startup configuration/model migrations and seeding.
    runtime = replace(RuntimeConfig.from_env(), database_path=database, maintenance_mode=True)
    repository = SQLiteRepository(runtime)
    before = repository.get_settings()
    if before.vlm.input_fps == fps:
        return dict(changed=False, config_version=before.config_version, borderline_fps=fps)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = database.with_name(f'{database.stem}.before-borderline-{fps:g}fps-{stamp}.db')
    if backup.exists():
        raise FileExistsError(backup)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(backup) as destination:
            source.backup(destination)
    updated = before.model_copy(update={
        'vlm': before.vlm.model_copy(update={'input_fps': fps}),
        'updated_at': utc_now(),
        'updated_by': 'local-borderline-sampling-update',
    })
    saved = repository.save_settings(updated, expected_config_version=before.config_version)
    # Fresh repository/settings read also checks persistence across reopening.
    restored = SQLiteRepository(runtime).get_settings()
    expected = before.model_dump(mode='json')
    expected['vlm']['input_fps'] = fps
    actual = restored.model_dump(mode='json')
    for key in ('config_version', 'updated_at', 'updated_by'):
        expected.pop(key, None)
        actual.pop(key, None)
    if actual != expected or restored.config_version != before.config_version + 1:
        raise RuntimeError('Configuration saved, but post-save settings preservation check failed.')
    after_tables = database_state(database)
    unexpected = [name for name, digest in before_tables.items()
                  if name != 'config_versions' and after_tables.get(name) != digest]
    if unexpected:
        raise RuntimeError(f'Configuration saved; other tables changed concurrently: {unexpected}')
    return dict(changed=True, database=str(database), backup=str(backup),
                previous_config_version=before.config_version, config_version=saved.config_version,
                previous_borderline_fps=before.vlm.input_fps, borderline_fps=restored.vlm.input_fps,
                unrelated_settings_preserved=True, other_database_tables_unchanged=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--fps', type=float, default=4.0)
    args = parser.parse_args()
    print(json.dumps(update_borderline_sampling(args.database, args.fps), indent=2, allow_nan=False))
