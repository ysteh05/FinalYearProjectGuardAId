from pathlib import Path

from fastapi.testclient import TestClient

from app.config import RuntimeConfig
from app.main import create_app
from app.media import DemoMediaStore
from app.repository import DemoRepository


def runtime(tmp_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        persistence_mode="demo",
        data_dir=tmp_path,
        database_path=tmp_path / "guardaid.db",
        max_upload_mb=5,
        auth_mode="disabled",
        notification_mode="console",
        firebase_project_id=None,
        firebase_credentials=None,
        violence_checkpoint=tmp_path / "mc3.pt",
        weapon_checkpoint=None,
        hf_cache=tmp_path / "hf",
    )


def test_camera_crud_settings_version_and_plan(tmp_path: Path) -> None:
    config = runtime(tmp_path)
    repository = DemoRepository()
    client = TestClient(create_app(config, repository, DemoMediaStore(config)))

    response = client.post(
        "/api/cameras",
        data={"name": "Corridor A", "location": "Level 2", "durationSeconds": "12"},
        files={"video": ("sample.mp4", b"not-a-real-video-but-valid-upload-test", "video/mp4")},
    )
    assert response.status_code == 201
    camera = response.json()
    assert camera["name"] == "Corridor A"
    assert camera["durationSeconds"] == 12

    listed = client.get("/api/cameras").json()
    assert len(listed) == 1
    assert listed[0]["mediaUrl"].endswith("/media")

    patched = client.patch(
        f"/api/cameras/{camera['id']}",
        json={"name": "Corridor West", "enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    settings = client.get("/api/settings").json()
    assert settings["alertNotificationEscalation"] == {
        "sameAudienceRepeatAfterSeconds": 60,
        "allUsersEscalationAfterSeconds": 120,
    }
    settings["defaultSensitivity"] = "certain"
    settings["alertNotificationEscalation"]["sameAudienceRepeatAfterSeconds"] = 180
    settings["alertNotificationEscalation"]["allUsersEscalationAfterSeconds"] = 360
    saved = client.put("/api/settings", json=settings)
    assert saved.status_code == 200
    assert saved.json()["configVersion"] == 2
    assert saved.json()["alertNotificationEscalation"] == {
        "sameAudienceRepeatAfterSeconds": 180,
        "allUsersEscalationAfterSeconds": 360,
    }

    invalid_settings = saved.json()
    invalid_settings["alertNotificationEscalation"]["allUsersEscalationAfterSeconds"] = 180
    assert client.put("/api/settings", json=invalid_settings).status_code == 422

    plan = client.post(f"/api/simulations/{camera['id']}/plan")
    assert plan.status_code == 200
    assert plan.json()["violenceWindows"] == [
        [round(index * 2.0, 1), round(index * 2.0 + 4, 1)]
        for index in range(5)
    ]
    plan_value = plan.json()
    assert plan_value["violenceRecordingThreshold"] == 0.9208608865737915
    assert plan_value["violenceNotificationThresholds"] == {
        "sensitive": 0.9208608865737915,
        "moderate": 0.9954879879951477,
        "certain": 0.9997246861457825,
    }
    assert plan_value["borderlineReviewInterval"] == [
        0.9208608865737915,
        0.9997246861457825,
    ]
    assert "does not invent alerts" in plan.json()["note"]

    # Compatibility sensitivity remains stored but cannot move the recording floor.
    changed = client.get("/api/settings").json()
    changed["defaultSensitivity"] = "moderate"
    assert client.put("/api/settings", json=changed).status_code == 200
    same_floor = client.post(f"/api/simulations/{camera['id']}/plan").json()
    assert same_floor["violenceRecordingThreshold"] == plan_value["violenceRecordingThreshold"]

    assert client.delete(f"/api/cameras/{camera['id']}").status_code == 204
    assert client.get("/api/cameras").json() == []


def test_rejects_unsupported_media(tmp_path: Path) -> None:
    config = runtime(tmp_path)
    client = TestClient(create_app(config, DemoRepository(), DemoMediaStore(config)))
    response = client.post(
        "/api/cameras",
        data={"name": "Unsafe upload"},
        files={"video": ("payload.exe", b"x", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "Unsupported video type" in response.json()["detail"]


def test_weapon_threshold_api_rounds_and_persists_to_four_decimals(
    tmp_path: Path,
) -> None:
    config = runtime(tmp_path)
    client = TestClient(create_app(config, DemoRepository(), DemoMediaStore(config)))
    settings = client.get("/api/settings").json()
    settings["weapon"]["classThresholds"]["sensitive"]["knife"] = 0.12345
    settings["weapon"]["classThresholds"]["certain"]["gun"] = 0.99995

    saved = client.put("/api/settings", json=settings)

    assert saved.status_code == 200
    assert saved.json()["weapon"]["classThresholds"]["sensitive"]["knife"] == 0.1235
    assert saved.json()["weapon"]["classThresholds"]["certain"]["gun"] == 1.0
    reloaded = client.get("/api/settings").json()
    assert reloaded["weapon"]["classThresholds"]["sensitive"]["knife"] == 0.1235
