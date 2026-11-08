"""Portable defaults and isolated SQLite creation, without real credentials."""
from app.config import APP_ROOT, WORKSPACE_ROOT, RuntimeConfig
from app.sqlite_repository import SQLiteRepository
from dataclasses import replace

def test_portable_external_paths(monkeypatch):
    for key in ('GUARDAID_VIOLENCE_CHECKPOINT', 'GUARDAID_WEAPON_CHECKPOINT',
                'GUARDAID_YOLOV9_ROOT', 'GUARDAID_WEAPON_THRESHOLDS', 'GUARDAID_HF_CACHE'):
        monkeypatch.delenv(key, raising=False)
    cfg = RuntimeConfig.from_env()
    assert cfg.violence_checkpoint == (WORKSPACE_ROOT / 'external_models/mc3/best_model.pt').resolve()
    assert cfg.weapon_checkpoint == (WORKSPACE_ROOT / 'external_models/weapon/inference.pt').resolve()
    assert cfg.weapon_thresholds_path == (WORKSPACE_ROOT / 'deployment/weapon_thresholds.json').resolve()
    assert cfg.yolov9_root == (WORKSPACE_ROOT / 'runtime_dependencies/yolov9').resolve()
    assert cfg.hf_cache == (APP_ROOT / 'runtime_data/hf-cache').resolve()

def test_fresh_synthetic_database_loads_calibration(tmp_path):
    cfg = replace(RuntimeConfig.from_env(), data_dir=tmp_path, database_path=tmp_path/'fresh.db',
                  persistence_mode='sqlite', auth_mode='disabled', notification_mode='console')
    repo = SQLiteRepository(cfg)
    settings = repo.get_settings()
    assert settings.weapon.class_thresholds.sensitive.knife > 0
    assert settings.weapon.class_thresholds.sensitive.gun > 0

def test_dashboard_and_health_use_an_isolated_database(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    cfg = replace(RuntimeConfig.from_env(), data_dir=tmp_path, database_path=tmp_path/'http.db',
                  persistence_mode='sqlite', auth_mode='disabled', notification_mode='console')
    with TestClient(create_app(cfg)) as client:
        assert client.get('/api/health').json()['status'] == 'ok'
        page = client.get('/')
        assert page.status_code == 200 and 'GuardAId' in page.text
