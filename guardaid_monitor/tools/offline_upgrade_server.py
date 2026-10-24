"""Synthetic-only HTTP server for browser/Android upgrade acceptance.

Run from guardaid_monitor: python -m tools.offline_upgrade_server DATA_DIR.
Never accepts an existing data directory, Firebase credentials, or live tokens.
"""
import base64
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import sqlite3
import sys
import os
import uvicorn
from app.config import RuntimeConfig
from app.domain import Camera,EvidenceRecord
from app.firebase_services import VerifiedIdentity
from app.media import LocalMediaStore
from app.migrate_v15 import migrate
from app.services import AlertService
from app.sqlite_repository import SQLiteRepository


class SyntheticVerifier:
    def verify(self,token):
        if token!='offline-upgrade-token':raise ValueError('Synthetic token required')
        return VerifiedIdentity('synthetic-admin','admin@example.test','Fixture Admin')


def main():
    data=Path(sys.argv[1]).resolve()
    if data.exists():raise RuntimeError('Use a new synthetic test directory')
    data.mkdir(parents=True)
    source=data/'v14.db'
    with sqlite3.connect(source) as c:c.executescript((Path(__file__).resolve().parents[1]/'tests/fixtures/v14_complete.sql').read_text(encoding='utf-8'))
    target=data/'v15.db';migrate(source,target)
    runtime=RuntimeConfig('sqlite',data,target,5,'disabled','console',None,None,data/'unused-model.pt',None,data/'hf')
    repository=SQLiteRepository(runtime)
    camera=repository.save_camera(Camera(name='Offline upgrade corridor',location='Synthetic Block A',storage_path='cameras/offline/source.mp4',original_filename='offline.mp4'))
    alert=AlertService(repository).record_weapon_confirmation(camera,.99,source_seconds=1)
    image_path=data/'media/evidence/offline.png';image_path.parent.mkdir(parents=True)
    image_path.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR1sAAAAASUVORK5CYII='))
    repository.save_evidence(EvidenceRecord(alert_id=alert.id,media_type='weapon_screenshot',storage_path='evidence/offline.png',content_type='image/png'),image_path.stat().st_size)
    repository.update_description(alert.id,'ready',dict(description='Synthetic offline verification image.',visible_evidence=['Synthetic fixture'],uncertainty='low'))
    # app.main also exposes a module-level ASGI app. Isolate that bootstrap
    # from any live database or Firebase settings inherited by this process.
    os.environ.update(GUARDAID_PERSISTENCE_MODE='sqlite',GUARDAID_DATA_DIR=str(data),
        GUARDAID_DATABASE_PATH=str(target),GUARDAID_AUTH_MODE='disabled',
        GUARDAID_NOTIFICATION_MODE='console',GUARDAID_MAINTENANCE_MODE='0')
    from app.main import create_app
    app=create_app(runtime,repository,LocalMediaStore(runtime),token_verifier_override=SyntheticVerifier())
    uvicorn.run(app,host='127.0.0.1',port=8031,log_level='warning')


if __name__=='__main__':main()
