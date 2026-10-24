# GuardAId system source backup — 24 October 2026

This independent backup branch captures the current application source without
changing the repository's `main` branch or the active local application folders.
`SOURCE_MANIFEST.json` records the original relative paths, byte counts and SHA-256
hashes of all 268 copied source files.

## Included

- Python Web Dashboard/backend, application inference adapters and VLM contracts.
- HTML, JavaScript, CSS and the GuardAId dashboard mark.
- SQL migrations through migration 020, launch scripts and maintenance tools.
- Current Flutter mobile application source and Android/iOS project files,
  following the mobile repository's existing ignore rules.
- Python and Flutter dependency declarations/lockfile.
- The YOLOv9 model/utilities runtime actually used by the application, including
  its upstream licence, without training datasets or model binaries.
- The deployed weapon model's calibration file under
  `deployment/weapon_thresholds.json`.

## Deliberately not included

Runtime SQLite databases, accounts, notification tokens, alert evidence, source
videos, datasets, frame caches, checkpoints, generated packages, private `.env`
files, Firebase service accounts/client configuration, reports and screenshots.
Local-only mobile tests and test artefacts remain local under the existing mobile
ignore policy. This snapshot is application source recovery, not a complete
machine/data backup or a new full-system test certification.

## Restore on a suitable development machine

1. Check out this backup branch into an empty directory. Preserve the directory
   layout; do not overwrite a running installation without a separate rollback.
2. Install the compatible Python/Flutter toolchains and dependencies described by
   `guardaid_monitor/requirements-cuda.txt`, `guardaid_monitor/requirements.txt`
   and `mobileapp/pubspec.lock`. Restore the CUDA runtime before general Python
   requirements where GPU inference is needed.
3. Supply trusted MC3-18 and YOLOv9-T checkpoints separately. Set
   `GUARDAID_VIOLENCE_CHECKPOINT` and `GUARDAID_WEAPON_CHECKPOINT` to their actual
   paths; weights are intentionally absent from GitHub.
4. Set `GUARDAID_YOLOV9_ROOT` to the absolute path of
   `runtime_dependencies/yolov9`, and `GUARDAID_WEAPON_THRESHOLDS` to the absolute
   path of `deployment/weapon_thresholds.json`. Absolute paths avoid the
   application-relative default path assumptions.
5. Restore local configuration and, for a real installation, the private database
   and evidence directory from a separate secure backup. Use
   `GUARDAID_DATABASE_PATH`/`GUARDAID_DATA_DIR` to select the intended data. Do not
   point a new test installation at production data accidentally. The included
   migrations describe the schema but do not contain real account or alert data.
6. Configure Firebase and the mobile Live Mode settings locally using the
   application setup procedure. Keep service accounts, Google Services files,
   device tokens and passwords outside Git. Do not treat a fresh/demo instance
   as a restoration of real accounts or notification routing.
7. Install the configured MiniCPM model in local Ollama as identified in
   `guardaid_monitor/app/vlm_contract.py`. The model itself is not in this backup.
8. Validate configuration, backend health, authentication, media access and model
   loading in an isolated installation before resuming real operation.

The source is copied without application-code rewrites. Source hashes verify
backup integrity, not model accuracy or operational readiness on another machine.
