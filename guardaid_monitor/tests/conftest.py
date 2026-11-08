"""Keep module-level application construction away from the operator's data."""
import os
from pathlib import Path
import tempfile


def pytest_configure(config):
    root = Path(__file__).resolve().parents[1] / "test_artifacts"
    root.mkdir(exist_ok=True)
    settings = {
        "GUARDAID_DATA_DIR": tempfile.mkdtemp(prefix="pytest-bootstrap-", dir=root),
        "GUARDAID_PERSISTENCE_MODE": "demo",
        "GUARDAID_AUTH_MODE": "disabled",
        "GUARDAID_NOTIFICATION_MODE": "console",
        "GUARDAID_MAINTENANCE_MODE": "0",
    }
    config._guardaid_previous_env = {key: os.environ.get(key) for key in settings}
    os.environ.update(settings)


def pytest_unconfigure(config):
    for key, value in getattr(config, "_guardaid_previous_env", {}).items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
