# GuardAId Testing and Evaluation Plan

## Document Control

| Item | Value |
|---|---|
| Project | GuardAId: Human-in-the-Loop AI School Safety Monitoring Prototype |
| Document purpose | Master testing plan for the AI models, simulated CCTV WebUI, backend, database, notification workflow, and Android mobile application |
| Version | 1.0 |
| Date | 6 September 2026 |
| Status | Unit-test execution recorded; integration, system, non-functional, and UAT execution remains pending |
| Result convention | `NOT RUN`, `PASS`, `FAIL`, `BLOCKED`, or `NOT APPLICABLE` |

This document is a plan, not a completed test report. Existing model-validation artefacts are identified as prior evidence, but no unexecuted test is presented as a pass.

## 1. Purpose

The purpose of this plan is to verify that GuardAId:

1. detects visible weapons and observable violence or physical-contact bullying with measurable performance;
2. uses the VLM as decision support without replacing human judgement;
3. produces urgent alerts, local evidence, and mobile notifications through a traceable end-to-end workflow;
4. enforces Firebase Authentication and SQLite-based role authorisation;
5. provides a usable English-language Android application for authorised school personnel; and
6. remains sufficiently responsive and stable for a controlled recorded-CCTV prototype demonstration.

The plan is aligned with the DPP objectives, scope, and Sections 3.7.1-3.7.10. It also adopts useful reporting practices from the two reference reports:

- Tan Yi Jia's Chapter 5 uses compact unit and integration test tables with a test case, expected outcome, result, and appendix evidence. It also reports UAT by participant demographics and evaluation area.
- Ong Yu Chin's Chapter 5 separates functional and non-functional testing, tests complete module navigation flows, and uses Android CPU, memory, and energy profiling.

GuardAId extends those practices by adding frozen dataset rules, objective acceptance criteria, AI-specific metrics, end-to-end alert latency, security checks, failure-path testing, and traceability to the DPP.

## 2. System Under Test

The tested prototype uses the following implemented architecture:

```text
Recorded CCTV-like video / uploaded camera source
    -> shared video decoding and real-time simulation
    -> YOLOv9-T weapon detection
    -> MC3-18 violence / physical-contact bullying detection
    -> Qwen3-VL 2B borderline review and scene description
    -> FastAPI backend services
    -> normalized SQLite operational database
    -> local server filesystem for evidence
    -> Firebase Authentication for identity
    -> Firebase Cloud Messaging for push notification
    -> Flutter Android mobile application
    -> authorised human review and response action
```

Cloud Firestore, Firebase Cloud Storage, and Cloud Functions are not part of the implemented prototype. Firebase passwords are managed by Firebase Authentication and are not stored in SQLite. FCM messages contain only the minimum alert identifier and summary; evidence is retrieved separately from an authenticated backend endpoint.

### 2.1 Main components

| Component | Implementation under test |
|---|---|
| Weapon detector | Validation-selected YOLOv9-T, class-aware NMS, 15 class-and-sensitivity thresholds, N-of-M temporal confirmation |
| Violence detector | Validation-selected MC3-18, 20-frame spatiotemporal input, Certain/Moderate/Sensitive thresholds |
| VLM borderline reviewer | Qwen3-VL 2B Instruct, native-video input, bitsandbytes NF4 4-bit, frozen binary prompt |
| VLM scene description | Qwen3-VL 2B Instruct, configurable uniform frame budget with a default of 20 frames, asynchronous generation |
| Web application | Simulated CCTV Monitor, Alerts, Cameras, and Config pages |
| Backend | FastAPI services and inference orchestration |
| Structured persistence | Normalized SQLite schema version 6 |
| Media persistence | Server-local filesystem using safe relative paths and SHA-256 metadata |
| Identity and push | Firebase Authentication and Firebase Cloud Messaging |
| Mobile application | Flutter Android application ID `com.guardaid.prototype` |

## 3. Scope and Traceability

### 3.1 DPP objective and scope traceability

| Trace ID | DPP commitment | Primary verification |
|---|---|---|
| OBJ-1 | Develop and evaluate visible weapon detection for CCTV-like footage | Weapon held-out evaluation, threshold analysis, speed measurement, system scenarios |
| OBJ-2 | Train and evaluate observable violence or physical-contact bullying detection | MC3-18 held-out evaluation, observation-ratio analysis, speed measurement, system scenarios |
| OBJ-3 | Implement VLM-assisted concise scene descriptions for flagged alerts | Structured human rubric, response-time measurement, mobile evidence-review UAT |
| SCP-1 | Recorded CCTV-like footage rather than direct live school CCTV integration | Upload, playback, real-time-paced inference, alert, and evidence system tests |
| SCP-2 | Certain, Moderate, and Sensitive role-based alerting | Threshold-policy unit tests, role delivery integration tests, administrator UAT |
| SCP-3 | VLM-assisted review of borderline violence candidates | Threshold-only, Sensitive-only, and VLM-assisted condition comparison |
| SCP-4 | Alert relevant personnel and preserve human responsibility | FCM tests, alert review, dismiss, resolve, escalate, audit-log, and simulation-only emergency tests |

### 3.2 In scope

- model evaluation and operational inference behaviour;
- WebUI camera CRUD, monitoring, progress, ETA, results, alerts, and configuration;
- FastAPI API validation, error handling, and authorisation;
- SQLite migrations, constraints, transactions, joins, aggregation, and audit records;
- local evidence creation, replacement, integrity, playback, and protected retrieval;
- Firebase Authentication token verification and account states;
- FCM token registration, routing, foreground/background delivery, and failure handling;
- Flutter mobile screens, role-based workflows, responsive layout, and evidence viewing;
- end-to-end recorded-CCTV scenarios;
- performance, resource use, reliability, usability, and prototype-level security;
- controlled UAT with representative users or role proxies.

### 3.3 Out of scope

- direct connection to production school CCTV or RTSP infrastructure;
- autonomous disciplinary decisions or claims that the prototype guarantees safety;
- real emergency-service calling; the app must remain simulation-only;
- iOS release and APNs testing;
- production-scale penetration testing, red-team testing, or regulatory certification;
- public unauthenticated internet exposure of FastAPI, SQLite, or evidence storage;
- Firestore, Cloud Storage, and Cloud Functions;
- testing on real violent incidents involving students.

## 4. Test Principles

1. **Human-in-the-loop:** every alert remains subject to authorised human review.
2. **No data leakage:** model selection and threshold tuning use validation data only. The test set is used once for the frozen final configuration.
3. **Same-condition comparison:** candidate models must use the same data, prompt, input protocol, precision, hardware, and measurement method.
4. **Alert-first safety policy:** direct alerts, provisional borderline alerts, evidence persistence, and notification dispatch must not wait for scene description.
5. **Reproducibility:** every evaluation records dataset manifest, model/checkpoint hash, prompt version, configuration version, random seed, software versions, and hardware.
6. **No fabricated success:** model-load, decoding, network, notification, or VLM failures must be reported explicitly.
7. **Privacy by design:** published evidence must not reveal passwords, tokens, service-account contents, private IP addresses, personal computer paths, or identifiable real-school data.
8. **Evidence for every result:** each PASS or FAIL must reference a log, screenshot, CSV/JSON result, profiler capture, database query, or video recording.

## 5. Dataset and Experimental-Control Plan

### 5.1 Dataset split policy

| Split | Permitted use | Prohibited use |
|---|---|---|
| Train | Train or fine-tune a model; optional LoRA; development smoke samples such as five Fight and five NonFight clips | Final model comparison or final performance claim |
| Validation | Compare model candidates; select checkpoints; select Certain/Moderate/Sensitive thresholds; validate the frozen VLM prompt and resource settings | Report as the final unbiased benchmark |
| Test | One final evaluation of the already selected model, thresholds, prompt, preprocessing, and quantisation | Repeated tuning, prompt rewriting, threshold selection, or model reselection |
| Operational scenario set | End-to-end workflow, alert timing, evidence, FCM, WebUI, and mobile tests | Model-accuracy claims unless the set has independent labels and a documented sampling method |
| UAT media set | Safe scripted or public-domain demonstration clips for user tasks | Training, threshold tuning, or claims about real school prevalence |

If a final test result causes any model, threshold, prompt, or preprocessing change, that test set has become development data. A new untouched test set must then be created for the final benchmark.

### 5.2 Borderline candidate construction

For the selected MC3-18 model, the borderline pool is created after scoring the chosen split with frozen thresholds:

```text
Sensitive threshold <= MC3-18 confidence < assigned user threshold
```

The pool must include both labelled Fight and NonFight clips. Its manifest must store only a non-sensitive clip identifier, ground-truth label, MC3-18 confidence, threshold mode, and evaluation split. The same borderline candidates must be used for every VLM candidate comparison.

### 5.3 Frozen VLM protocol

Before validation comparison or final testing, freeze and archive:

- model ID and revision;
- NF4 4-bit quantisation configuration;
- native-video processor configuration;
- binary prompt version and SHA-256;
- input FPS, video token budget, output-token limit, and generation settings;
- scene-description prompt and default 20-frame uniform sampling rule;
- JSON parser and safe fallback rules;
- random seed and deterministic settings where supported.

Prompt exploration is not part of the main comparison. If the prompt changes materially, all affected validation runs must be repeated and the final test must remain untouched.

### 5.4 Existing validation evidence to preserve

The following artefacts already exist and should be treated as development or validation evidence, not newly executed tests:

- YOLOv9-T was selected from the weapon-model validation comparison.
- MC3-18 was selected from MC3-18, R(2+1)D-18, and R3D-18 using validation results.
- A completed Qwen3-VL 2B validation run with the frozen binary prompt reported a recall gain of 9.09 percentage points, precision retention of 99.08%, assisted F2 of 0.9635, 10 additional alerts, and zero parse failures for that run.

These values are useful baselines. They must not be described as final held-out VLM test performance until the frozen configuration is evaluated on the designated test set.

## 6. Test Levels and Execution Order

Testing will be executed in the following order:

1. environment and artefact verification;
2. static checks and unit testing;
3. AI model evaluation on the correct split;
4. backend, database, media, Auth, FCM, and mobile integration testing;
5. complete system and regression testing;
6. non-functional performance, reliability, compatibility, usability, and security testing;
7. UAT;
8. final defect correction and full regression;
9. Chapter 5 result consolidation.

A later stage should not begin if an unresolved blocker makes its results unreliable. For example, FCM delivery timing must not be measured while the phone clock or Firebase project configuration is invalid.

## 7. Test Environment and Configuration Record

The following record must be completed before formal execution.

| Environment item | Value to record |
|---|---|
| Host operating system | Windows edition, build, and update level |
| CPU and system RAM | Exact model and usable RAM |
| GPU | Exact NVIDIA model, total VRAM, driver, CUDA runtime |
| Python environment | Python, PyTorch, torchvision, Transformers, accelerate, bitsandbytes, TorchCodec, OpenCV, FFmpeg |
| Backend | FastAPI, Uvicorn, SQLite schema version |
| Browser | Chrome and/or Edge version |
| Flutter toolchain | Flutter, Dart, Android SDK, Gradle, Java |
| Physical Android device | realme GT Neo 2, Android version, available storage, network type |
| Emulator | Realme-equivalent profile, 1080 x 2400 logical display target, API level, RAM allocation |
| Firebase | Project ID alias only; Authentication and FCM enabled; never record credentials |
| Network | Local controlled LAN or emulator loopback; do not publish a private IP in the report |
| Model artefacts | Filename, logical model version, SHA-256, quantisation |
| Test start/end | Timestamp and tester |

Absolute mobile performance conclusions must come from the physical phone. The emulator is suitable for repeatable UI layout, navigation, and basic compatibility checks but not for authoritative battery, GPU, or network performance measurements.

## 8. Entry, Suspension, and Exit Criteria

### 8.1 Entry criteria

- the application builds and starts in demo mode;
- live mode configuration has been completed without committing secrets;
- model files and dataset manifests are available;
- the final evaluation configuration is versioned and frozen;
- the designated validation and test splits are confirmed not to overlap;
- the backend can create a clean schema version 6 SQLite database;
- at least one active administrator and test accounts for the other roles exist;
- the physical phone can authenticate and reach the backend through the approved network path;
- safe test footage and expected outcomes have been reviewed.

### 8.2 Suspension criteria

Testing must pause if any of the following occurs:

- repeated host black-screen, GPU-driver reset, thermal shutdown, or uncontrolled out-of-memory event;
- evidence corruption or accidental use of private real-world footage;
- validation/test-set overlap or evidence of test-set tuning;
- Firebase credentials, device tokens, private paths, or private network addresses appear in an export;
- a test could open a real dialler or contact an external emergency service;
- the test environment changes without being recorded.

### 8.3 Exit criteria

- all Critical and High test cases have been executed;
- all Critical defects are closed and no High security or evidence-integrity defect remains open;
- final model metrics and operational latency are reproducible from saved manifests;
- the end-to-end weapon, violence, borderline, non-incident, and failure scenarios have evidence;
- role-based access and alert delivery are verified for all three roles;
- the mobile app completes its core tasks on the physical phone without overflow or crash;
- UAT findings and limitations are documented;
- every result table identifies sample size, environment, execution date, and evidence reference;
- a final regression run is completed after the last code change.

## 9. Defect Severity and Result Rules

| Severity | Definition | Example |
|---|---|---|
| Critical | Safety, privacy, or authorisation failure that makes the prototype unsafe to demonstrate | Non-admin changes roles; real emergency dialler opens; another role accesses unauthorised evidence |
| High | Core detection or incident workflow is unusable or evidence is incorrect | Direct alert waits for VLM; evidence cannot play; alert action is not persisted |
| Medium | Important function has a workaround | One filter is incorrect; retry is required after recoverable network failure |
| Low | Cosmetic or minor usability problem | Spacing inconsistency with no task impact |

A test is `PASS` only when all stated expected results and acceptance criteria are met. A partial result is `FAIL`, not a partial pass. A blocked test records the blocker and is rerun after correction.

## 10. AI Model Evaluation

### 10.1 Common evaluation controls

All candidate comparisons must use:

- the same split manifest and labels;
- the same preprocessing and input resolution;
- the same host hardware and power mode;
- warm-up iterations excluded from steady-state timing but reported separately;
- at least three timing repetitions where practical;
- confusion matrix counts, not only rounded percentages;
- per-sample prediction exports;
- median and 95th-percentile latency in addition to mean latency;
- 95% bootstrap confidence intervals for the main accuracy metrics where sample size permits.

Accuracy alone is not sufficient for any safety-related model in this project.

### 10.2 Weapon detection evaluation

#### Metrics

| Metric | Purpose |
|---|---|
| Precision | Reliability of predicted weapon detections |
| Recall | Proportion of actual visible weapons detected |
| F1-score | Balance of precision and recall |
| IoU | Bounding-box overlap quality |
| mAP@0.50 | General localisation and classification performance |
| mAP@0.50:0.95 | Stricter localisation performance across IoU thresholds |
| Per-class AP | Performance for parang/machete, knife, baseball bat, long gun, and gun |
| Image-level confusion matrix | Weapon versus background operational decision |
| FPS and latency/frame | Timeliness on the project hardware |

The operational decision order must be tested exactly as implemented:

```text
model candidates -> class-aware NMS -> per-class confidence threshold
-> weapon/no-weapon frame -> N-of-M confirmation -> alert and notification policy
```

#### Required comparisons

- report the validation comparison that selected YOLOv9-T;
- keep all 15 validation-selected class thresholds frozen;
- run the selected YOLOv9-T once on the held-out test set;
- report Certain, Moderate, and Sensitive image-level precision, recall, F1, false positives, and false negatives;
- report difficult-condition slices where labels are available: small object, occlusion, blur, low light, and visually similar non-weapons;
- include representative true positive, false positive, and false negative images.

#### Acceptance and interpretation

- the test run must complete with 100% of valid test images accounted for;
- no test-set threshold tuning is allowed;
- the Sensitive mode should prioritise recall, Certain should prioritise precision, and Moderate should provide the middle operating point; departures must be explained from the measured data;
- the selected model must be justified using localisation, classification, false-alert behaviour, and speed together, not one metric;
- results support only a controlled prototype claim, not production weapon-screening certification.

### 10.3 Violence and physical-contact bullying evaluation

#### Metrics

| Metric | Purpose |
|---|---|
| Accuracy | Overall correctness, reported with class balance |
| Precision | Reliability of positive incident predictions |
| Recall | Proportion of actual Fight clips detected |
| F0.5 | Precision-weighted evaluation for Certain mode |
| F1 | Balanced evaluation |
| F2 | Recall-weighted evaluation for Sensitive mode |
| Balanced accuracy | Fairer performance under unequal class counts |
| ROC-AUC | Ranking/separation across thresholds |
| Confusion matrix | TP, FP, TN, and FN transparency |
| Clips/second and seconds/clip | Operational processing speed |
| Observation-ratio performance | Early recognition after 25%, 50%, 75%, and 100% of a clip is observed |

#### Required comparisons

- preserve the validation comparison among MC3-18, R(2+1)D-18, and R3D-18;
- use validation data to select MC3-18 and the three operating thresholds;
- evaluate only the frozen selected model on the held-out test set;
- report all three threshold modes on the same test predictions;
- include normal movement, running, sports/play, crowds, falls, and unclear contact among negative or difficult cases where available;
- include representative TP, FP, TN, and FN clips or frame strips.

#### Acceptance and interpretation

- every valid test clip must receive exactly one reproducible score;
- the test manifest, checkpoint hash, preprocessing, and thresholds must match the frozen configuration;
- Certain-mode F0.5, Moderate-mode F1/balanced accuracy, and Sensitive-mode F2/recall are the primary mode-specific summaries;
- observation-ratio results must be described as early-warning analysis, not as a separate trained model unless retraining occurred;
- violence and observable physical-contact bullying remain one operational class; verbal, cyber, and social bullying are outside model scope.

### 10.4 VLM scene-description evaluation

Scene descriptions are open-ended text and must not be evaluated using classification accuracy alone.

#### Sample design

- target at least 30 raised alerts;
- include weapon alerts, violence/physical-contact alerts, borderline alerts, unclear scenes, occlusion, low light, and long merged evidence;
- use the same frozen scene prompt and default 20 uniformly sampled frames unless the test explicitly studies frame budget;
- anonymise samples and randomise review order;
- use two independent human raters where feasible, followed by adjudication of major disagreement.

#### Five-point rubric

| Criterion | 1 - Very poor | 3 - Acceptable | 5 - Very good |
|---|---|---|---|
| Relevance | Misses the main visible event | Mentions the main event but omits useful detail | Focuses on the most decision-relevant visible content |
| Factual correctness | Contains material unsupported claims | Mostly grounded with a minor ambiguity | Fully grounded in visible evidence and calibrated uncertainty |
| Clarity | Difficult to understand | Understandable with minor awkwardness | Immediately understandable by school personnel |
| Conciseness | Unhelpfully empty or excessively long | Usable length with some redundancy | One or two focused sentences suitable for rapid review |
| Consistency | Similar scenes receive contradictory treatment | Generally similar structure | Stable content and uncertainty treatment across comparable inputs |
| Prompt following | Required structure is absent | Most required fields are present | Valid expected structure with all constraints followed |

Also record:

- JSON/structured-output success rate;
- material hallucination count;
- omission of the principal visible action;
- uncertainty appropriateness;
- median, mean, and P95 response time;
- peak VRAM and host RAM;
- inter-rater agreement, using weighted Cohen's kappa or percentage agreement as appropriate.

#### Proposed acceptance criteria

- mean relevance, factual correctness, clarity, and conciseness each at least 4.0/5;
- at least 95% of outputs contain no material unsupported claim;
- at least 95% prompt/format success after the documented safe parser;
- no description states identity, motive, guilt, or injury severity without visible support;
- description failure never delays, withdraws, or corrupts an existing urgent alert;
- response time is reported rather than hidden; because description is asynchronous, alert latency remains the primary operational gate.

### 10.5 VLM-assisted borderline evaluation

The primary research question is whether VLM assistance recovers true incidents for stricter roles without causing excessive additional false alerts.

#### Compared conditions

| Condition | Definition |
|---|---|
| Threshold-only | A user receives only clips meeting the assigned MC3-18 threshold |
| Sensitive-only | A user receives every clip meeting the Sensitive threshold |
| VLM-assisted borderline | Borderline clips are reviewed; only VLM-positive candidates are added to the user's threshold-only alerts |

#### Metrics and formulae

```text
Precision = TP / (TP + FP)
Recall = TP / (TP + FN)
F-beta = (1 + beta^2) * Precision * Recall / ((beta^2 * Precision) + Recall)

Recall Gain (percentage points)
    = 100 * (Recall_assisted - Recall_threshold_only)

Precision Retention (%)
    = 100 * (Precision_assisted / Precision_threshold_only)

Additional Alert Count
    = alerts_assisted - alerts_threshold_only
```

Report accuracy, precision, recall, F0.5, F1, F2, TP, FP, TN, FN, recall gain, precision retention, precision change, additional alerts, parse failures, median/P95 latency, and peak resource use.

#### Objective decision rule

The selected VLM configuration is acceptable for the prototype when all of the following are satisfied on validation data and then confirmed on the untouched final test set:

1. recall gain is greater than 0 percentage points;
2. precision retention is at least 95%;
3. assisted F2 is not lower than threshold-only F2;
4. the additional false-positive and alert counts are reported and judged operationally manageable;
5. at least 95% of valid inputs produce a usable binary result without a parser or runtime failure;
6. the model runs within the safe memory envelope of the project hardware; and
7. the prompt, quantisation, video input, and generation settings remain frozen.

The 95% precision-retention threshold is a proposed project acceptance rule, not a value dictated by the DPP. It must be frozen before the final test. Recall gain and precision retention must be interpreted together: high recall gain is not beneficial if precision collapses and produces alert fatigue.

#### Model-comparison requirement

A 2B/4B/8B size comparison is not mandatory unless the final report claims that Qwen3-VL 2B is the best model size. It is academically valid to preselect Qwen3-VL 2B for local-resource feasibility and then evaluate whether that chosen configuration satisfies the objective rule above. If Qwen3-VL 2B and Qwen3.5 2B are compared, the comparison must occur on validation data only, under identical conditions; only the selected configuration proceeds to the final test.

## 11. Unit Testing Plan

Automated unit tests should use temporary databases and media directories. Firebase and FCM should be mocked unless the test explicitly targets live integration.

| ID | Component | Test case | Expected result | Priority | Status | Evidence |
|---|---|---|---|---|---|---|
| UT-001 | Video policy | Generate overlapping windows including a short final tail | No duplicate window; final frames remain covered | High | NOT RUN | |
| UT-002 | Video policy | Segment duration, stride, FPS, and frame-budget boundary values | Invalid values are rejected with a clear error | High | NOT RUN | |
| UT-003 | Violence policy | Map Certain/Moderate/Sensitive to the frozen MC3-18 thresholds | Exact configured threshold is returned | Critical | NOT RUN | |
| UT-004 | Borderline policy | Score at, below, and above range boundaries | Candidate inclusion follows the frozen interval rule | Critical | NOT RUN | |
| UT-005 | Weapon policy | Apply class-aware NMS before class thresholding | Duplicate boxes are suppressed and valid classes remain | Critical | NOT RUN | |
| UT-006 | Weapon policy | Evaluate N-of-M sequences and exact boundary | Alert triggers only on the first qualifying recent window | Critical | NOT RUN | |
| UT-007 | Incident policy | Compare merge-gap and notification-cooldown boundaries | Alert grouping and repeated push suppression remain independent | Critical | NOT RUN | |
| UT-008 | Alert service | Merge source intervals from the same camera/type/run | One alert has multiple detection events and correct aggregate times | Critical | NOT RUN | |
| UT-009 | Alert service | Start a new uploaded-video run at source time zero | It does not merge into an earlier run's alert | Critical | NOT RUN | |
| UT-010 | VLM parser | Parse valid binary JSON and approved narrow fallback | Consistent boolean decision is returned | High | NOT RUN | |
| UT-011 | VLM parser | Malformed, contradictory, empty, or unsafe VLM output | Failure is recorded; no guessed decision is created | Critical | NOT RUN | |
| UT-012 | Scene sampling | Uniformly sample 20 frames from 5, 20, 40, and 60 second evidence | Exactly 20 chronological frames and correct source metadata are produced | High | NOT RUN | |
| UT-013 | SQLite migration | Create a database from migrations 001-006 | Schema version is 6 with all required tables and constraints | Critical | NOT RUN | |
| UT-014 | SQLite integrity | Insert invalid FK, duplicate unique value, or invalid enum | Transaction is rejected and database remains consistent | Critical | NOT RUN | |
| UT-015 | SQLite aggregation | Store multiple detection events for one alert | first/last time, max confidence, and count are derived correctly | High | NOT RUN | |
| UT-016 | Config versioning | Save settings twice | New immutable version is created; previous version remains reproducible | High | NOT RUN | |
| UT-017 | Local media | Save evidence bytes | Safe relative path, size, MIME type, and SHA-256 are correct | Critical | NOT RUN | |
| UT-018 | Local media | Attempt absolute path or `..` traversal | Access is rejected without disclosing server path | Critical | NOT RUN | |
| UT-019 | Authentication | Verify valid, expired, malformed, wrong-project, and clock-skew tokens | Correct safe response and status code are produced | Critical | NOT RUN | |
| UT-020 | Authorisation | Evaluate administrator, security, teacher, pending, and disabled policies | Only permitted operations and alert levels are returned | Critical | NOT RUN | |
| UT-021 | Notification policy | Select recipients by role sensitivity and borderline eligibility | Exact authorised device set is returned | Critical | NOT RUN | |
| UT-022 | Notification delivery | Handle invalid/unregistered FCM token | Token is disabled or marked failed without breaking alert creation | High | NOT RUN | |
| UT-023 | Emergency simulation | Execute simulated emergency action | `realCallMade=false` and `diallerOpened=false` are persisted | Critical | NOT RUN | |
| UT-024 | Mobile repository | Parse success, empty, pending, failed, and unauthorised API states | Typed state is exposed without crash | High | NOT RUN | |
| UT-025 | Flutter UI | Render core screens at 320 x 640 and text scale 1.3 | No overflow, clipped critical action, or uncaught exception | High | NOT RUN | |

Suggested automated commands, executed from their respective folders:

```powershell
python -m pytest guardaid_monitor/tests -q
python -m pytest vlm_borderline_dashboard/tests -q
flutter analyze --no-pub
flutter test --no-pub
```

The exact Python executable used by the project environment must be recorded in the execution log without publishing a private absolute path in the final report.

## 12. Integration Testing Plan

| ID | Integration flow | Test condition | Expected result | Priority | Status | Evidence |
|---|---|---|---|---|---|---|
| IT-001 | Upload -> media store -> SQLite -> WebUI | Upload a supported MP4 | Camera, media metadata, safe file, and playable source appear | High | NOT RUN | |
| IT-002 | WebUI Config -> SQLite -> inference | Change valid settings and save | New config version is used by the next run and UI values do not revert | High | NOT RUN | |
| IT-003 | Decode -> MC3-18 -> alert | Direct violence score exceeds role threshold | Alert and initial evidence are persisted immediately | Critical | NOT RUN | |
| IT-004 | Decode -> YOLOv9-T -> N-of-M -> alert | Required frames exceed class threshold | Weapon alert contains correct class, confidence, and screenshot | Critical | NOT RUN | |
| IT-005 | Borderline -> provisional alert -> VLM | Borderline clip is detected | Provisional alert/evidence/push occur before VLM completion | Critical | NOT RUN | |
| IT-006 | VLM positive -> existing alert | VLM returns `raise_alert=1` | Same alert is promoted; no duplicate detection event is invented | Critical | NOT RUN | |
| IT-007 | VLM negative/failure -> existing alert | VLM returns 0 or fails | Safety-first alert remains for human review and status is traceable | Critical | NOT RUN | |
| IT-008 | Alert -> description queue | Direct or promoted alert exists | Low-priority scene job is queued without blocking detector processing | High | NOT RUN | |
| IT-009 | Scene description -> SQLite -> clients | VLM returns valid output | Pending changes to ready and text appears in WebUI/mobile | High | NOT RUN | |
| IT-010 | Scene failure isolation | VLM OOM, parse error, or timeout | Alert, evidence, and FCM remain valid; description becomes failed | Critical | NOT RUN | |
| IT-011 | Detection grouping -> evidence rebuild | Events fall inside merge gap | One alert range expands and final continuous H.264 evidence replaces initial clip after quiet period | Critical | NOT RUN | |
| IT-012 | Cooldown -> notification records | Separate alert occurs inside notification cooldown | New alert/evidence is preserved; duplicate push is suppressed and recorded | Critical | NOT RUN | |
| IT-013 | Firebase Auth -> FastAPI -> SQLite | New identity signs in | Pending user is created but protected content/device registration is denied | Critical | NOT RUN | |
| IT-014 | Admin -> user assignment -> role policy | Admin activates and assigns a user | User receives inherited sensitivity and permitted alerts after refresh | Critical | NOT RUN | |
| IT-015 | Alert -> FCM -> Android | Eligible active device receives push | Correct minimal notification arrives and opens the related alert | Critical | NOT RUN | |
| IT-016 | Authenticated media -> Android player | Active authorised user opens evidence | Image or byte-range video loads; no filesystem path is exposed | Critical | NOT RUN | |
| IT-017 | Mobile action -> SQLite -> all clients | Dismiss, resolve, or escalate with reason | Status, action log, saved log, and other clients update consistently | Critical | NOT RUN | |
| IT-018 | Emergency simulation -> audit | User confirms simulation | English confirmation appears; no dialler opens; action is audited | Critical | NOT RUN | |
| IT-019 | FCM token refresh -> devices | Firebase rotates a registration token | New token replaces/updates registration without duplicate device delivery | High | NOT RUN | |
| IT-020 | Restart -> SQLite/local media | Restart backend after alerts exist | Cameras, alerts, settings, evidence, actions, and logs remain consistent | High | NOT RUN | |

## 13. End-to-End System Testing Plan

Each system scenario begins with a known database/configuration snapshot and safe recorded footage. The run must capture source-video time, wall-clock time, backend events, database records, notification delivery, and mobile outcome.

| ID | Scenario | Expected system behaviour | Priority | Status | Evidence |
|---|---|---|---|---|---|
| ST-001 | Visible weapon confirmed by N-of-M | Alert, screenshot, SQLite record, eligible FCM push, mobile review, and action are available | Critical | NOT RUN | |
| ST-002 | Direct violence/physical-contact incident | Urgent alert is created with initial clip without waiting for VLM description | Critical | NOT RUN | |
| ST-003 | True borderline incident | Provisional alert is immediate; VLM result updates the same record; eligible role policy is respected | Critical | NOT RUN | |
| ST-004 | Non-incident borderline candidate | Outcome is traceable; VLM does not silently create an unsupported definitive decision | Critical | NOT RUN | |
| ST-005 | Clearly normal movement | No unnecessary alert is created under the configured mode | High | NOT RUN | |
| ST-006 | Event segments separated by less than merge gap | One alert contains the combined source range and final evidence | Critical | NOT RUN | |
| ST-007 | Event segments separated by more than merge gap but inside push cooldown | Two alert records/evidence sets exist; second push is suppressed, not the second alert | Critical | NOT RUN | |
| ST-008 | Event evidence grows beyond 10 seconds | First available evidence and push are sent promptly; final evidence is rebuilt later | Critical | NOT RUN | |
| ST-009 | One-minute merged evidence with brief event portions | Description uses exactly the configured uniform frame budget; alert workflow stays responsive | High | NOT RUN | |
| ST-010 | VLM unavailable | Direct detection, evidence, notification, and human actions remain usable | Critical | NOT RUN | |
| ST-011 | GPU memory pressure during startup | Models load in the safe sequence or fail visibly; host does not enter uncontrolled failure | Critical | NOT RUN | |
| ST-012 | Camera upload replaced or disabled | New source/config state is reflected without corrupting historical alert evidence | High | NOT RUN | |
| ST-013 | Attempt to delete camera with alert history | Safe conflict is returned and history is preserved | High | NOT RUN | |
| ST-014 | Pending/disabled account | Protected feeds, evidence, device registration, and admin pages remain inaccessible | Critical | NOT RUN | |
| ST-015 | Teacher, Security Personnel, and School Administrator roles | Each sees the correct alert sensitivity and only authorised settings/actions | Critical | NOT RUN | |
| ST-016 | Administrator changes role sensitivity | All assigned users inherit the new mode and an audit record is created | Critical | NOT RUN | |
| ST-017 | Administrator changes borderline recipients | Only eligible Certain/Moderate roles receive VLM-assisted additions | Critical | NOT RUN | |
| ST-018 | Phone temporarily offline during an alert | Backend records the alert; app recovers and refreshes after connectivity returns | High | NOT RUN | |
| ST-019 | Backend temporarily unavailable | App shows a recoverable English error and does not display stale action success | High | NOT RUN | |
| ST-020 | Full human response | User reviews evidence/description, acknowledges, dismisses/resolves/escalates, and retrieves saved log | Critical | NOT RUN | |

## 14. Mobile Application Functional Testing

The mobile test report should follow the concise evidence table style used by the reference reports. Detailed screenshots belong in an appendix rather than interrupting the main Chapter 5 discussion.

### 14.1 Authentication and account state

- valid login and logout;
- invalid email/password and empty fields;
- password-reset request;
- expired, malformed, wrong-project, and clock-skew token responses;
- pending approval screen;
- disabled account handling;
- first administrator bootstrap verification;
- notification-permission denial without loss of login/evidence access.

### 14.2 Alert Centre

- count summary and role sensitivity label;
- incident-type, status, threshold, camera, date, and text filters individually and in combination;
- empty and loading states;
- active versus saved records;
- refresh after push notification or action;
- correct ordering and no duplicated alert card.

### 14.3 Alert details and evidence

- weapon image display;
- violence video playback, pause, resume, seek, and completion;
- byte-range streaming over authenticated requests;
- multiple evidence records for one alert;
- unavailable or deleted evidence state;
- pending, ready, failed, and disabled VLM-description states;
- AI disclaimer, confidence, camera, source time, status, and threshold clarity.

### 14.4 Response actions

- acknowledge/review where exposed;
- dismiss with required reason and confirmation;
- resolve with required reason and confirmation;
- escalate with intended recipients and message;
- cancellation of each confirmation dialog;
- simulation-only emergency call with the exact safety message: `Simulated emergency call has been placed. No real call was made.`;
- action reflected in Updates, Logs, and backend audit history.

### 14.5 Notifications and saved logs

- unread/read indicators;
- mark all as read and confirmation;
- foreground, background, and terminated-app FCM delivery;
- notification tap opens the intended alert;
- resolved, dismissed, and escalated log filters and search;
- historical evidence can be reopened when still retained.

### 14.6 Administrator functions

- view the three fixed prototype roles;
- change inherited sensitivity and observe affected user policy;
- view pending/active/disabled users;
- assign one primary role and change account status;
- enable or disable VLM-assisted borderline delivery;
- choose eligible Certain/Moderate roles;
- verify that non-admin users cannot open or call administrator functions.

## 15. Non-Functional Testing

### 15.1 End-to-end timeliness

For every alert scenario, capture these timestamps using a monotonic clock where possible:

```text
T0 = qualifying source event/window becomes available to the detector
T1 = detector decision is produced
T2 = alert and initial evidence transaction completes
T3 = FCM send request is accepted or fails
T4 = notification is received on the phone
T5 = alert details and evidence are visible to the user
```

Report median, P95, minimum, maximum, and sample size for:

- detector delay: `T1 - T0`;
- persistence delay: `T2 - T1`;
- notification dispatch delay: `T3 - T2`;
- FCM delivery delay: `T4 - T3`;
- usable-alert delay: `T5 - T0`;
- VLM borderline time and scene-description time separately.

Acceptance criteria:

- direct and provisional urgent alert creation must not wait for scene description;
- for evidence that continues beyond 10 seconds, the first available up-to-10-second evidence must be persisted and dispatched before final merge completion;
- the recorded-video simulation must not process future source frames before their playback time;
- any backlog or inference lag must be visible in the WebUI rather than hidden;
- actual values are reported transparently. The prototype is not described as real-time if measured end-to-end delay does not support that description.

### 15.2 Backend and model performance

Measure:

- YOLOv9-T frames/second and latency/frame;
- MC3-18 clips/second and latency/window;
- Qwen model-load time, median/P95 request time, and queue wait;
- peak GPU VRAM, host RAM, CPU, GPU utilisation, and GPU temperature;
- WebUI API response time for alert list, camera list, config save, and evidence start;
- one-feed steady state, then a staged two-feed test only if one-feed resource use is safe;
- model startup sequence and absence of concurrent peak loading.

Safety limits for resource tests must be frozen from the actual host before execution. As a default precaution, stop a staged load test if sustained GPU memory exceeds 90%, system memory exceeds 85%, the GPU driver resets, or the UI becomes unresponsive. Do not jump directly to four concurrent feeds on an 8 GB GPU.

### 15.3 Mobile performance and stability

Use Android Studio Profiler on the physical realme GT Neo 2 to capture:

- cold and warm app start;
- CPU while loading the Alert Centre and playing evidence;
- memory baseline, peak, and post-navigation recovery;
- network time for alert/evidence retrieval;
- frame rendering/jank during scrolling and video playback;
- energy use during a controlled 10-minute alert-review session;
- crashes, application-not-responding events, and memory leaks.

Suggested prototype targets to freeze before execution:

- no crash or ANR in five repeated core-workflow runs;
- no continuously increasing memory after five open/close evidence cycles;
- cold start to usable sign-in screen within 5 seconds on the physical test phone;
- ordinary non-video taps provide visible feedback within 1 second under local-LAN conditions;
- video either begins within 5 seconds or shows a clear loading/error state;
- no critical rendering overflow at supported display and text scales.

### 15.4 Compatibility and responsive layout

Test at minimum:

- physical realme GT Neo 2;
- matching 1080 x 2400 emulator profile;
- narrow 320 x 640 logical layout;
- portrait orientation;
- text scale 1.0 and 1.3, plus a manual accessibility check at a larger scale;
- light/dark behaviour only if both themes are supported;
- current supported Android API and one lower supported API where available;
- Chrome and Edge for the WebUI.

Verify no clipped day/time, overflow warning, inaccessible action, unreadable evidence control, or hidden error message.

### 15.5 Reliability and recovery

- restart backend during idle and during a queued scene-description job;
- disconnect/reconnect phone Wi-Fi;
- deny and later grant notification permission;
- expire the Firebase session and sign in again;
- simulate FCM invalid token;
- simulate VLM timeout, malformed JSON, and out-of-memory error;
- cancel an analysis job;
- recover from unsupported/corrupt video input;
- verify database and media consistency after interrupted writes;
- perform and verify one database-plus-media backup and restore.

### 15.6 Security testing

| ID | Area | Check | Expected result | Priority | Status | Evidence |
|---|---|---|---|---|---|---|
| SEC-001 | Authentication | Request protected API without Bearer token | 401; no protected metadata returned | Critical | NOT RUN | |
| SEC-002 | Token validation | Use malformed, expired, wrong-project, or future-dated token | 401 with safe non-sensitive explanation | Critical | NOT RUN | |
| SEC-003 | Account state | Pending/disabled user requests alerts or evidence | Access denied | Critical | NOT RUN | |
| SEC-004 | RBAC | Teacher calls role/user/settings admin endpoint | 403; no state change | Critical | NOT RUN | |
| SEC-005 | Object access | Authorised user requests an alert outside permitted sensitivity/audience | Access denied without revealing evidence | Critical | NOT RUN | |
| SEC-006 | Evidence path | Use traversal, absolute path, guessed file path, or direct filesystem access | Rejected; no server path disclosure | Critical | NOT RUN | |
| SEC-007 | Upload | Upload unsupported MIME, executable, oversized, or corrupt media | Rejected safely; no execution | High | NOT RUN | |
| SEC-008 | Input validation | Inject SQL-like text, HTML/script, overlong reason, or invalid enum | Parameterised/validated handling; no execution or corruption | Critical | NOT RUN | |
| SEC-009 | Secrets | Scan Git history, logs, exports, APK configuration, and screenshots | No service-account key, password, FCM token, private IP, or private path is exposed | Critical | NOT RUN | |
| SEC-010 | FCM minimisation | Inspect notification payload | Only minimum alert data; no evidence bytes or local path | High | NOT RUN | |
| SEC-011 | Audit | Change role, status, threshold, borderline setting, or alert action | Actor, action, target, and time are recorded without sensitive token | High | NOT RUN | |
| SEC-012 | Emergency safety | Trigger and cancel emergency simulation repeatedly | No dialler intent, telephone permission, or real call occurs | Critical | NOT RUN | |
| SEC-013 | Database integrity | Force failure midway through multi-table write | Transaction rolls back without orphan record | Critical | NOT RUN | |
| SEC-014 | Media integrity | Modify evidence after hashing | Integrity mismatch can be detected during audit/verification | High | NOT RUN | |

This is prototype-level security verification, not a claim of production penetration-test assurance.

## 16. Usability Testing and UAT

### 16.1 Participants

- target: 20 participants, following the scale used in the Tan Yi Jia reference report;
- minimum for a useful descriptive prototype study: 15 completed participants;
- prioritise teachers, school administrators, security-related personnel, or credible role proxies;
- record age band, role/background, mobile-app familiarity, and relevant safety-workflow familiarity;
- do not collect unnecessary sensitive personal information;
- state clearly that convenience sampling limits generalisation.

### 16.2 UAT procedure

1. provide a short safety and prototype briefing without teaching the exact navigation path;
2. obtain consent for anonymous task and questionnaire data;
3. assign one of the three prototype roles;
4. ask the participant to complete the tasks below using safe simulated alerts;
5. record task completion, assistance, error, and time;
6. administer a five-point Likert questionnaire and open-ended questions;
7. conduct a short follow-up interview for confusing or safety-critical findings;
8. anonymise and export raw responses for the appendix.

### 16.3 UAT tasks

| ID | Task | Success condition |
|---|---|---|
| UAT-01 | Sign in and identify the assigned role/sensitivity | Correct information found without assistance |
| UAT-02 | Locate a new weapon alert | Correct alert opened |
| UAT-03 | Identify camera, time, confidence, status, and evidence | All required fields interpreted correctly |
| UAT-04 | Play a violence evidence clip and read the VLM description | Evidence and description reviewed; AI uncertainty understood |
| UAT-05 | Filter alerts by type, status, camera, threshold, or date | Requested result set is obtained |
| UAT-06 | Dismiss a supplied false alarm with a reason | Confirmation and saved log are understood |
| UAT-07 | Resolve a confirmed incident | Status and recorded action are understood |
| UAT-08 | Escalate a simulated urgent incident | Intended recipients and simulation boundary are understood |
| UAT-09 | Find an update and reopen a saved incident | Correct records are found |
| UAT-10 | Administrator: change role sensitivity and borderline recipient setting | Correct setting changed and effect can be explained |
| UAT-11 | Administrator: activate and assign a pending user | Correct user, role, and state selected |
| UAT-12 | Log out | Session ends and protected screen is no longer available |

### 16.4 UAT questionnaire areas

Use a five-point scale from 1 (Strongly Disagree) to 5 (Strongly Agree).

1. The app is easy to navigate.
2. Alert type, location, time, confidence, and status are clear.
3. Evidence images and videos are easy to review.
4. The VLM description helps me understand the visible scene.
5. The app makes clear that the AI output requires human verification.
6. Dismiss, Resolve, and Escalate actions are clear.
7. The emergency-call feature is clearly identified as a simulation.
8. Notifications help me reach relevant alerts promptly.
9. Saved logs make completed actions traceable.
10. The difference among Certain, Moderate, and Sensitive modes is understandable.
11. The borderline-alert setting is understandable to an administrator.
12. The app responds quickly enough for this prototype.
13. I feel confident completing the assigned review workflow.
14. The application appears reliable during the demonstration.
15. Overall, GuardAId is suitable as a prototype to support school safety monitoring.

Open-ended questions:

- Which part was most useful?
- Which label, page, or action was confusing?
- Was any alert information missing?
- Did any VLM description appear unsupported or misleading?
- What is the most important improvement before a real school pilot?

### 16.5 UAT measures and acceptance criteria

Report:

- task success rate;
- assisted versus unassisted completion;
- median task time;
- critical and non-critical error count;
- median and mean Likert score per item;
- percentage of ratings 4 or 5;
- themes and frequencies from open-ended responses;
- results by role/background where the sample is large enough, without overstating significance.

Prototype acceptance targets:

- at least 85% unassisted completion across core tasks UAT-01 to UAT-09;
- at least 80% positive responses (4 or 5) for overall usability and alert clarity;
- median score at least 4 for evidence review, action clarity, human-in-the-loop understanding, and overall acceptance;
- zero participant leaves believing the emergency simulation placed a real call;
- zero unresolved safety-critical misunderstanding of AI confidence, VLM advice, or escalation state.

## 17. Regression Testing

A focused regression suite runs after every bug fix. The full suite runs after the final code change.

Minimum regression set:

- settings persist and no form value reverts before Save;
- day/time is fully visible;
- mobile core screens have no overflow;
- H.264 evidence plays using byte-range responses;
- malformed VLM output cannot terminate the alert pipeline;
- scene descriptions use the configured fixed frame budget;
- merge gap and notification cooldown remain independent;
- urgent evidence/FCM do not wait for VLM;
- token verification, pending/disabled state, and role authorisation remain enforced;
- simulated emergency call never opens the dialler;
- model startup does not concurrently produce unsafe memory peaks.

## 18. Test Evidence and File Organisation

Use a stable, non-sensitive evidence structure:

```text
testing_evidence/
  00_environment/
  01_model_evaluation/
    weapon/
    violence/
    vlm_borderline/
    vlm_scene_description/
  02_unit/
  03_integration/
  04_system/
  05_performance/
  06_security/
  07_uat/
  08_regression/
  defect_log.csv
  test_execution_summary.csv
```

Each evidence item should use an anonymous ID such as `ST-003_run-01`. Before including an artefact in Git or the report:

- remove Firebase ID tokens, service-account values, FCM registration tokens, emails not created for testing, private IP addresses, and absolute paths;
- avoid committing the SQLite runtime database or original CCTV/evidence media;
- use cropped or blurred screenshots if a person is identifiable;
- record checksums for final CSV/JSON result bundles;
- keep raw UAT responses access-controlled and include only anonymised exports in the appendix.

## 19. Required Result Tables for Chapter 5

The final Chapter 5 should include concise summary tables and place repetitive screenshots/raw results in appendices.

### 19.1 Model tables

1. weapon candidate validation comparison;
2. YOLOv9-T held-out test metrics by class and sensitivity mode;
3. violence candidate validation comparison;
4. MC3-18 held-out test metrics by sensitivity mode and observation ratio;
5. VLM scene-description rubric summary with rater agreement;
6. threshold-only versus Sensitive-only versus VLM-assisted borderline comparison;
7. model/resource timing and peak memory comparison.

Recommended borderline table:

| Condition/Model | Precision | Recall | F1 | F2 | Recall Gain | Precision Retention | Extra Alerts | Parse Success | Median/P95 Time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Threshold-only |  |  |  |  | - | - | 0 | - | - |
| Sensitive-only |  |  |  |  |  |  |  | - | - |
| Qwen3-VL 2B assisted |  |  |  |  |  |  |  |  |  |

### 19.2 Software tables

1. unit test summary by component;
2. integration test summary by end-to-end flow;
3. complete system scenario summary;
4. mobile functional test summary;
5. performance and resource summary;
6. security test summary;
7. defect and regression summary;
8. UAT participant profile, task results, Likert results, and thematic feedback.

Use this execution-table template:

| ID | Test Case | Preconditions/Input | Expected Outcome | Actual Outcome | Result | Evidence | Defect ID |
|---|---|---|---|---|---|---|---|
|  |  |  |  |  | NOT RUN |  |  |

## 20. Proposed Chapter 5 Structure

```text
CHAPTER 5: TESTING AND EVALUATION
5.1 Introduction
5.2 Test Environment, Data Splits, and Evaluation Protocol
5.3 AI Model Evaluation
    5.3.1 Weapon Detection Evaluation
    5.3.2 Violence / Physical-Contact Bullying Evaluation
    5.3.3 VLM Scene-Description Evaluation
    5.3.4 VLM-Assisted Borderline Alert Evaluation
5.4 Functional Testing
    5.4.1 Unit Testing
    5.4.2 Integration Testing
    5.4.3 System Testing
    5.4.4 Mobile Application Testing
5.5 Non-Functional Testing
    5.5.1 Performance and Alert Latency
    5.5.2 Resource Use and Stability
    5.5.3 Compatibility and Usability
    5.5.4 Security Testing
5.6 User Acceptance Testing
5.7 Defects, Corrections, and Regression Testing
5.8 Discussion Against Objectives and Limitations
5.9 Conclusion
```

This structure keeps the AI research evaluation separate from software functional testing while still showing that the complete prototype satisfies the DPP workflow.

## 21. Deliverables

The formal testing phase should produce:

- frozen data and configuration manifests;
- per-sample model prediction CSV files;
- model metric JSON/CSV summaries and confusion matrices;
- scene-description rubric and anonymised rater scores;
- automated unit-test and static-analysis logs;
- integration, system, mobile, performance, and security execution sheets;
- FCM and authenticated-evidence evidence captures with secrets redacted;
- Android Profiler captures from the physical phone;
- anonymised UAT questionnaire export and charts;
- defect log and final regression report;
- a Chapter 5-ready results summary with limitations.

## 22. Unit Test Execution Record

Executed on 6 September 2026 against the current workspace after the mobile test-helper fix:

| Suite | Command scope | Result | Notes |
|---|---|---:|---|
| GuardAId Monitor backend | `guardaid_monitor/tests` | 25 passed | One non-blocking Starlette/httpx deprecation warning |
| VLM evaluation dashboard | `vlm_borderline_dashboard/tests` | 7 passed | No failures or skips |
| Flutter mobile app | `mobileapp/test` | 5 passed | Core login, shell, alert detail, role administration, and user administration layout tests |
| Flutter static analysis | `flutter analyze --no-pub` | No issues found | Final analyzer run after the test-helper fix |

The first backend attempt used the operating-system temporary directory and produced setup `PermissionError` failures before tests executed. Rerunning with a project-local writable pytest base directory produced the 25/25 result above; this was an environment-permission issue, not a product failure.

The Flutter unit tests initially stopped while loading the Demo controller because `DemoGuardAidRepository.signIn` intentionally uses a delayed future and widget tests use a fake-async zone. The test helper was corrected to call `WidgetTester.runAsync`, preserving the real Demo sign-in and refresh path. No application login behaviour was bypassed.

These results cover unit/static checks only. Integration testing, complete recorded-CCTV system scenarios, real Firebase/FCM delivery, physical-phone performance profiling, security testing, and UAT remain Chapter 5 activities.

## 23. Source Documents Used to Prepare This Plan

1. `6000CEM - DPP Submission - Teh Yao Sheng.pdf`, especially Objectives and Scope and Sections 3.7.1-3.7.10.
2. `60001CEM - Report - Tan Yi Jia.pdf`, Chapter 5 and its unit, integration, UAT, and appendix-evidence approach.
3. `6001CEM - Final Report - Ong Yu Chin.pdf`, Chapter 5 and its functional-flow and Android profiler approach.
4. `vlm_evaluation_decisions.md`, current data-split, prompt, architecture, and VLM decisions.
5. `guardaid_monitor/DATABASE_DESIGN.md`, implemented SQLite and local-media design.
6. `mobileapp/DPP_MOBILE_ALIGNMENT.md`, implemented mobile scope and approved DPP adjustments.
