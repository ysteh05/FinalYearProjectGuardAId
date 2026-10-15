# GuardAId Alert Response Workflow — Implementation and Acceptance

Last updated: 2026-10-07

## Purpose

This record tracks the requested shared alert visibility, confirm/dismiss/handler
workflow, role-targeted Broadcast, two-phase reminders, and the distinct purposes
of Alerts, Updates and Logs. It is an implementation record, not evidence that
every item has passed.

## Initial source audit

The following facts were observed before this implementation began:

- The backend currently stores the legacy alert statuses `open`, `in_review`,
  `resolved`, `dismissed` and `escalated`.
- The mobile response endpoint currently accepts only `dismiss`, `resolve` and
  `escalate`; its reason is optional in the API model, although the screen also
  exposes resolve/escalate as initial actions.
- SQLite migration 010 adds one reminder schedule per alert and two dispatch
  rows keyed by alert and stage. The worker repeats to the original notification
  recipients, then sends a second-stage notification to all active role-bearing
  users. Any legacy `alert_actions` row currently stops reminders.
- The current Alert Review UI still offers dismiss, resolve and escalate as
  peer actions. The screen already separates simulated-call activity from
  decision history.
- Mobile alert list/detail access currently uses active-account and valid-role
  checks. The list/detail no longer applies role-sensitivity filtering; this is
  the intended direction. Evidence access also passes through the same
  protected alert lookup.
- Initial notification delivery already filters by alert sensitivity and
  applicable borderline settings. Broadcast currently uses the legacy
  `escalate` action and a role-targeted notification helper.
- The app has distinct Alerts, Updates and Logs tabs. Alerts has pull-to-refresh,
  five-second foreground polling, a new-alert entry and a transient notice.
- Existing schema is at migration 010. Alert actions, per-user reads, notification
  recipients, evidence and audit records are already persisted.
- Flutter models and Demo repository still express the legacy response states.
- The project root contains a `.git` directory, but Git reports that it is not a
  repository; no commit/push will be attempted as part of this work.
- No applicable `AGENTS.md` was found in the backend or mobile app source trees.

## Target workflow

All authenticated, Active users with a valid Role may view every alert, its
detail, evidence and history. Sensitivity affects notification audiences only.
Pending, Disabled, roleless and unauthenticated users remain denied.

For undecided alerts, the initial choices are `Dismiss as false alarm` and
`Confirm incident`. Confirmation must select exactly one path:

1. `Confirm only`: choose one or more Broadcast Roles, enter the
   `Confirmed — awaiting handler` state and start a new reminder phase.
2. `I'll handle this`: atomically assign the current user as the single Handler
   and enter `In progress`.
3. `Already resolved`: record confirmation and completion, then stop reminders.

Dismissed and Resolved incidents appear in Logs. Active work remains in Alerts.
The Alerts screen defaults to active work so new responders are not shown a
history-heavy queue; the explicit `All` filter remains available when a user
needs to find a terminal incident. This is a presentation default, not an
access-control restriction.
Updates contains relevant collaboration events and routes back to Alert Review.
Comments are optional. Simulated calls are in-app only and do not change the
workflow state or reminder clock. New UI does not offer Escalate; old history is
preserved and remains readable. Handler release/reassignment back to
`Confirmed — awaiting handler` is explicitly out of scope for this iteration;
the App exposes no such control, so an incident cannot silently restart Phase B.

### User-directed Confirm dialog refinement (2026-10-07)

The user chose to assume that deployable Broadcast Roles will have active users,
so the app must not show an empty-recipient count/warning or ask for a separate
acknowledgement before confirming. `Confirm only` still requires at least one
valid selected Role in both the app and API. If that Role currently resolves to
no eligible device, the incident decision and Phase B schedule are still saved;
the outbox simply has no initial recipients, and the app uses neutral status
wording rather than claiming a push was delivered. Reminder dispatch continues
to resolve current eligible users/devices when it runs. The legacy API field
`allow_empty_audience` remains accepted for older clients but no longer changes
this behavior.

The empty-Role validation is rendered inside the open confirmation sheet (next
to its submit control), not as a SnackBar hidden behind that sheet. The optional
call control is titled `Simulate emergency call`; its safety subtitle still
states that the dialler does not open and no real call is made.

Broadcast success feedback uses its own queued-dispatch count, separate from
routine collaboration updates. The API retains `notificationsQueued` as the
combined total for compatibility, and additionally returns
`broadcastDispatchesQueued` and `decisionUpdateDispatchesQueued`. The App must
use only the Broadcast-specific field before saying a selected-role Broadcast
was queued.

Reminder phases are independent and persist in SQLite:

- Phase A (`awaiting_decision`): initial notification and first repeat use the
  current sensitivity audience; the second repeat targets every currently
  eligible Active role-bearing user.
- Phase B (`awaiting_handler`): initial Broadcast and first repeat use the
  selected Role audience without an additional sensitivity filter; the second
  repeat targets every currently eligible Active role-bearing user.
- Default timing is 60 and 120 seconds from the phase anchor. The second delay
  is cumulative and must be greater than the first. Dispatches are bounded to
  two per phase. If both deadlines elapsed during downtime, only the all-user
  stage is sent.
- Confirming cancels Phase A. Taking ownership, dismissing or resolving stops
  the applicable phase. Reading, comments, simulated calls and incident merging
  do not reset its clock.

## Implementation sequence

1. Complete source audit and document the state model and migration approach.
2. Add versioned persistence/API behavior with transactional state changes,
   authorization, concurrency control and idempotency.
3. Finalize shared alert/evidence visibility while keeping sensitivity-based
   initial notification filtering.
4. Implement phase A and B reminders, Role Broadcast targeting, dynamic
   eligibility checks and the existing 2/3/4-beep routing.
5. Implement matching Demo and Live mobile flows.
6. Refine Alerts, Updates, Logs and Alert Review presentation/navigation.
7. Verify refresh behavior, collaboration updates, historical compatibility
   and regressions.
8. Complete endpoint, repository, Flutter and runtime verification; update the
   run guide, database design and DPP alignment.

## Acceptance checklist

### Access and notification audiences

- [x] Active role-bearing users can list/open every alert and its evidence.
- [x] Unauthenticated, Pending, Disabled and roleless users are denied by the
  backend.
- [x] Certain users can view Sensitive alerts and do not receive their initial
  sensitivity-targeted notification.
- [x] The three-level sensitivity notification matrix is verified.
- [x] Reading state remains per-user and separate from incident state.

### Decisions and ownership

- [x] Initial UI offers only Dismiss and Confirm.
- [x] Comments are optional end-to-end.
- [x] Confirm choices are mutually exclusive and persist correctly.
- [x] Confirm-only requires at least one valid Broadcast Role.
- [x] Broadcast defaults follow each alert's Sensitivity; a selected Role is not
  filtered by Sensitivity again. Confirm-only requires a valid selected Role,
  but currently eligible recipient/device count does not block confirmation or
  produce a warning in the app.
- [x] Confirmation feedback distinguishes selected-role Broadcast dispatches
  from routine alert-update dispatches; the combined queue count cannot trigger
  a false Broadcast success message.
- [x] Only one user can acquire Handler ownership under concurrency; a simultaneous
  loser receives a stale-workflow conflict rather than silently replacing the Handler.
- [x] Handler can resolve; action history retains actor, time and optional note.
- [x] Handler release/reassignment is documented as out of scope and no inactive
  release control is shown.
- [x] New UI omits Escalate while preserving legacy history.
- [x] Simulated call remains inside the app and never affects reminder state.

### Reminders and notifications

- [x] Phase A initial/first reminder use sensitivity; second stage uses all
  eligible active role-bearing users.
- [x] Phase B initial/first reminder use the selected Role audience; second
  stage uses all eligible active role-bearing users.
- [x] A is cancelled when B starts; stopping actions cancel active phases.
- [x] Timing is cumulative from the phase anchor and safely configurable.
- [x] Restart, concurrency, duplicate workers, stale dispatch and overdue-stage
  behavior are covered.
- [x] Backend channel IDs and packaged WAV assets map initial/repeat/all-user
  stages to two/three/four beeps; routine updates use the ordinary channel.
- [x] 15-second incident merging retains evidence and does not restart reminders
  or send a duplicate initial notification.

### User experience and compatibility

- [x] Alerts, Updates and Logs have distinct purposes and correct navigation.
- [x] Alert Review clearly shows status, actor, action, time, comment and
  Broadcast Role where relevant.
- [x] Existing structured AI summary, evidence aspect ratio, unread state and
  alert refresh behavior remain correct.
- [x] Alerts defaults to active incidents; `All` can still show terminal history,
  and Logs remains the dedicated terminal-record/history view.
- [x] A new action based on an outdated workflow revision is rejected by the
  API with a conflict; the App refreshes the latest incident before asking the
  user to review and retry. An idempotent network retry remains a no-op.
- [x] Demo mirrors the workflow without Firebase writes or real FCM.
- [x] Live API authorization is enforced server-side; partial failures do not
  show false success.
- [x] Existing records, media, read state and legacy actions survive migration.
- [x] Narrow screens and 1.3x/2.0x text scale remain usable without overflow in
  Flutter layout tests and the Realme GT Neo 2 AVD Demo integration run.
- [x] Backend and Flutter widget tests pass.
- [ ] Flutter static-analysis recheck after the Confirm-dialog refinement;
  the latest invocation stalled without diagnostics (the previous source
  revision's analysis passed, and the current sources compile in widget tests).
- [x] Runtime/live-device checks are clearly labeled PASS, FAIL, PENDING or
  BLOCKED; unperformed checks are not reported as passed.
- [x] Run guide, database design and DPP alignment reflect the actual behavior.
- [x] No unrelated data/model/config changes or automatic Git publication.

## Evidence log

| Date | Area | Result | Evidence / notes |
|---|---|---|---|
| 2026-10-07 | Initial source audit | PASS | Backend, Flutter, Demo/Live repositories, SQLite migrations, API authorization, FCM sender/outbox, reminder worker, run guide, database design, DPP alignment and relevant test files were reviewed before implementation. |
| 2026-10-07 | Backend workflow-focused tests | PASS | `tests/test_notification_escalation.py`, `tests/test_sqlite_repository.py`, and `tests/test_firebase_notification_payload.py`: latest combined no-cache run passed 58 tests in 32.14s (one upstream Starlette/httpx deprecation warning only). An earlier combined focused run reported 64 passed. Coverage includes shared visibility/evidence including Active roleless denial, sensitivity matrix, Broadcast defaults/targeting, Phase A/B routing, restart recovery, duplicate-worker suppression, idempotency, selected-role confirmation with an empty current audience, overdue-stage selection, stale queued reminder suppression, handler-only resolve, simulation history, migration recovery, merge boundaries, simultaneous API handler acquisition, 15-second incident merge, and fake-sender FCM envelope/channel routing. No actual Firebase sender or device token was used. |
| 2026-10-07 | Flutter static analysis | PASS WITH TOOLING NOTE | `dart analyze` and the Flutter SDK analyzer reported no issues. The ordinary `flutter analyze --no-pub` wrapper stalled during the later recheck; see the detailed static-analysis recheck row below. |
| 2026-10-07 | Flutter widget tests | PASS | Latest full suite rerun after queue-counter separation: **36 passed** using the Flutter SDK tool snapshot with `--no-pub --concurrency=1`. New cases verify zero-recipient confirmation, Broadcast-specific success copy when only a routine update is queued, and inline empty-Role validation while the modal stays open. |
| 2026-10-07 | Full backend regression suite | PASS WITH TOOLING WARNING | Latest full rerun after queue-counter separation used `pytest tests -q -p no:cacheprovider` and passed **81/81 tests in 52.46s**, exit code 0. One upstream Starlette/httpx deprecation warning remains. No real Firebase request or device notification was made. |
| 2026-10-07 | VLM/video dependency regression isolation | PASS | `test_scene_description_builds_exact_uniform_native_video_input` passed in two isolated no-cache runs: 21.01s previously and 10.83s in the latest rerun; the full-suite `0x8007000e` traceback did not recur. |
| 2026-10-07 | FCM envelope for background-capable delivery | PASS (stubbed sender only) | Five parameterized cases exercise the actual `FcmNotificationSender` message builder with a fake Firebase module. Initial urgent, initial Broadcast, first-repeat, all-user repeat, and routine decision updates each include a visible notification title/body plus alert data and the expected Android channel ID. The fake sender returns a synthetic provider ID; no network call, credential, or real token is involved. This verifies payload construction, not Firebase delivery or Android presentation. |
| 2026-10-07 | Static analysis recheck | PASS WITH TOOLING NOTE | After the notification-channel initialization split, `dart analyze lib test integration_test` completed with `No issues found!`. Earlier, Flutter's own `flutter_tools.snapshot analyze --no-pub` also passed; the ordinary `flutter analyze --no-pub` wrapper produced no output and spun in its command wrapper, so it was cancelled after process inspection. The SDK analyzer passes; the batch wrapper remains a local tooling issue. |
| 2026-10-07 | Runtime / Live FCM / device sound | PENDING | Emulator integration verifies native channel registration and read-back without changing notification permission. Source audit confirms the backend builds visible notification title/body plus data and an Android channel ID; the App has a foreground listener, FCM tap-open handling, and initial-message retrieval. This code-path evidence does not verify real Live FCM delivery, background/terminated remote-message display, notification tap-through, or audible playback. No real FCM was sent and no operational database migration was run. |
| 2026-10-07 | Active Alerts default and stale-action concurrency | PASS | Alerts opens on the `Active` filter while `All` still exposes resolved/dismissed history. Workflow actions carry the screen's expected revision; SQLite rejects a new stale action with HTTP 409, the App reloads the latest feed, and same-key retries are idempotent. Regression tests cover both paths. |
| 2026-10-07 | Demo reminder behavior | PASS | Deterministic Flutter test advances a fake clock through Phase A's first and second reminder, verifies duplicate suppression and the all-user stage, then confirms handling stops the reminder phase. |
| 2026-10-07 | Simultaneous handler acquisition | PASS | A barrier releases two requests from different active test users against the local API with the same observed workflow revision. Exactly one returns HTTP 200 and acquires ownership; the other returns HTTP 409 with the stale-workflow explanation. The persisted workflow has one Handler and exactly one `take_ownership` event. Test authentication and notification delivery are local stubs/console mode; no real FCM is sent. |
| 2026-10-07 | Initial notification sensitivity matrix | PASS | Temporary SQLite databases with one Sensitive, one Moderate and one Certain role verified recipients for Sensitive, Moderate and Certain alerts. Recipient sets were respectively Sensitive; Sensitive+Moderate; and all three levels. The recording sender did not call Firebase. |
| 2026-10-07 | Reminder restart and duplicate-worker recovery | PASS | A newly constructed repository/worker resumed Phase A from its SQLite phase row and sent first-repeat then all-user stage. Two independent workers released simultaneously for one due stage produced one send and one persisted sent-dispatch row; a repeated scan produced no second send. |
| 2026-10-07 | Active roleless authorization | PASS | A test Firebase identity with SQLite status Active but no role received HTTP 403 for alert list, alert detail and evidence API requests. |
| 2026-10-07 | Beep asset and channel mapping | PASS (static/audio-file checks) | Existing WAV analysis verifies exactly 2/3/4 loud beep regions with the intended gap and duration; backend dispatch tests verify stage channel IDs; Flutter tests verify stage routing and force decision updates onto the routine channel even if payload metadata requests an urgent channel. Actual Android channel installation and human audibility remain pending. |
| 2026-10-07 | 15-second merged evidence and reminder preservation | PASS (local integration test) | A temporary SQLite repository and synthetic 26-second source video exercise the actual incident merge, evidence extraction, media replacement and persisted reminder row. Two detections exactly 15 seconds apart become one incident; the final playable clip spans the union interval and visibly contains frames from both event windows; only the initial notification dispatch occurs; reminder phase ID/status/anchor/due times remain unchanged. Timer is controlled by the test; no real FCM is sent. |
| 2026-10-07 | Emulator Demo integration (fresh rerun) | PASS | Rebuilt and reran `flutter test integration_test/emulator_operations_test.dart --no-pub -d emulator-5554` against current source on the `Realme_GT_Neo_2_API_36` AVD: 5/5 passed in 1m23s. Four cases cover navigation, initial Dismiss/Confirm, confirm/handle/simulated-call, resolution, filters, updates/logs/settings/admin drafts; the fifth verifies native channels. The Demo workflow case itself does not initialize FCM or send notifications. |
| 2026-10-07 | Native Android notification-channel runtime check | PASS (channel registration/config only) | The integration test called the production channel-registration method on the AVD, read the channels back from Android, and verified urgent/repeat/all-user IDs, raw sound resource names, max importance, alarm audio usage, no DND bypass, and the routine channel's system-default notification sound/importance. Notification permission state was read before and after and remained unchanged. This does not prove sound is audible or remote FCM behavior. |
| 2026-10-07 | Current focused verification | PASS WITH TOOLING WARNING | Latest full backend suite: **81 passed in 52.46s**; the prior audience-refinement focused backend tests passed 4; this delta's API dispatch-separation test passed; isolated native-video regression previously passed 1. Full Flutter widget suite: **36 passed**, including queue-counter-specific UI feedback. Flutter SDK static analysis had passed before these UI deltas; a fresh analyzer invocation this turn stalled without diagnostics, so the current static-analysis recheck is not claimed. Fresh Emulator integration previously passed 5/5, including Android channel read-back; it was not rerun because the Emulator was shut down and these changes are covered by widget/API tests. No real FCM was sent. |
| 2026-10-07 | Confirm dialog audience and validation refinement | PASS (automated verification) | Confirm-only with a valid Role and zero eligible notification devices succeeds with `allow_empty_audience=false`, persists `confirmed_awaiting_handler` plus an active Phase B reminder, and queues no fabricated initial Broadcast. Missing Role selection remains HTTP 409 / repository `ValueError`. Flutter tests verify no recipient-count warning/acknowledgement control, the “Simulate emergency call” label, and inline accessible empty-Role validation. |
| 2026-10-07 | Broadcast vs. routine queue accounting | PASS | API regression reproduces the prior misleading case with one queued routine update and zero selected-role recipients: `notificationsQueued=1`, `broadcastDispatchesQueued=0`, `decisionUpdateDispatchesQueued=1`. The Flutter view uses the Broadcast-specific count, so it cannot call the routine update a Broadcast. Full backend suite: 81 passed; full Flutter suite: 36 passed. No FCM network request was made. |
| 2026-10-07 | Static-analysis recheck after Confirm dialog refinement | PENDING | `flutter_tools.snapshot analyze --no-pub` displayed its initial “Analyzing mobileapp...” line but produced no diagnostics or completion before it was stopped. The full Flutter widget suite compiled and passed; formatting was applied. The already-running user Live Flutter process was left untouched. |

## Verification status

The full backend suite, workflow-focused tests, Flutter SDK static analysis, complete
Flutter widget suite, deterministic Demo reminder test for both phases, initial-sensitivity
matrix, simultaneous API handler acquisition, reminder restart/duplicate-worker
tests and Emulator integration suite have now been run; their latest
evidence is listed above. Native Android channel registration/configuration is
verified on the Emulator, but Live FCM background/terminated delivery,
notification tap-through and physical-device sound acceptance remain pending. The current engineering
progress estimate is approximately 97% after native channel registration was
verified on the Emulator; it is not a test pass percentage and
does not override those pending acceptance gates.

## User-assisted physical-device checks

These are remaining manual acceptance checks, not a reason to pause other
implementation or local verification work. Follow the current
`MOBILE_PHONE_RUN_GUIDE.md` section 4.1 startup procedure; keep all Firebase
credentials on the user's computer and never send credential files, tokens,
private logs or private network details in chat.

Safety prerequisites:

- Use a dedicated test login and a test-only incident/video. Do not create a
  false test incident in front of real recipients.
- Before a first-repeat test, confirm the chosen sensitivity or Broadcast Role
  includes only consenting test accounts. If that cannot be confirmed, do not
  trigger a Live notification.
- The second reminder targets every eligible Active role-bearing user. Test its
  four-beep sound only in an isolated test dataset/project where every possible
  recipient is a consenting test account; never let this stage run on a shared
  school deployment for this check.
- Do not change phone volume, notification permission, DND, network or system
  settings for this check. Do not open Settings, notification shade, launcher,
  browser or dialler, and do not use the simulated-call control as a real call.

Suggested shortest Live sound check, only after the recipient-safety checks
above:

1. Sign in to the test account in Live Mode and use an approved test clip to
   produce one new alert. Observe whether the initial alert is delivered and
   whether its two-beep sound is audible; record only pass/fail and the in-app
   alert state.
2. If the alert's eligible audience is test-only, temporarily set the Config
   reminder delays to 15 seconds and 30 seconds. Leave the alert unanswered
   until the first reminder; check for the three-beep sound, then take ownership
   in GuardAId before the 30-second all-user stage. This must stop later
   reminders.
3. For Phase B, use another test-only alert, choose `Confirm only` and a
   Broadcast Role containing only test accounts. Check the initial Broadcast,
   wait for its first reminder, check three beeps, then take ownership before
   the all-user stage.
4. Test the four-beep all-user stage only in the isolated test environment
   described above. Confirm each consenting test account receives one
   notification; do not run this test against real school users.
5. Restore the production-configured reminder delays after temporary testing.
   Record whether routine decision updates use the ordinary sound, not an urgent
   beep. No system-level notification interaction is requested.

Report each sound as `audible`, `not audible` or `not tested`, and whether the
phone stayed in GuardAId. If Live setup or safe recipient isolation is not
available, stop before sending and report that condition; do not share secrets
to troubleshoot it. Background/terminated tap-through remains a separate
Emulator/system integration check and is not claimed PASS by the steps above.
