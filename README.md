# GuardAId

GuardAId is a local school-safety research prototype combining weapon detection,
video violence classification, VLM-assisted review, evidence handling and a Flutter
mobile response application. This repository contains application source, not a
dataset, user-account backup, experimental report or model distribution.

## Source layout

- `guardaid_monitor/`: FastAPI backend, local Web Dashboard, inference adapters,
  persistence and database migrations.
- `mobileapp/`: Flutter application and Android/iOS project source.
- `runtime_dependencies/yolov9/`: required upstream YOLOv9 runtime source; its
  upstream licence remains in that directory.
- `deployment/weapon_thresholds.json`: class-threshold configuration only.

## Local setup

Use Python 3.11 or newer, FFmpeg, Flutter matching `mobileapp/pubspec.yaml`, and
local Ollama with `openbmb/minicpm-v4.7:1b-q4_K_M` for VLM inference. Install an
appropriate PyTorch/TorchVision build first (see `requirements-cuda.txt`), then
install `guardaid_monitor/requirements.txt` in an isolated environment.

Model weights are deliberately absent. Supply trusted compatible MC3-18 and
YOLOv9-T checkpoints locally using `GUARDAID_VIOLENCE_CHECKPOINT` and
`GUARDAID_WEAPON_CHECKPOINT`. Without overrides, the portable defaults look under
the ignored `external_models/` directory. YOLO runtime and thresholds default to
`runtime_dependencies/yolov9/` and `deployment/weapon_thresholds.json`.

From `guardaid_monitor/`, start local operator mode with:

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 8020
```

The default disabled-auth / console-notification configuration is for loopback
prototype use. It is not an Internet deployment configuration. For Mobile API
authentication and real FCM, configure Firebase environment variables from the
names in `.env.example`, set authentication/notification modes to `firebase`, and
supply private credentials outside Git. The template is not loaded automatically.
Configure the Flutter app with your own local Firebase build settings and backend
address; inspect `mobileapp/lib/src/firebase_api_repository.dart` for supported
build-time variables. Never add those private settings to the repository.

The optional offline demo uses explicitly synthetic identities (`example.test`).
It has no bundled password: set `GUARDAID_DEMO_PASSWORD` as a local Flutter
build-time definition if that offline demonstration mode is needed. This is
independent of Firebase authentication and does not provision any live account.

## Development workflow

Integration and functional changes are reviewed on feature branches before
merging to `main`. The system-integration branch establishes the source layout;
the Borderline Review alignment branch records the subsequent input-contract
change. Existing historical backup branches and commits remain available.
Tests and review notes describe the exercised scope; passing synthetic tests is
not a measurement of model accuracy or a certification of production deployment.

## Files intentionally excluded

Do not upload user/account records, passwords, Firebase configuration or service
accounts, device tokens, operational databases, logs, media evidence, original
videos, datasets, model weights, generated application packages or the report.
Only generic application branding/icons and generated notification sounds belong
among the source assets. Supply private dependencies separately on a trusted
machine; this repository is not a complete operational-data backup.
