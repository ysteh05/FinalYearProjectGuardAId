# Alert and Borderline Review implementation

## Behavioral contract

- Web and Mobile alert lists show every saved, detector-confirmed alert. Violence uses the minimum configured profile threshold; weapons use class-specific minimum thresholds and class-aware N-of-M confirmation. The legacy `sensitivity_mode` column remains for compatibility and does not gate new alert creation or visibility.
- First-notification eligibility is computed from persisted detection qualifications and all of an account's roles. A later qualifying detection can add newly eligible accounts to an open alert without repeating the first notification for an account already sent one.
- Borderline Review remains limited to MC3 violence windows in `[lowest threshold, highest threshold)`. Each unique detection window is persisted before the single VLM worker processes it. A valid positive result can add opted-in accounts; negative, failed, cancelled, or disabled work cannot create a supplemental notification.
- The Web Review switch governs review and Borderline-only notifications, not ordinary alert recording or threshold-qualified notifications. Queued reviews are cancelled when switched off. Running work may finish for audit, but checks the current switch before supplemental dispatch.
- Reminder phases use persisted account audiences and original phase deadlines. Stage 1 requires a successful provider-accepted first notification; later accounts can catch up at the original deadline. The all-active-user stage remains an explicit escalation exception.
- “FCM accepted” is not represented as confirmed phone delivery, display, or sound. Those operating-system outcomes require a real-device check.

## Code boundaries and persistence

- `app/policy.py` and `app/services.py` contain the threshold, profile, and N-of-M rules. An analysis run uses a deep-copied settings snapshot; later configuration changes apply to the next run, while the optional Borderline master switch is rechecked before supplemental delivery.
- `app/inference/orchestrator.py` coordinates detection, evidence, durable review tasks, and outbox dispatch. Review and scene-description work share the existing single prioritized VLM worker. Evidence/codec failures are recorded separately and do not discard an alert or its ordinary notification.
- `app/sqlite_repository.py` owns SQL transactions for review tasks, detection qualifications, account audiences, Borderline preferences, and reminder-stage audiences. Bulk role changes are atomic and revision-checked; account overrides can be changed individually.
- `app/main.py` adds review summaries and current-account notification evidence to API responses. Web alert cards show detector qualifications, class scores, review-window counts/reasons/errors, and evidence state. Mobile detail loads the review and detection audit independently from the compact alert list.
- Migration 013 adds `alerts.evidence_status` without deleting or rebuilding alert history. Existing evidence is marked available; older records with no reconstructable evidence state are marked `legacy_unknown`. New records start as preparing and transition to available or failed.

## Operational notes

- Back up the SQLite database and confirm the deployment's expected data directory before starting the upgraded app. The migration runner applies additive migrations on startup; schema version is now 13.
- Tests should set `GUARDAID_DATA_DIR` to a temporary directory. Importing the module-level ASGI app can initialize/migrate the configured database.
- During this implementation, an earlier module-level app import applied the existing migration 012 to the configured runtime database (schema 11 to 12). Inspection before and after showed 103 alert rows and no observed deletion. No backup was taken at that time. Later automated tests used isolated temporary data directories; migration 013 has not been applied to the runtime database. Create and verify a backup before starting the upgraded app against that database.
- No training code, model weights, training caches, or calibration values were changed for this work.
- FCM “provider accepted” is not proof that a handset displayed or played a notification. A real authorized-device audio check is still required before claiming physical notification behavior is verified.

## Automated validation status

- Backend: `python -m compileall -q app` and `python -m pytest -q -p no:cacheprovider tests` — 101 passed. One pre-existing Starlette/httpx deprecation warning remains.
- Mobile: `flutter test --no-pub` — all 36 tests passed; `flutter analyze --no-pub` — no issues found.
- Formatting: Dart format check passed for the four touched Dart files.
- Web: `node --check app/static/app.js` passed.
- Not yet verified: real-device FCM delivery, foreground/background/lock-screen channel behavior, and audible 2/3/4-beep playback. Those require an authorized handset, valid push setup, and recorded OS notification settings.
