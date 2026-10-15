# GuardAId Mobile — DPP Alignment and Implementation Adjustments

Updated: 2026-10-07

This file records how the Flutter implementation aligns with DPP Figures 3.23–3.34 and identifies every deliberate adjustment. It is additive project evidence for the later DPP revision.

## UI and workflow alignment

| DPP prototype | Flutter implementation |
|---|---|
| Figure 3.23 navigation | Four-tab Alerts, Updates, Logs and Settings shell |
| Figure 3.24 authentication | Splash, sign-in, forgot-password, pending approval and disabled access |
| Figure 3.25 Alert Centre | Counts, search, type/status filters, shared incident visibility and alert cards |
| Figure 3.26 alert review | Evidence image/video, AI confidence, VLM summary, disclaimer and response actions |
| Figure 3.27 dismiss/resolve | Updated to Dismiss as false alarm or Confirm incident, followed by an explicit handling path, optional comment, actor-attributed history and Logs |
| Figure 3.28 escalation | New Escalate action replaced by Confirm-only role-targeted Broadcast and bounded reminder stages; optional emergency-call simulation remains in-app only |
| Figure 3.29 updates | Read/unread notifications and mark-all-read confirmation |
| Figure 3.30 saved logs | Resolved and dismissed history, search, status filter and evidence reopening; historical Escalated records remain read-only legacy history |
| Figures 3.31–3.33 administration | Role sensitivity, user-role assignment and borderline recipient settings |
| Figure 3.34 logout | Confirmation and Firebase sign-out |

## Architecture alignment

- Flutter uses typed `User`, `Role`, `Alert`, `EvidenceRecord`, `NotificationRecord` and settings objects, preserving the DPP class-diagram domain intent without forcing UI widgets to mirror persistence entities.
- Firebase Authentication provides identity; FastAPI verifies the ID token and SQLite decides role, activation status and sensitivity.
- FCM carries a minimal alert payload. Evidence is fetched separately through an authenticated FastAPI endpoint.
- Every final incident response and administrator change is persisted as an action/audit record.

## Deliberate adjustments to record in the revised DPP

1. **Firestore and Cloud Storage replaced.** Operational records use normalized SQLite (currently schema v11) and evidence remains in server local filesystem storage. Firebase is limited to Authentication and FCM.
2. **Assigned Cameras label adjusted.** The current UI says “Monitored cameras” because the schema does not yet model user-to-camera assignment. If assignment becomes a project requirement, add a normalized relation and restore the DPP label.
3. **No public self-registration.** Firebase accounts are provisioned by an authorised operator through the authenticated Mobile App. The first administrator remains a one-time Firebase Console plus local bootstrap step; later users are created and assigned one role in the App. Existing same-project Firebase identities require explicit linking confirmation.
4. **First administrator bootstrap added.** A local, one-time command establishes the first School Administrator and refuses to run once an active administrator exists, avoiding a circular approval dependency.
5. **One role per prototype user.** Although the relational table can support multiple roles, the current administrator UI intentionally assigns one primary role so inherited sensitivity is unambiguous.
6. **Role sensitivity is inherited.** Certain, Moderate or Sensitive is stored on the role; changing it affects every assigned user and creates an audit record.
7. **Emergency call is simulation-only.** It never opens a dialler or contacts an external service. The action is stored as `simulated_emergency_call`, and the UI explicitly states that no real call was made.
8. **Android-first delivery.** Flutter remains structurally cross-platform, but Firebase/APNs and iOS release configuration are outside this Android prototype stage.
9. **Demo and live modes separated.** Demo mode uses local sample data and never contacts Firebase. Live mode must be explicitly enabled with build-time flags after Firebase/backend configuration.
10. **Role-aware delivery added (original policy, superseded for visibility on 2026-10-07).** Alert list visibility and FCM recipients originally respected role sensitivity. VLM-assisted borderline notifications additionally respect the configured eligible role list. The current shared-visibility policy is recorded below; this entry is retained as change history.
11. **Operational UI states added.** Loading, empty, pending-account, disabled-account, unavailable-evidence and VLM-pending states were added because the DPP wireframes mainly show successful flows.

## Safety and privacy invariants

- Firebase passwords, refresh tokens, service-account keys and FCM registration values are never stored in Flutter application data or ordinary logs.
- CCTV video, evidence and VLM descriptions are not uploaded to Firebase.
- The mobile app never receives a server filesystem path.
- Pending or disabled users cannot register a notification device or access alert evidence.
- Physical-phone access requires an authenticated, controlled network path; Firebase Authentication alone does not make a local server safe to expose publicly.

## 2026-09-26 user-management implementation update (additive)

- **In-App account creation.** Settings > User assignments includes Add user for operators with
  `can_manage_users`. The backend uses the existing server-only Firebase Admin credential to create
  an Authentication identity with a random, non-disclosed initial password, then creates the active
  SQLite user, one role assignment and audit row in a transaction. The client requests a Firebase
  password reset/setup email for the new identity without signing out the administrator. A request
  accepted by Firebase is not represented as guaranteed mail delivery; the administrator can
  request it again from the user action menu.
- **Explicit identity linking.** If an email already exists in the same Firebase project but is not
  in SQLite, the App asks the operator to confirm linking before the backend creates the SQLite
  assignment. An email already present in SQLite is never duplicated. A newly created Firebase
  identity is deleted as compensation if the local transaction fails; a failed compensation is
  surfaced as a partial failure for administrator follow-up. If an email is already in SQLite, the
  App opens that existing user's assignment instead of creating another record or sending another
  setup email.
- **Reversible access lifecycle.** Deactivate account preserves the Firebase identity, SQLite user,
  role and audit/alert history, sets local status to `disabled`, and disables registered FCM devices.
  Reactivation reuses the stored role and displays its sensitivity and permissions for confirmation;
  the user's next authenticated session refresh re-registers its FCM device. Revoke role remains a
  different action that removes the role and places the user in `pending`.
- **Server-side guardrails.** Firebase ID token verification and `can_manage_users` checks are
  required on every administration endpoint. The backend rejects self role/status changes,
  self-deactivation, removal of the last active School Administrator, and assignment of permissions
  the operator does not hold. Demo Mode mirrors the screens in memory and never contacts Firebase.
- **No schema migration for this addition.** Existing `users`, `user_roles`, `devices` and `audit_logs`
  tables in schema v7 already represent the required identity link, one role, pending/active/disabled
  status, device registration and audit trail.

## Development completion scope (testing deferred)

The current development pass treats formal unit, integration, system, UAT, usability,
and security testing as Chapter 5 work. They are intentionally not used as blockers for
the implementation pass.

The mobile development implementation now also includes the DPP Figure 3.25 filter
dimensions that are meaningful for the loaded alert data: incident type, status, threshold
mode, camera source, date range, and free-text search. Live mode reads the persisted
borderline-recipient settings from the authenticated backend rather than silently using
demo defaults. Pending users can remain in `pending` status while an administrator reviews
their assignment; they are not forced into `active` access before approval.

The following list records an earlier approved prototype snapshot; later dated updates below
supersede an item when the implementation changed. It is retained so the DPP update does not
lose the history of the original design decisions:

- (Superseded by the 2026-09-08 implementation update below) roles were fixed to the three prototype roles, so Figure 3.31 create-role was then deferred. Custom roles are now supported.
- user lifecycle uses pending/active/disabled access states; permanent Firebase account
  deletion and the DPP discipline-team role are deferred from this Android prototype;
- the emergency call is simulation-only and never opens the dialler;
- Firebase Auth/FCM are retained, while operational records and media use SQLite and the
  authenticated local/server media endpoint instead of Firestore/Cloud Storage.

## 2026-09-08 implementation update (additive)

The following DPP-aligned items are now implemented and should be reflected when
Chapter 3 is revised. The earlier decisions above remain historical notes and are
not deleted.

- SQLite schema v7 adds role display names, three granular management permissions and
  role timestamps. System roles remain protected; custom roles can be created with
  case-insensitive name validation and only permissions already held by the actor.
- The mobile app now provides Create Role, Profile, and Assigned Users screens. Role
  cards open the assigned-user list, where administrators can reassign or revoke a
  role. Revoke is transactional: the user becomes Pending, FCM devices are disabled,
  and an audit record is written; self-revoke and last-active-administrator removal
  are rejected.
- Display names are the primary user label and email is secondary. Firebase token
  names no longer erase a locally edited SQLite display name. Profile editing updates
  the current session and UI immediately.
- Legacy VLM JSON, double-encoded JSON, plain text and malformed output are converted
  to a readable scene summary on the API/mobile read path. Raw model JSON is not shown
  on Alert Centre or Alert Review.
- Sensitivity changes reload the current session, roles, users and alerts before the
  controller notifies listeners. App resume also performs a guarded refresh, so a
  logout/login cycle is not required to see inherited sensitivity changes.
- Android creates `guardaid_urgent_alerts_v1` and `guardaid_updates_v1` channels. The
  urgent channel uses a small original GuardAId two-tone WAV, max importance and
  vibration for weapon, violence and physical-contact bullying alerts. It does not use
  full-screen intents or open a dialler. The channel id is versioned because Android
  persists channel sound settings.
- The non-functional Settings > Application informational tiles (`Evidence access` and
  `Prototype build`/`Prototype view`) were removed; evidence authorization and actual
  evidence playback remain available from Alert Review.
- Alerts cards now present status, confidence and sensitivity as labelled responsive
  metrics instead of a crowded row of badges. Alert Review retains typed action history
  and shows the latest decision, actor display name, timestamp and note.
- Initial alert delivery snapshots exact user recipients. Later dismiss, resolve and
  escalate decisions create routine FCM update notifications for the other original
  recipients, exclude the decision actor and use `guardaid_updates_v1` rather than the
  urgent alert channel. The alert action/API records remain the source of actor and
  decision attribution.
- GitHub remains source-only. DPP, reports, tests, Firebase configuration, datasets,
  models, SQLite/runtime evidence and generated build files stay local and are blocked
  by `.gitignore`. Live Firebase values are supplied at build time; the Google Services
  Gradle plugin is enabled only when a developer provides the private local JSON file.

## Additional adjustments — 2026-10-02

- Alert Centre uses pull-to-refresh without a dedicated refresh button. While the
  Alerts tab is visible in the foreground, it refreshes the session, alert feed and
  notification feed every five seconds. Administration data is not fetched on each
  polling tick. In-flight requests are shared; requests do not overlap.
- Polling pauses on other tabs, in the background, and when another route (including
  Alert Review or the filter sheet) covers the feed. Background errors retain the
  last successful data and the user's filters/scroll position.
- While browsing older records, the visible snapshot remains stable. New cards are
  queued behind a small `New alerts · View` control. A separate arrival notice lasts
  four seconds. View, pull-to-refresh, or returning to the top applies the latest
  snapshot. The persistent entry respects current filters and does not auto-scroll.
- Alert unread dots represent per-user viewing, persisted in SQLite migration 9
  (`alert_reads`). Opening Alert Review marks only that user's alert as viewed.
  Alert lifecycle status and Updates notification-read status remain independent.
  Demo Mode stores the same viewing behavior in memory.
- Dismiss, Resolve and Escalate comments are optional, including empty or omitted
  comments at the API. Escalate still requires at least one recipient role. This
  records the earlier requested adjustment to the original reason/message form.
- Latest Decision spans the available row, retaining actor, action, time, optional
  comment and history within one decision panel above static incident metadata.

## Additional adjustment — 2026-10-07: shared incident visibility

- All authenticated Active users with a valid role can view every alert, Alert Review and the
  corresponding evidence, regardless of inherited sensitivity or FCM device registration.
- Role sensitivity controls initial notification delivery only. Sensitive alerts notify Sensitive
  roles; Moderate alerts notify Sensitive and Moderate roles; Certain alerts notify all three
  sensitivity levels. Existing borderline-recipient restrictions still apply to relevant pushes.
- The all-user reminder stage changes notification recipients, not visibility: the incident is
  already accessible to all Active role-assigned users before that stage.
- Pending, Disabled and roleless users remain unable to access protected incident/evidence APIs.
  Per-user unread state and optional user-selected list filters remain independent.
- Role sensitivity descriptions now explicitly describe notification preferences. No SQLite
  migration or new per-user alert copy is required for this access-policy change.
- The proposed Confirm/handling workflow remains a separate planned change; this update changes
  visibility and explanatory copy only.

## Additional adjustment — 2026-10-07: shared alert response workflow

This dated addendum supersedes the earlier planned-workflow notes above while
preserving them as historical record for the next DPP revision.

- **Shared incident access.** Every authenticated Active user with a valid Role
  can list/open every alert and its evidence from inception. Sensitivity controls
  push recipients only; Pending, Disabled, unauthenticated and roleless users are
  still denied by the backend. The second reminder's broader audience changes
  push delivery, not when a user gains access to the alert page.
- **Decision flow.** Alert Review begins with `Dismiss as false alarm` or
  `Confirm incident`. Confirmation requires one explicit route: `Confirm only`
  with at least one Broadcast Role, `Confirm and handle it myself`, or `Confirm ·
  already resolved`. A confirmed unassigned alert permits one user to take
  ownership; only its handler can mark an in-progress incident resolved. The
  backend enforces state transitions and serializes competing ownership claims.
- **Broadcast and notifications.** The Confirm-only initial Broadcast and first
  repeat go to the selected roles regardless of their sensitivity; the second
  repeat goes to all Active role-bearing users with enabled devices. Undecided
  alerts use the sensitivity audience initially and on first repeat, then the
  same all-active audience at stage two. Defaults remain 60/120 seconds from
  each phase anchor and can be changed in the dashboard Config page. Phase A and
  Phase B use independent persisted schedules.
- **No `Escalate` response action.** The old action has been replaced in new UI by
  Confirm plus optional role-targeted Broadcast. Historical Escalate entries are
  retained as read-only legacy history. Simulated emergency call remains optional
  and never opens the dialler.
- **Optional comments and ownership.** Comments may be omitted or left blank for
  every workflow action. Taking ownership is a state change; Updates tells other
  prior alert recipients who acted. Alerts remains the shared live incident
  queue, Updates is the notification/collaboration feed, and Logs contains
  terminal incidents and their response history.
- **Handler release scope.** This iteration does not provide a Handler release,
  reassignment, or return-to-queue action. After a user takes ownership, the
  handler remains assigned until the incident is resolved; the UI does not show
  a non-functional release control. A future release workflow would need an
  explicit audited action and a deliberate reminder-phase restart.
- **Storage change.** SQLite schema v11 adds per-alert workflow state/events,
  phase-based reminders, per-stage dispatches and a durable notification outbox
  with user and device delivery records. It retains v10 reminder tables and
  `alert_actions` for compatibility; history is not destructively rewritten.
- **Delivery limitation.** An outbox retry means backend delivery is persisted
  and retried; FCM acceptance still cannot prove Android displayed or the user
  heard the notification. Live-phone checks, role audience coverage and sound
  audibility remain runtime acceptance items, not claimed as passed by unit tests.
- **Queue and history navigation.** Alerts opens on the `Active` filter
  (Awaiting confirmation, Confirmed — awaiting handler, and In progress) so
  responders start with current work. The explicit `All` filter can still show
  terminal records; Logs remains the dedicated resolved/dismissed history view.
- **Concurrent decision protection.** Each workflow action carries the
  workflow revision shown to the user. The API rejects a new action based on a
  stale revision with a conflict; the App reloads the alert and asks the user to
  review its latest state. Repeating an already accepted request with its
  original idempotency key remains a no-op rather than creating duplicate
  history or notifications.
