"""Opt-in loopback fixture for the Flutter HTTP regression; no real credentials.

Run from guardaid_monitor with: python tests/serve_mobile_fixture.py
Then run Flutter tests with --dart-define=CORRECTNESS_HTTP=true.
All generated database records are temporary, synthetic test data.
"""
from pathlib import Path
import os
import sys
import tempfile

if __name__ == '__main__':
    import uvicorn
    with tempfile.TemporaryDirectory(prefix='guardaid-public-http-') as folder:
        os.environ.update(GUARDAID_DATA_DIR=folder, GUARDAID_PERSISTENCE_MODE='demo',
                          GUARDAID_AUTH_MODE='disabled', GUARDAID_NOTIFICATION_MODE='console',
                          GUARDAID_MAINTENANCE_MODE='0')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.domain import Camera
        from app.main import create_app
        from app.media import LocalMediaStore
        from test_notification_escalation import (
            make_unanswered_alert, runtime, EscalationTokenVerifier,
        )
        repository, *_ = make_unanswered_alert(Path(folder))
        for index in range(3):
            repository.save_camera(Camera(id=f'fixture-count-{index}', name='Duplicate name',
                location='Synthetic fixture', storage_path=f'fixture-{index}.mp4',
                original_filename='synthetic.mp4', enabled=index < 2))
        cfg = runtime(Path(folder))
        app = create_app(cfg, repository, LocalMediaStore(cfg),
                         token_verifier_override=EscalationTokenVerifier())
        uvicorn.run(app, host='127.0.0.1', port=18843, log_level='warning')
