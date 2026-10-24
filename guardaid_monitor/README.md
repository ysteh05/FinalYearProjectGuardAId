# GuardAId Monitor

Operational simulated-CCTV WebUI for the GuardAId prototype. This app is intentionally separate from `vlm_borderline_dashboard`, which remains the research/evaluation interface.

Current architecture:

```text
SQLite                     structured operational source of truth
Local server filesystem    camera sources and alert evidence
Firebase Authentication    optional login/ID-token provider
Firebase Cloud Messaging   optional mobile push provider
```

Cloud Firestore, Cloud Storage and Cloud Functions are not used.

Implemented operational pipeline:

- Monitor, Alerts, Cameras and Config views;
- camera-video upload, edit, enable/disable, playback and deletion;
- explicit SQLite/Auth/FCM runtime status;
- typed domain models plus a normalized relational migration;
- decode-once multi-feed worker with live progress, video time, ETA and per-model counters;
- MC3-18 Kinetics-400 violence inference using the four-source Epoch 8 best
  checkpoint at
  `model_runs/mc3_temporal_shift_letterbox_4dataset_4s16f2_k400/run_temporal_shift_letterbox_ui_20261015_194612_288156/best_model.pt`;
  4-second windows, 2-second stride, 16 RGB frames sampled at 4 FPS/bin centres,
  and full-frame 160x160 RGB-mean letterboxing (no center crop);
- YOLOv9-T inference using the expanded-negative fine-tuned two-class best checkpoint, class-aware NMS,
  six validation-selected F-beta thresholds (Sensitive=F2, Moderate=F1,
  Certain=F0.5) and N-of-M temporal confirmation;
- MiniCPM V4.7 1B through local Ollama, with non-thinking borderline review and structured scene descriptions;
- local violence clips / weapon screenshots, SHA-256 metadata, SQLite audit references and
  optional FCM dispatch;
- configurable two-phase unanswered-alert notifications: undecided alerts repeat to accounts
  actually notified initially, while Confirm-only broadcasts repeat to selected roles; each phase's
  second reminder goes to all active role-bearing users with enabled devices;
- authenticated mobile API with pending-user approval, DPP roles, inherited sensitivity,
  incident actions, notification read state, borderline recipient settings and audit records;
- a shared alert/evidence feed for all authenticated Active role-assigned users; role sensitivity
  controls notification delivery rather than incident visibility;
- Firebase-ready repository/media adapters;
- no fabricated detector results: runtime failures are surfaced on the camera job.

For local development, Config includes **Detection Debug Mode**. When enabled, each newly
started analysis appends MC3 window probabilities and YOLO per-class candidate confidence
and thresholds beneath its video card. Rows distinguish below-threshold results, positives
that did not create a new incident, and newly created alerts. Debug events are kept in the
browser during the run; the server only sends a small rolling batch per poll.

Alert policy is deliberately separate from the legacy `defaultSensitivity` setting. Violence
alerts are recorded at or above the lowest of the three role thresholds, while each account's
role threshold determines ordinary phone notification eligibility. Weapon alerts use per-class
minimum thresholds and a separate N-of-M confirmation for recording and for each notification
profile. Active role-assigned users can view every recorded alert in both Web and Mobile. The Web
Borderline Review switch controls review and VLM-supplemental notifications only; enabled review
processes violence windows in `[lowest threshold, highest threshold)` until the first VLM-positive result for that alert, even when no account
is currently eligible for a supplemental notification. Only a valid VLM-positive result can add
accounts to the initial notification audience.

The selected MC3-18 checkpoint, two-class YOLOv9-T checkpoint, official YOLOv9 runtime and
six validation thresholds are all present in this workspace. Models load lazily when the
first analysis starts; MiniCPM generation is limited to one concurrent request. Violence descriptions
run after the source-time incident ends: up to 8 seconds uses 2 FPS, longer clips use 1 FPS,
and only the first 60 seconds of an incident are sent to the VLM. Complete evidence is retained.
See [MiniCPM runtime](docs/MINICPM_RUNTIME.md) for the processing and upgrade details.

The active weapon checkpoint is the selected nine-source YOLOv9-T Epoch 6 best model at
`model_runs/yolov9_selected_sources_50epoch/runs/sel50_20261011_193754_83560d/best_inference.pt`,
version `yolov9-t-selected-9source-f05-ft-20261011-193754-83560d-e06`. Epoch 6 was selected by
the `0.5 * F0.5 + 0.5 * fitness` benchmark. Its class order remains ID 0 `knife`, ID 1 `gun`
(firearm types are merged into `gun`). The previous BREV hard-negative checkpoint remains available.

The six active thresholds were selected independently on this checkpoint's validation set
(Sensitive=F2, Moderate=F1, Certain=F0.5): Sensitive Knife `0.28156810998916626`, Gun
`0.12619423866271973`; Moderate Knife `0.5161489248275757`, Gun `0.3640787899494171`;
Certain Knife `0.6588038802146912`, Gun `0.6444171071052551`. The calibration report is at
`model_runs/yolov9_selected_sources_50epoch/runs/sel50_20261011_193754_83560d/sensitivity_thresholds.json`.

On application startup, SQLite migrates each untouched previous-model threshold to the
corresponding new validation default. This includes exact matches to the previously deployed
Mall-negative thresholds. Per-class values manually changed by an operator are preserved; the
migration is recorded as a new configuration version. Historical alert and model-evaluation
records are not rewritten. Previous checkpoints remain available as files, but are not the
default runtime model.

The active violence model is `mc3-18-k400-harvard-rwf-airtlab-dancetrack-letterbox160-4s16f2-20261015-e08`,
selected at Epoch 8 by validation ROC AUC. Its validation-selected thresholds are Sensitive
`0.15228421986103058` (F2), Moderate `0.15228421986103058` (F1), and Certain
`0.998852014541626` (F0.5). Startup migrates the previous Epoch 5 threshold triplet only when
all three persisted values still match its defaults; operator-customized thresholds and timing
values are retained. Inference resize, padding and normalization match the Epoch 8 training
pipeline: preserve the full frame, fit within 160x160, fill padding with the RGB normalization
mean, then normalize. Historical alert and model-evaluation records are not rewritten; previous
checkpoints remain available as files but are not the default runtime model. Runtime windows use
a 2-second stride and add the training protocol's end-aligned tail window when needed.

Weapon decision order:

```text
model candidates -> class-aware NMS -> active per-class threshold
-> weapon/no-weapon frame -> N-of-M confirmation -> alert/cooldown
```

The low `nmsCandidateFloor` is only a technical candidate prefilter. It must remain below
all deployment class thresholds and is not the weapon decision threshold.

## Run locally

```powershell
cd guardaid_monitor
.\setup.ps1
.\run_demo.ps1
```

`setup.ps1` reuses the CUDA-enabled `C:\tmp\guardaid-vlm-venv` created for the
VLM evaluation dashboard. It installs only missing GuardAId packages, checks CUDA,
and validates the installed MiniCPM model through the local Ollama service.
The serving VLM no longer needs Transformers, bitsandbytes or a second PyTorch copy.

The pinned GPU stack is PyTorch 2.11 / torchvision 0.26 / TorchCodec 0.11. To build
a fresh GPU environment, install `requirements-cuda.txt` before `requirements.txt`;
do not install `requirements.txt` into a CPU-only Python environment and use it to
run analysis.

Open `http://127.0.0.1:8020`. The default is persistent local mode: metadata is stored in `runtime_data/database/guardaid.db` and uploaded videos stay under `runtime_data/media/`. No video, image or database row is uploaded to Firebase.

AVI sources remain the analysis input. For browser playback, the first media request creates and caches a separate H.264/AAC MP4 preview; this requires `ffmpeg` to be available on `PATH`. Replacing or deleting a camera source also removes its cached preview.

After setup, the convenience launcher is:

```powershell
.\run_demo.ps1
```

See `DATABASE_DESIGN.md` for the schema and `FIREBASE_AUTH_FCM_SETUP_TUTORIAL.md` before enabling login or real push notifications. Schema v14 stores the incident response workflow, detection qualifications, per-user Borderline defaults, durable review tasks, actual notification audiences, phase-anchored reminders and the notification outbox. Each phase defaults to 60 seconds for its first repeat and 120 seconds for its all-user stage; both are editable in Config. Pending migrations are applied automatically when the backend starts.

The mobile response migration is schema v6. Create the first administrator only after the
Firebase user exists:

```powershell
& "C:\tmp\guardaid-vlm-venv\Scripts\python.exe" -m app.bootstrap_admin `
  --firebase-uid "FIREBASE_UID" `
  --email "admin@example.test" `
  --display-name "School Administrator"
```

The command refuses to run once an active School Administrator exists. Emergency-call
behaviour is simulation-only and never opens a dialler.
