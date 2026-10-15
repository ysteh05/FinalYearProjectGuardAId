# GuardAId Mobile App — Page and Operation Test Checklist

**Document type:** Code-derived manual UI, functional, integration, and usability checklist  
**Application:** GuardAId Mobile 1.0.0 (`com.guardaid.prototype`)  
**Baseline date:** 6 September 2026; alert-workflow cases revised 7 October 2026  
**Source of truth:** Current Flutter app and FastAPI/SQLite backend implementation

> **Current workflow note (7 October 2026):** The P07–P10, O05–O09A, E2E,
> and backend/security cases below reflect shared alert visibility, the
> Confirm/Dismiss/Handler workflow, role-targeted Broadcast, and two-phase
> reminders. The dated Emulator observations in Section 15 are historical
> evidence for the older UI and must not be treated as acceptance of the current
> response flow. Detailed implementation evidence and device-pending checks are
> tracked in [the alert-response implementation and acceptance record](../guardaid_monitor/ALERT_RESPONSE_WORKFLOW_IMPLEMENTATION_AND_ACCEPTANCE.md).
>
> **Physical-phone safety boundary:** On a real handset, perform only actions
> inside `com.guardaid.prototype`. Do not open Android Settings, the notification
> shade, launcher/recent apps, browser, dialler, or any other app; do not alter
> volume, notification permission, DND, network, or system settings. Use an
> Emulator for background/terminated notification taps and other system-level
> checks. If the app leaves GuardAId or a system dialog appears, stop and record
> the case as not run rather than interacting with system UI.

## 1. Purpose

This document inventories every implemented GuardAId Mobile page, screen state, dialog, bottom sheet, and user operation. It is intended to support a systematic page-by-page review covering:

- visual quality, alignment, readability, consistency, and responsiveness;
- whether the interface is understandable and easy to use;
- whether every control produces the correct visible response;
- whether valid and invalid workflows behave correctly;
- whether Firebase Authentication, FastAPI, SQLite, protected local evidence, and FCM behave correctly in Live mode;
- whether failures are explained without crashes, freezes, stale data, or misleading success messages.

This is an execution checklist, not a claim that every item has passed. Record a result and evidence when each item is tested.

## 2. Code and architecture covered

The inventory was derived from:

- `lib/src/app.dart` — startup, repository selection, splash, configuration failure, and access-state routing;
- `lib/src/screens/login_screen.dart` — sign-in, password reset, pending approval, and disabled access;
- `lib/src/screens/home_shell.dart` — shared app bar, four-tab navigation, FCM alert opening, and exit confirmation;
- `lib/src/screens/alerts_screen.dart` — alert list, metrics, search, filters, refresh, and alert cards;
- `lib/src/screens/alert_detail_screen.dart` — evidence, VLM summary, Confirm/Dismiss workflow, Broadcast selection, handler actions, and in-app emergency-call simulation;
- `lib/src/screens/updates_screen.dart` — notification history and mark-all-read;
- `lib/src/screens/logs_screen.dart` — saved incident metrics, search, filters, and record reopening;
- `lib/src/screens/settings_screen.dart` — account display and administrator functions;
- `lib/src/firebase_api_repository.dart` — Firebase Auth, FCM, authenticated HTTP, and API mapping;
- `guardaid_monitor/app/main.py` and `guardaid_monitor/app/sqlite_repository.py` — authorization, persistence, evidence access, audit logging, notifications, and FCM dispatch.

## 3. Test modes must not be confused

| Mode | Authentication | Data and persistence | Notifications | Appropriate claims |
|---|---|---|---|---|
| Demo | Fixed local credential in memory | Seeded in-memory alerts, users, roles, and notifications; resets after restart | No real FCM | UI flow, layout, copy, and basic interaction only |
| Live | Firebase email/password plus backend ID-token verification | FastAPI with SQLite; evidence served from protected local/server storage | FCM device registration and push delivery | End-to-end authentication, authorization, persistence, media, and notification claims |

Passing an operation in Demo mode does **not** prove the corresponding API, SQLite, evidence authorization, or FCM behavior. Important workflows should be run once in Demo mode for fast UI review and again in Live mode for integration evidence.

### 3.1 Mode classification legend

Use the following labels when executing the checklist:

| Label | Meaning | Pass rule |
|---|---|---|
| **D — Demo** | The stated result is a Flutter UI, navigation, validation, search, filter, or in-memory behavior that can be adequately checked with the current seeded Demo repository | A Demo pass is sufficient for that specific presentation/client-side behavior; it does not prove any backend behavior |
| **B — Both** | The operation has a reusable UI portion and an authoritative Live-mode result | Run it in Demo for UI/usability, then in Live for API/persistence/security; both results are required |
| **L — Live** | The result depends on Firebase, FastAPI authorization, SQLite persistence, protected evidence, FCM, real network failure, or a user state not available in the current Demo repository | Only a Live-mode pass can satisfy the item |

For **B** items, record two results, for example `Demo: Pass; Live: Fail`. Such an item remains **Fail overall** until its Live result also passes.

### 3.2 Exact mode mapping by checklist ID

| Page/area | D — test in Demo | B — test in both modes | L — test in Live |
|---|---|---|---|
| P01 Splash | P01-01, P01-03 | — | P01-02 |
| P02 Configuration Error | — | — | P02-01–P02-03 |
| P03 Sign In | P03-01 | P03-03–P03-05, P03-08–P03-09 | P03-02, P03-06–P03-07, P03-10–P03-13 |
| O01 Reset Password | O01-01–O01-02 | O01-03 | O01-04–O01-05 |
| P04 Approval Pending | — | — | P04-01–P04-04 |
| P05 Access Disabled | — | — | P05-01–P05-04 |
| P06 Home Shell | P06-02–P06-04, P06-08 | P06-01 | P06-05–P06-07 |
| O02 Exit Confirmation | O02-01–O02-04 | — | — |
| P07 Alert Centre | P07-06–P07-15 | P07-01–P07-05, P07-16 | — |
| O03/O04 Alert Filters | O03-01–O03-07, O04-01–O04-02 | — | — |
| P08 Alert Review | P08-02–P08-07, P08-13–P08-15 | P08-01, P08-16 | P08-08–P08-12 |
| O05 Dismiss false alarm | O05-01–O05-03 | O05-04 | O05-05 |
| O06 Confirm incident | O06-01–O06-02 | O06-03 | O06-04 |
| O07 Broadcast and Phase B reminders | O07-01–O07-04 | O07-05 | O07-06–O07-08 |
| O08/O09 In-app emergency-call simulation | O08-01–O08-02 | O08-03, O09-01, O09-03 | O09-02 |
| O09A Urgent/repeat and collaboration notifications | — | — | O09A-01–O09A-04 |
| P09 Updates | — | P09-01–P09-03, P09-05 | P09-04, P09-06 |
| O10 Mark All Read | — | O10-01–O10-03 | O10-04 |
| P10 Incident Logs | P10-01, P10-03–P10-08 | P10-02, P10-09 | — |
| P11 Settings | P11-04–P11-05 | P11-03 | P11-01–P11-02 |
| O11 Sign Out | — | O11-01–O11-04 | — |
| P12 Roles and Sensitivity | P12-01, P12-03 | — | P12-02 |
| O12/O13 Role Edit | O12-01–O12-04, O13-01 | O13-02 | O13-03 |
| P13 User Assignments | P13-01–P13-05 | — | P13-06 |
| O14/O15 User Edit | O14-01–O14-06, O15-01 | O15-02 | O15-03–O15-06 |
| P14 Borderline Settings | P14-02–P14-05 | P14-01 | P14-06–P14-08 |
| O16 Borderline Save | O16-01–O16-03 | O16-04–O16-05 | — |
| E2E workflows | — | E2E-07 | E2E-01–E2E-06, E2E-08–E2E-13 |
| Backend/security | — | B12 | B01–B11 |
| U01–U18 UI/UX | Run the full first-pass review in Demo | Recheck populated, long-data, loading, error, and role-specific Live screens | Live-only states must be reviewed in Live |

### 3.3 Practical execution order

1. **Demo pass:** execute every **D** item and the Demo half of every **B** item. This is the fastest way to find layout, wording, navigation, validation, overflow, and general usability problems.
2. **Live pass:** execute every **L** item and the Live half of every **B** item. This proves Firebase login, backend authorization, SQLite writes, evidence access, FCM, persistence, and real error handling.
3. **Final result:** report the feature as passed only when all applicable D, B, and L items pass. A Demo success message alone is never evidence that the Live operation works.

### 3.4 Emulator versus physical-device classification

An Android Emulator is the primary development and repeatable regression environment. Matching the Realme GT Neo 2 resolution in an Android Virtual Device reproduces dimensions reasonably well; it does not reproduce the physical panel, real touch behavior, Realme UI insets, device performance, hardware media decoder, Wi-Fi path to the computer, or OEM background/battery restrictions. Physical confirmation is limited to in-app operations under the safety boundary above; background/terminated delivery and notification-shade interaction must be tested on Emulator, not by leaving the GuardAId app on the phone.

Use these environment labels:

| Label | Meaning | Pass rule |
|---|---|---|
| **EMU** | Emulator is sufficient for this individual client-side behavior | It may be accepted from a repeatable Emulator run with evidence |
| **CROSS** | Run on Emulator first and perform a physical-phone confirmation | Both runs must pass; use this for layouts and workflows affected by real data/device behavior |
| **PHONE** | A physical Android phone is required for final acceptance | An Emulator pass is useful preliminary evidence but cannot close the item |

#### EMU — normally sufficient

The following may be completed primarily on Emulator:

- Splash and configuration-error presentation: P01 and P02;
- text entry, password visibility, dialog cancellation, local validation, and keyboard-submit behavior;
- bottom-tab navigation, search, quick filters, advanced filters, date picker, and empty-state presentation;
- opening and closing sheets/dialogs and testing their client-side validation;
- Alert/Update/Log/Settings card presentation using controlled data;
- administrator page navigation and draft selection behavior before a Live save;
- 320×640, 360/390-width, rotation, and 1.0×/1.3×/1.5× text-scale regression;
- negative API, permission, and data-state tests that are more repeatable with controlled Live test fixtures or mocked responses.

#### CROSS — Emulator plus Realme GT Neo 2 confirmation

At minimum, open and visually review every reachable full page P03–P14 on the physical Realme GT Neo 2 for the applicable user states/roles. The following operations require both an Emulator run and physical-phone confirmation:

- P03-03–P03-05 and P03-08–P03-10 — credential entry, keyboard behavior, successful/failed Live login, and network feedback;
- P06-01–P06-04, P06-08, and O02-01–O02-04 — main shell, badges, responsive navigation, Android Back, and exit confirmation;
- P07-01–P07-05 and P07-16 — real alert loading, metrics, refresh, and long/real card data;
- P08-01–P08-07 and P08-12–P08-13 — alert metadata, summary states, unavailable media, final records, and Back navigation;
- the visible UI and loading/success/failure states of O05, O06, O07, O10, O11, O13, O15, and O16;
- P09–P14 populated-page presentation with real Live data;
- U01–U18, especially keyboard, tap targets, text scaling, Back behavior, and screen-reader semantics.

#### PHONE — mandatory physical-device acceptance

The following cannot be closed using only the Emulator:

| Area | Mandatory IDs or scenario | Why the phone is required |
|---|---|---|
| Firebase/FCM in-app receipt | P03-06–P03-07, E2E-02, O09A-01–O09A-04 | Use a preconfigured dedicated test account and existing system permission state; do not change permission/settings or expose the FCM token |
| Background/terminated delivery and notification tap | P06-05–P06-07, E2E-03 | Emulator only under the physical-phone safety rule; do not leave GuardAId or open the notification shade on the handset |
| Protected evidence | P08-08–P08-11 | Real Wi-Fi path, hardware image/video decoding, buffering, and playback controls |
| Broadcast targeting and staged reminders | O07-05–O07-08, E2E-06, O09A-01–O09A-03 | Use a dedicated isolated test audience; physical checks are limited to foreground/in-app receipt while GuardAId remains open; all-user system delivery is an Emulator check |
| Emergency safety | O08-03, O09-01–O09-03, E2E-07 | The actual phone must prove that no dialler opens and no real call occurs |
| Borderline notifications | P14-06–P14-07, E2E-09 | Real recipient-device delivery must follow the saved policy |
| Session/network recovery | E2E-11–E2E-12 | Exercise recoverable behavior in-app; use Emulator/backend-controlled failures rather than changing phone network/system settings |
| Target-device UI | U13, U15–U18 | Physical display, touch, keyboard/insets, Back, accessibility, and Realme UI behavior |

A single physical Realme GT Neo 2 is sufficient for in-app visual, touch, form, evidence, and foreground workflow confirmation. Use Emulator for background/terminated state, notification-shade tapping, and system permission-state variants. Multi-role FCM targeting can use test accounts in an isolated environment; never trigger all-user sends to real school recipients.

### 3.5 Recommended environment sequence

1. Run all client-side and layout checks on Emulator because screenshots, state reset, screen sizes, and defect reproduction are faster and more consistent.
2. Run Live mode on a Google Play-enabled Emulator to exercise Firebase Auth, FastAPI, SQLite, and controlled error cases.
3. Install the same build on the Realme GT Neo 2 and execute every **PHONE** item plus the physical confirmation portion of every **CROSS** item.
4. Fix failures, rerun the failed environment, then run a short regression in the other environment when shared Flutter code changed.
5. Do not report “Android mobile testing passed” until the mandatory physical-device subset passes.

### 3.6 Test-count summary

Counting each unique Test Case ID once, this document contains **215 test criteria**:

- 101 page-operation cases (`Pxx-xx`);
- 71 overlay/dialog/sheet-operation cases (`Oxx-xx`);
- 13 end-to-end cases (`E2E-xx`);
- 12 backend/security cases (`Bxx`);
- 18 reusable UI/UX criteria (`Uxx`).

The 18 UI/UX IDs are counted as 18 criteria here, although each applicable criterion is actually reviewed on multiple pages. Therefore, the real number of recorded page-level UI observations will be higher than 215.

#### Counts by Demo/Live execution mode

| Mode category | Unique IDs | Emulator execution implication |
|---|---:|---|
| D — Demo | 85 | 85 Demo executions |
| B — Both | 59 | 59 Demo executions plus 59 Live executions |
| L — Live | 71 | 71 Live executions |
| **Total unique IDs** | **215** | — |

Consequently:

- **Emulator + Demo:** 144 executions = 85 D + the Demo half of 59 B;
- **Emulator + Live:** 130 executions = 71 L + the Live half of 59 B;
- **Total Emulator execution instances:** 274;
- **Unique Test Case IDs exercised on Emulator:** 215, because the 59 B items are counted in both the Demo and Live execution totals.

All 215 IDs can be attempted or rehearsed on a suitably configured Emulator. This does **not** mean all 215 can receive final acceptance from Emulator evidence alone.

#### Counts by final device requirement

| Device requirement | Unique IDs | Meaning |
|---|---:|---|
| EMU | 71 | Emulator evidence alone can close the case |
| CROSS | 113 | Emulator execution plus physical-phone confirmation is required |
| PHONE | 31 | Physical-phone evidence is mandatory for final acceptance |
| **Total** | **215** | — |

Within the 71 **EMU-only closable** IDs:

- Emulator + Demo covers 38 execution entries: 36 D plus the Demo half of 2 B;
- Emulator + Live covers 35 execution entries: 33 L plus the Live half of the same 2 B;
- the two B items occur in both counts, so `38 + 35 − 2 = 71` unique IDs.

Use the **144 Demo / 130 Live** figures when planning Emulator workload. Use the **71 EMU / 113 CROSS / 31 PHONE** figures when deciding what evidence is sufficient for final acceptance.

## 4. Implemented roles and access rules

| User state or role | Access represented in current code |
|---|---|
| Signed out | Splash/configuration state and Sign In only |
| Pending | Approval Pending page and Sign Out only |
| Disabled | Access Disabled page and Sign Out only |
| Active Teacher | Four main tabs, all shared alerts/evidence, state-appropriate response actions, updates, logs, settings, and sign-out |
| Active Security Personnel | Same shared alert-viewing and response functions as Teacher; sensitivity changes notification eligibility, not alert visibility |
| Active School Administrator | All active-user functions plus Roles and Sensitivity, User Assignments, and Borderline Alert Settings |

The current backend permits every **authenticated Active user with a valid Role** to view every alert and its protected evidence, regardless of sensitivity. The backend enforces legal response-state transitions and handler ownership. Sensitivity affects notification eligibility only. Administrator and delegated permissions for role/user administration and borderline settings are enforced server-side.

## 5. Explicit boundaries of the current mobile build

The following are not mobile pages or user operations in the current implementation:

- no self-registration or “Create account” page;
- no mobile workflow to create the first administrator; the first administrator is bootstrapped outside the app;
- custom role creation and deletion are available to appropriately authorised administrators; built-in roles cannot be deleted;
- authorised administrators can add/link users, change a user's single role, deactivate accounts, and reactivate accounts; permanent deletion and direct password viewing/setting are not available;
- Profile supports display-name editing; email remains read-only and cannot be changed in-app;
- no individual “mark notification read” control; only “Mark all as read” is implemented;
- no camera configuration or CCTV upload controls in the mobile app;
- no true emergency dialler integration; the app must never open a dialler or place a real call;
- no Firestore or Firebase Storage dependency; Firebase is used for Authentication and Cloud Messaging, while application data is in SQLite and evidence is in protected local/server storage.

## 6. Page and overlay inventory

### 6.1 Full pages and routed states

| Page ID | Page/state | How it is reached | Who can access it |
|---|---|---|---|
| P01 | Splash | App launch for approximately 900 ms while repository initializes | Everyone |
| P02 | Live Mode Configuration Error | Firebase/live repository initialization fails | Everyone in misconfigured Live mode |
| P03 | Sign In | Initialization completes and no user is signed in | Signed-out user |
| P04 | Approval Pending | Firebase identity is valid but backend user status is `pending` | Pending user |
| P05 | Access Disabled | Firebase identity is valid but backend user status is `disabled` | Disabled user |
| P06 | Home Shell / shared navigation | Active user signs in successfully | Active role-assigned user |
| P07 | Alert Centre | Alerts tab, default home page | Active role-assigned user |
| P08 | Alert Review | Tap an alert/log/update, or open a valid FCM notification | Any authenticated Active user with a valid Role |
| P09 | Updates | Updates tab, app-bar notification button | Active role-assigned user |
| P10 | Incident Logs | Logs tab | Active role-assigned user |
| P11 | Settings | Settings tab | Active role-assigned user |
| P12 | Roles and Sensitivity | Settings → Roles and sensitivity | School Administrator only |
| P13 | User Assignments | Settings → User assignments | School Administrator only |
| P14 | Borderline Alert Settings | Settings → Borderline alert settings | School Administrator only |

### 6.2 Dialogs, sheets, and system flows

| Overlay ID | Overlay/flow | Parent page |
|---|---|---|
| O01 | Reset Password dialog | P03 Sign In |
| O02 | Leave GuardAId confirmation | P06 Home Shell |
| O03 | Advanced Alert Filters sheet | P07 Alert Centre |
| O04 | Date Range picker | O03 Advanced Alert Filters |
| O05 | Dismiss as False Alarm confirmation and optional comment | P08 Alert Review |
| O06 | Confirm incident and select one handling path | P08 Alert Review |
| O07 | Confirm-only Broadcast targeting and Phase B reminder lifecycle | P08 Alert Review / Config |
| O08 | Optional in-app Emergency Call Simulation confirmation | P08 Alert Review / Confirm flow |
| O09 | In-app Emergency Call Simulation result dialog | P08 Alert Review / Confirm flow |
| O10 | Mark All as Read confirmation | P09 Updates |
| O11 | Sign Out confirmation | P11 Settings |
| O12 | Edit Role Threshold sheet | P12 Roles and Sensitivity |
| O13 | Role Sensitivity update confirmation | P12 Roles and Sensitivity |
| O14 | Edit User Assignment sheet | P13 User Assignments |
| O15 | User Assignment confirmation | P13 User Assignments |
| O16 | Borderline Settings confirmation | P14 Borderline Alert Settings |

## 7. Reusable UI/UX review for every page and overlay

Apply checks U01–U18 to every relevant page and overlay rather than judging appearance only on the home screen.

| ID | Review item | Acceptance criterion | Result/evidence |
|---|---|---|---|
| U01 | Visual hierarchy | Page title, primary information, and primary action are immediately distinguishable | ☐ Pass ☐ Fail ☐ N/A — |
| U02 | Alignment | Cards, labels, icons, fields, and buttons align consistently; no visibly uneven edges | ☐ Pass ☐ Fail ☐ N/A — |
| U03 | Spacing | Related controls are grouped and unrelated sections have clear separation | ☐ Pass ☐ Fail ☐ N/A — |
| U04 | Typography | Text is readable, weights are consistent, and no essential text is unnecessarily truncated | ☐ Pass ☐ Fail ☐ N/A — |
| U05 | Colour and contrast | Text/status colours remain readable; status is not communicated by colour alone | ☐ Pass ☐ Fail ☐ N/A — |
| U06 | English copy | Labels and feedback are grammatical, concise, consistent, and understandable without DPP knowledge | ☐ Pass ☐ Fail ☐ N/A — |
| U07 | Affordance | Tappable cards, buttons, filters, and navigation controls look interactive | ☐ Pass ☐ Fail ☐ N/A — |
| U08 | Primary action | The expected next action is easy to find; destructive/high-risk actions are differentiated | ☐ Pass ☐ Fail ☐ N/A — |
| U09 | Feedback | Tap, loading, success, failure, disabled, and selected states are visible and timely | ☐ Pass ☐ Fail ☐ N/A — |
| U10 | Error recovery | Error text identifies what failed and lets the user retry or safely return | ☐ Pass ☐ Fail ☐ N/A — |
| U11 | Empty state | A zero-result state explains why nothing is shown and, where relevant, how to recover | ☐ Pass ☐ Fail ☐ N/A — |
| U12 | Small screen | No overflow/clipping at 320×640 logical pixels and narrow Android widths | ☐ Pass ☐ Fail ☐ N/A — |
| U13 | Target device | No overflow/clipping on the Realme GT Neo 2 profile (1080×2400 physical display) | ☐ Pass ☐ Fail ☐ N/A — |
| U14 | Text scaling | Layout remains usable at 1.0×, 1.3×, and 1.5× text scale | ☐ Pass ☐ Fail ☐ N/A — |
| U15 | Keyboard | Focus, scrolling, submit, and dismissal work without the keyboard covering fields/actions | ☐ Pass ☐ Fail ☐ N/A — |
| U16 | Touch targets | Frequently used controls are comfortably tappable and do not cause accidental adjacent taps | ☐ Pass ☐ Fail ☐ N/A — |
| U17 | Back navigation | Android Back and app-bar Back return to the correct state without data loss or accidental exit | ☐ Pass ☐ Fail ☐ N/A — |
| U18 | Accessibility | Semantics/tooltips are meaningful and a screen reader can identify important controls and evidence | ☐ Pass ☐ Fail ☐ N/A — |

## 8. Page-by-page operation checklist

### P01 — Splash

**Visible content:** GuardAId shield, product name, and “School Safety Response”.  
**Data effect:** None.

| Operation ID | User action or condition | Expected interface result | Expected system result | Result/evidence |
|---|---|---|---|---|
| P01-01 | Launch in Demo mode | Splash appears briefly, then Sign In opens | Demo repository initializes; no Firebase/API call | ☐ Pass ☐ Fail — |
| P01-02 | Launch in correctly configured Live mode | Splash appears briefly, then Sign In opens | Firebase initializes with the bundled app configuration | ☐ Pass ☐ Fail — |
| P01-03 | Rotate/change size during splash | Branding remains centred with no overflow | No crash or duplicate initialization | ☐ Pass ☐ Fail — |

### P02 — Live Mode Configuration Error

**Visible content:** settings icon, “Live mode is not configured”, setup guidance, and technical error text.  
**Data effect:** None.

| Operation ID | User action or condition | Expected interface result | Expected system result | Result/evidence |
|---|---|---|---|---|
| P02-01 | Start Live mode with invalid/missing Firebase initialization | Configuration error page replaces Sign In | App remains running and reports the initialization exception | ☐ Pass ☐ Fail — |
| P02-02 | View a long technical error | Error remains readable/scrollable with no overflow | No credentials, ID tokens, private keys, or sensitive paths are exposed | ☐ Pass ☐ Fail — |
| P02-03 | Correct configuration and restart | Normal Splash → Sign In path returns | Firebase initializes successfully | ☐ Pass ☐ Fail — |

### P03 — Sign In and O01 Reset Password

**Visible content:** email, password, show/hide password, Forgot password, and Sign in. Demo mode additionally shows prefilled demo credentials and a prototype notice.

**Live integration:** Firebase email/password authentication, forced ID-token refresh, `GET /api/mobile/session`, and—only for an active user—notification permission/token registration through `PUT /api/mobile/device`.

| Operation ID | User action | Expected interface result | Expected backend/auth result | Result/evidence |
|---|---|---|---|---|
| P03-01 | Open Demo mode | Demo email/password are prefilled; prototype notice is visible | No Firebase/API request | **PASS** — automated integration run 1 + run 2 |
| P03-02 | Open Live mode | Credentials are empty; demo notice is absent | No authentication until submit | ☐ Pass ☐ Fail — |
| P03-03 | Tap Show/Hide password | Password visibility and tooltip/icon toggle correctly | No data sent | ☐ Pass ☐ Fail — |
| P03-04 | Submit using the keyboard action | Same behavior as tapping Sign in | One sign-in attempt | ☐ Pass ☐ Fail — |
| P03-05 | Submit valid active credentials | Button disables and shows “Signing in…”, then P06/P07 opens | Firebase signs in; `/session` returns active role; data refresh completes | **Demo: PASS; Live: NOT RUN** — automated integration run 1 + run 2; Live auth/backend still required |
| P03-06 | Sign in when FCM permission is allowed | User still enters app normally | Device token is stored/updated in SQLite `devices` | ☐ Pass ☐ Fail — |
| P03-07 | Sign in when FCM permission is denied | Login still succeeds; app remains usable | Device registration failure is non-blocking | ☐ Pass ☐ Fail — |
| P03-08 | Submit an incorrect email/password | Error snackbar appears and Sign In remains usable | Firebase rejects credentials; no active app session | ☐ Pass ☐ Fail — |
| P03-09 | Submit invalid email syntax | Clear validation/auth error appears | No authenticated backend access | ☐ Pass ☐ Fail — |
| P03-10 | Lose network during Firebase sign-in | Network error appears; button returns to enabled state | No partial signed-in app state | ☐ Pass ☐ Fail — |
| P03-11 | Firebase succeeds but backend is unreachable | Error appears and user remains on Sign In | Repository signs Firebase user out after backend failure | ☐ Pass ☐ Fail — |
| P03-12 | Backend returns 401 once | No duplicate navigation or crash | Client refreshes ID token and retries once | ☐ Pass ☐ Fail — |
| P03-13 | Backend still rejects refreshed token | Error appears and sign-in does not continue | Firebase session is cleared by failed sign-in workflow | ☐ Pass ☐ Fail — |
| O01-01 | Tap Forgot password | Reset Password dialog opens with current email copied in | No request yet | ☐ Pass ☐ Fail — |
| O01-02 | Edit email and tap Cancel | Dialog closes and no success message appears | No reset email sent | ☐ Pass ☐ Fail — |
| O01-03 | Enter invalid email and Send link | Error snackbar appears | Demo validates `@`; Live uses Firebase validation | ☐ Pass ☐ Fail — |
| O01-04 | Enter authorized Live email and Send link | Success snackbar says to check email | Firebase sends reset email; password is never shown by the app | ☐ Pass ☐ Fail — |
| O01-05 | Enter valid-looking unknown email | App handles Firebase's configured response without leaking unnecessary account information | No crash or credential disclosure | ☐ Pass ☐ Fail — |

### P04 — Approval Pending

**Visible content:** explanation that identity is verified but a School Administrator must assign a role, plus Sign out.

| Operation ID | User action or condition | Expected interface result | Expected system result | Result/evidence |
|---|---|---|---|---|
| P04-01 | A new Firebase identity signs in for the first time | Approval Pending appears; tabs and alerts are inaccessible | `/session` upserts an SQLite user with `pending` status and no role | ☐ Pass ☐ Fail — |
| P04-02 | Tap Sign out | Returns to Sign In | Firebase session is cleared | ☐ Pass ☐ Fail — |
| P04-03 | Try Android Back/relaunch while pending | Active content is not exposed | Backend still blocks active endpoints until assignment/activation | ☐ Pass ☐ Fail — |
| P04-04 | Administrator activates user, then user signs in again | Home Shell opens with assigned role/sensitivity | `/session` reflects active status and role | ☐ Pass ☐ Fail — |

### P05 — Access Disabled

**Visible content:** disabled-access explanation and Sign out.

| Operation ID | User action or condition | Expected interface result | Expected system result | Result/evidence |
|---|---|---|---|---|
| P05-01 | A locally disabled user signs in | Access Disabled appears; tabs and alerts are inaccessible | `/session` reports `disabled`; active endpoints remain forbidden | ☐ Pass ☐ Fail — |
| P05-02 | Tap Sign out | Returns to Sign In | Firebase session is cleared | ☐ Pass ☐ Fail — |
| P05-03 | Restore account as administrator and sign in again | Home Shell becomes available | Updated SQLite state is honored | ☐ Pass ☐ Fail — |
| P05-04 | Firebase itself reports `user-disabled` | Clear Firebase-disabled message appears on Sign In | No backend access is granted | ☐ Pass ☐ Fail — |

### P06 — Home Shell and O02 Exit Confirmation

**Visible content:** GuardAId app bar, unread badge when needed, user initials, and Alerts/Updates/Logs/Settings navigation.

| Operation ID | User action | Expected interface result | Expected system result | Result/evidence |
|---|---|---|---|---|
| P06-01 | Complete active sign-in | Alerts tab is selected by default | Alerts, notifications, roles, users, and admin settings (where applicable) are refreshed | **Demo: PASS; Live: NOT RUN** — automated integration run 1 + run 2; Live refresh still required |
| P06-02 | Tap each bottom destination | Correct tab appears and selected icon/label updates | Indexed pages retain their local search/filter state | **PASS (smoke)** — automated integration run 1 + run 2; state-retention assertion remains open |
| P06-03 | Tap the app-bar notification icon/badge | Updates tab opens | No notification is silently marked read | ☐ Pass ☐ Fail — |
| P06-04 | View app bar with long/two-part name | Up to two initials display without collision | No data change | ☐ Pass ☐ Fail — |
| P06-05 | Receive/tap FCM while app is backgrounded | App opens the matching P08 Alert Review | `alertId` from FCM data is resolved against permitted alerts | ☐ Pass ☐ Fail — |
| P06-06 | Launch app by tapping an FCM from terminated state | After sign-in/data load, matching P08 opens once | Initial FCM message is consumed without duplicate navigation | ☐ Pass ☐ Fail — |
| P06-07 | Open FCM for missing/not-permitted alert | App does not crash or expose unauthorized data | Request is ignored or backend returns 403/404 | ☐ Pass ☐ Fail — |
| P06-08 | Use narrow width or large text | Navigation shows selected label only when needed | All four destinations remain reachable | ☐ Pass ☐ Fail — |
| O02-01 | Press Android Back from Home Shell | “Leave GuardAId?” confirmation appears | App remains open until confirmed | ☐ Pass ☐ Fail — |
| O02-02 | Cancel exit | Dialog closes and current tab/state remains | No sign-out/data mutation | ☐ Pass ☐ Fail — |
| O02-03 | Confirm Exit app | App closes | No unintended response action is recorded | ☐ Pass ☐ Fail — |
| O02-04 | Press Back repeatedly while dialog is open | Only one exit dialog is shown | No stacked confirmations or crash | ☐ Pass ☐ Fail — |

### P07 — Alert Centre, O03 Advanced Filters, and O04 Date Range

**Visible content:** active-alert and monitored-camera metrics, search, four quick filters, advanced filters, and alert cards ordered newest first.

**Live integration:** `GET /api/mobile/alerts`. The backend enforces authenticated Active status and a valid Role; sensitivity does not restrict alert visibility. Search and user-selected filters are applied locally.

| Operation ID | User action | Expected interface result | Expected data/result | Result/evidence |
|---|---|---|---|---|
| P07-01 | Open Alert Centre as an Active role-bearing user | All alerts appear newest first, including alerts below the user's sensitivity | Backend access is shared across valid roles; sensitivity affects notification targeting only | ☐ Pass ☐ Fail — |
| P07-02 | Inspect Active alerts metric | Count matches active incidents in the loaded list | The count reflects the shared feed and does not imply a sensitivity visibility boundary | ☐ Pass ☐ Fail — |
| P07-03 | Inspect Monitored cameras metric | Count matches unique camera names in the loaded permitted list | No unrelated camera inventory is implied | ☐ Pass ☐ Fail — |
| P07-04 | Pull to refresh | Refresh indicator appears and list/metrics update; search and filters remain selected | Session, alerts and notifications reload; failures retain existing content and show a retry message | ☐ Pass ☐ Fail — |
| P07-05 | Inspect refresh controls and wait for automatic refresh | No explicit Refresh button; foreground Alert Centre refreshes every 5 seconds without resetting browsing position | Requests do not overlap; polling pauses outside the active page and in the background | ☐ Pass ☐ Fail — |
| P07-06 | Search by camera name | Matching cards remain, case-insensitively | Local filter only | **PASS** — automated integration run 1 + run 2 |
| P07-07 | Search by location | Matching cards remain | Local filter only | ☐ Pass ☐ Fail — |
| P07-08 | Search by incident type | Matching Weapon/Violence/Bullying cards remain | Local filter only | ☐ Pass ☐ Fail — |
| P07-09 | Enter unmatched search text | “No matching alerts” recovery state appears | Loaded data is unchanged | ☐ Pass ☐ Fail — |
| P07-10 | Select All | All loaded alerts meeting advanced/search filters appear | Filter state updates immediately | ☐ Pass ☐ Fail — |
| P07-11 | Select Weapon | Only Weapon alerts appear | Other types hidden locally | **PASS** — automated integration run 1 + run 2 |
| P07-12 | Select Violence / Bullying | Violence and Physical-contact bullying appear; Weapon does not | Correct combined filter | ☐ Pass ☐ Fail — |
| P07-13 | Select Active | Awaiting confirmation, Confirmed · awaiting handler, and In progress appear | Resolved and Dismissed records are excluded from this filter; no server-side sensitivity restriction is applied | ☐ Pass ☐ Fail — |
| P07-14 | Combine search, quick filter, and advanced filters | Results satisfy every active condition | No filter is silently ignored | ☐ Pass ☐ Fail — |
| P07-15 | Tap an alert card | P08 opens for the correct alert ID | No status change merely from opening | **PASS** — automated integration run 1 + run 2 |
| P07-16 | Inspect alert card | Type, camera, status, confidence, threshold, optional borderline badge, location, time, and summary are correct | Values match API/SQLite record | ☐ Pass ☐ Fail — |
| O03-01 | Tap More filters | Advanced filter sheet opens with current selections retained | No filter applied until Apply filters | ☐ Pass ☐ Fail — |
| O03-02 | Select Incident status | Selected status is visible | No backend mutation | ☐ Pass ☐ Fail — |
| O03-03 | Select Threshold mode | Selected sensitivity is visible | No backend mutation | ☐ Pass ☐ Fail — |
| O03-04 | Select Camera source | Full camera list is usable without overflow | Camera list comes from loaded permitted alerts | ☐ Pass ☐ Fail — |
| O04-01 | Choose a valid date range | Chosen dates are shown on the filter button | Start is inclusive; end includes the full selected day | ☐ Pass ☐ Fail — |
| O04-02 | Clear date range | Date filter returns to unset | Other draft filters remain | ☐ Pass ☐ Fail — |
| O03-05 | Tap Cancel | Sheet closes; previous applied filters remain unchanged | Draft changes are discarded | ☐ Pass ☐ Fail — |
| O03-06 | Tap Apply filters | Sheet closes; results and filter-count label update | Up to four advanced criteria are applied locally | ☐ Pass ☐ Fail — |
| O03-07 | Select “All” options | Corresponding filters clear and count decreases | No stale criteria remain | ☐ Pass ☐ Fail — |

### P08 — Alert Review, Evidence, AI Summary, and Response Actions

**Visible content:** incident metadata, current workflow status, confidence, sensitivity, provisional state where applicable, evidence, Qwen3-VL scene summary/pending state, AI disclaimer, latest decision, full actor-attributed history, and the action valid for the current workflow state.

| Operation ID | User action or condition | Expected interface result | Expected backend/data result | Result/evidence |
|---|---|---|---|---|
| P08-01 | Open an active alert | Correct type, camera, location, detection time, confidence, sensitivity, and status display | Values match the selected alert | ☐ Pass ☐ Fail — |
| P08-02 | Open a provisional borderline alert | “Provisional · VLM review pending” appears | Provisional/borderline flags match backend | ☐ Pass ☐ Fail — |
| P08-03 | Alert description status is pending | Progress indicator and non-blocking pending message appear | Alert can still be acted upon before VLM finishes | ☐ Pass ☐ Fail — |
| P08-04 | Description is ready | Structured Observed, optional Key evidence, and Uncertainty fields display; no raw JSON or parser error is shown | `sceneSummary` is mapped while legacy `sceneDescription` remains available | ☐ Pass ☐ Fail — |
| P08-05 | Description is absent but not pending | “No scene description is available” appears | No invented summary | ☐ Pass ☐ Fail — |
| P08-14 | Open/in-review alert has no action records | “No decision recorded yet” appears without actor, timestamp, or invented reason | No action row is created merely by opening the alert | ☐ Pass ☐ Fail — |
| P08-15 | Open alert has a simulated emergency-call activity | Activity is shown separately from Latest decision | `simulated_emergency_call` remains auditable and does not change alert status | ☐ Pass ☐ Fail — |
| P08-16 | A response action is recorded by another user | Latest decision and history show the backend display name, action, timestamp, optional comment, and selected Broadcast Roles where relevant | Actor and action come from stored workflow events, not the signed-in client | ☐ Pass ☐ Fail — |
| P08-06 | Evidence list is empty | Evidence-unavailable card appears | No endless media spinner | ☐ Pass ☐ Fail — |
| P08-07 | Demo evidence has no URL | Demo preview and explanation appear | No network request | **PASS** — automated integration run 1 + run 2 |
| P08-08 | Open protected weapon image in Live mode | Image loads with correct aspect/fit or shows a bounded error | Bearer token is required by `/api/mobile/evidence/{id}/media` | ☐ Pass ☐ Fail — |
| P08-09 | Open protected incident video in Live mode | Video initializes and play control appears | Authorized media response has playable type/content | ☐ Pass ☐ Fail — |
| P08-10 | Tap video Play/Pause repeatedly | Playback state and icon change correctly | No duplicate player/crash | ☐ Pass ☐ Fail — |
| P08-11 | Media request fails/expires | Clear image/video error replaces spinner | Unauthorized/missing evidence is not exposed | ☐ Pass ☐ Fail — |
| P08-12 | Open Resolved/Dismissed or legacy Escalated record | Final result and complete history are readable; no new response action is offered | New actions cannot mutate terminal records; historical Escalate remains a read-only legacy event | ☐ Pass ☐ Fail — |
| P08-13 | Press Back | Returns to the originating Alerts/Updates/Logs page | Originating local filter/tab state remains | ☐ Pass ☐ Fail — |

#### O05 — Dismiss as False Alarm

| Operation ID | User action | Expected interface result | Expected backend/SQLite/FCM result | Result/evidence |
|---|---|---|---|---|
| O05-01 | Tap Dismiss as false alarm | Confirmation sheet opens with an optional comment | No change before confirmation | **PASS** — automated integration run 1 + run 2 |
| O05-02 | Leave comment empty or enter whitespace | Submission remains valid; blank text is stored as no comment | No validation error; no reason is required | ☐ Pass ☐ Fail — |
| O05-03 | Cancel/dismiss sheet | Returns to unchanged alert | No mutation | ☐ Pass ☐ Fail — |
| O05-04 | Confirm dismissal with or without a comment | Saving state appears, then success; active actions disappear and the incident moves to Logs | `POST /actions` with `dismiss_false_alarm`; state and actor/time history persist; reminder phases stop; one routine update targets prior interested recipients except the actor | ☐ Pass ☐ Fail — |
| O05-05 | Submit after another user already finalized alert | Failure is clear; stale success is not shown | Backend returns 409; no second final action | ☐ Pass ☐ Fail — |

#### O06 — Confirm Incident and Select a Handling Path

| Operation ID | User action | Expected interface result | Expected backend/SQLite/FCM result | Result/evidence |
|---|---|---|---|---|
| O06-01 | Tap Confirm incident | Confirmation flow presents exactly three mutually exclusive paths: Confirm only, I'll handle this, and Already resolved | No mutation before the user confirms a path; comment is optional | **PASS** — automated integration run 1 + run 2 |
| O06-02 | Cancel, or choose Confirm only without any Broadcast Role | Cancel leaves the alert unchanged; an empty role selection is rejected with clear guidance | No workflow event, phase, or notification is created for invalid/cancelled submission | ☐ Pass ☐ Fail — |
| O06-03 | Choose Confirm only, select at least one valid Role, and submit | Alert becomes Confirmed · awaiting handler; selected roles are shown and a handler can later take ownership | Phase A stops; Phase B is persisted from confirmation time; comment may be omitted; initial Broadcast targets selected roles regardless of their sensitivity | ☐ Pass ☐ Fail — |
| O06-04 | On separate alerts, choose I'll handle this and Already resolved | First alert becomes In progress with the current user as Handler; second becomes Resolved and moves to Logs | Both paths persist the correct actor/action/time; handling or resolution stops reminders; no Escalate action or real dialler is invoked | ☐ Pass ☐ Fail — |

#### O07 — Broadcast Targeting and Phase B Reminders (replaces new Escalate action)

| Operation ID | User action | Expected interface result | Expected backend/SQLite/FCM result | Result/evidence |
|---|---|---|---|---|
| O07-01 | Open the Confirm-only Role selector | Sensitivity-matched Roles are selected by default for this alert | Default selection is a convenience, not an extra server-side sensitivity filter | ☐ Pass ☐ Fail — |
| O07-02 | Change the selected Broadcast Roles | Role selection updates; a Role outside the sensitivity defaults can remain selected | Role IDs are validated by the backend; Broadcast targets selected Roles as explicitly chosen | ☐ Pass ☐ Fail — |
| O07-03 | Continue without selecting any Role | User is told at least one Broadcast Role is required | API rejects empty selection; no Confirm-only event or reminder phase is created | ☐ Pass ☐ Fail — |
| O07-04 | Select a valid Role with no currently eligible recipients | A clear no-recipient warning appears and requires an explicit continue/cancel choice | Continue does not claim a push was delivered; cancel creates no action; audience is rechecked at dispatch time | ☐ Pass ☐ Fail — |
| O07-05 | Confirm-only submission and initial Broadcast | Alert shows Confirmed · awaiting handler; selected Role names and current state are visible | One initial Phase B dispatch is queued to eligible users in selected Roles; a user belonging to multiple selected Roles receives one logical event per dispatch | ☐ Pass ☐ Fail — |
| O07-06 | Phase B first reminder becomes due | A three-beep urgent reminder is sent to currently eligible users in the selected Roles, without another sensitivity filter | Dispatch is unique and persisted; reading, comments, or simulated call do not reset the phase; taking ownership stops it | ☐ Pass ☐ Fail — |
| O07-07 | Phase B second cumulative deadline becomes due | A four-beep notification is sent to all currently eligible Active role-bearing users | It is one bounded all-user stage; it does not repeat indefinitely and is not gated by whether the first repeat was delivered | ☐ Pass ☐ Fail — |
| O07-08 | A selected Role has no active recipient/device, or delivery fails | UI/history distinguishes saved Broadcast intent from actual provider delivery | No false delivery claim; failure is recorded; stale/ineligible users/devices are excluded when dispatching | ☐ Pass ☐ Fail — |

#### O08/O09 — Emergency Call Simulation

| Operation ID | User action | Expected interface result | Expected backend/SQLite result | Result/evidence |
|---|---|---|---|---|
| O08-01 | Choose the optional Simulate emergency call control during Confirm | Explicit in-app warning says no dialler/service will be contacted | Simulation is separate from Confirm, ownership, resolution, and reminder state | **PASS** — automated integration run 1 + run 2 |
| O08-02 | Cancel | Dialog closes; no result dialog | No action/audit row | **PASS** — automated integration run 1 + run 2 |
| O08-03 | Confirm Run simulation | Saving state then “Simulated emergency call placed. No real call was made.” appears inside GuardAId | Backend records only the simulation event; no dialler, external app, or real call; reminder anchor/state is unchanged | ☐ Pass ☐ Fail — |
| O09-01 | Verify device behavior | Result says “Simulated emergency call has been placed. No real call was made.” | `diallerOpened=false`, `realCallMade=false`; Android dialler never opens | ☐ Pass ☐ Fail — |
| O09-02 | Backend does not confirm both safety flags | “Simulation failed” appears | Client rejects unsafe/ambiguous response | ☐ Pass ☐ Fail — |
| O09-03 | Tap Done | Result closes and returns to alert | No final status change from simulation alone | ☐ Pass ☐ Fail — |

#### O09A — Initial, Repeat, and Collaboration Notification Verification

| Operation ID | User action or condition | Expected interface result | Expected backend/FCM result | Result/evidence |
|---|---|---|---|---|
| O09A-01 | Receive an initial sensitivity-targeted alert or initial Broadcast | Initial alert/Broadcast uses the two-beep urgent channel | Phase A follows sensitivity; initial Phase B Broadcast follows selected Roles without an extra sensitivity filter | ☐ Pass ☐ Fail — |
| O09A-02 | Receive a first reminder | First repeat uses the three-beep channel | Phase A repeats to its current sensitivity audience; Phase B repeats to selected Roles; no duplicate device dispatch | ☐ Pass ☐ Fail — |
| O09A-03 | Receive the second reminder or tap an authorised notification | Second reminder uses the four-beep all-user channel; tapping any alert notification opens the refreshed matching Alert Review | Stage two reaches all currently eligible Active role-bearing users; payload uses routing data only and exposes no evidence path, token, credential, or full comment | ☐ Pass ☐ Fail — |
| O09A-04 | Another user dismisses, confirms, takes ownership, or resolves an alert | One clear collaboration update names who did what; the actor does not receive their own update | Recipient cohort is the deduplicated prior interested users, revalidated for current access; routine updates use `guardaid_updates_v1`, not urgent sound | ☐ Pass ☐ Fail — |

### P09 — Updates and O10 Mark All as Read

**Visible content:** unread count, Mark all as read, and notification cards ordered newest first.

| Operation ID | User action or condition | Expected interface result | Expected backend/SQLite result | Result/evidence |
|---|---|---|---|---|
| P09-01 | Open Updates | Cards are newest first; unread cards are visually differentiated and identify the actor/action | `GET /api/mobile/notifications` returns collaboration updates only to the deduplicated prior interested audience, excluding the actor and revalidating current access | ☐ Pass ☐ Fail — |
| P09-02 | Compare badges and subtitle | App-bar/bottom badge and page unread count agree | Counts equal unread visible records | ☐ Pass ☐ Fail — |
| P09-03 | Tap notification with valid alert | Correct P08 Alert Review opens | Active role-bearing users can access the incident regardless of sensitivity; backend still enforces identity/status/Role access | ☐ Pass ☐ Fail — |
| P09-04 | Tap notification whose alert is unavailable | Error snackbar appears; app remains on Updates | No unauthorized/missing alert opens | ☐ Pass ☐ Fail — |
| P09-05 | Open a notification card | Card is not individually marked read | This matches current implementation; do not expect individual read mutation | ☐ Pass ☐ Fail — |
| P09-06 | No notifications exist | “No updates yet” state appears | No crash | ☐ Pass ☐ Fail — |
| O10-01 | Tap Mark all as read with unread items | Confirmation dialog opens | No change before confirmation | **Demo: PASS; Live: NOT RUN** — automated integration run 1 + run 2; Live read-state persistence still required |
| O10-02 | Cancel | Unread markers/count remain | No `notification_reads` mutation | ☐ Pass ☐ Fail — |
| O10-03 | Confirm Mark all | Success snackbar; markers disappear; counts become zero; action disables | Read rows persist for every currently visible unread notification | ☐ Pass ☐ Fail — |
| O10-04 | Restart/re-login | Read state remains | SQLite read state persists | ☐ Pass ☐ Fail — |

### P10 — Incident Logs

**Visible content:** Resolved and Dismissed incident history, search/status filters, final decision, handler/completion details, and full action history. Legacy Escalated records remain readable as legacy history only.

| Operation ID | User action | Expected interface result | Expected data/result | Result/evidence |
|---|---|---|---|---|
| P10-01 | Open Logs | Resolved and Dismissed alerts appear newest first; historic Escalated records remain readable | Active incidents are excluded from terminal history; no new Escalated state is created, and the detail view identifies old entries as legacy | ☐ Pass ☐ Fail — |
| P10-02 | Inspect status metrics | Counts match loaded Resolved, Dismissed, and legacy Escalated records | Counts reflect the shared incident history, not the user's sensitivity | ☐ Pass ☐ Fail — |
| P10-03 | Search by camera/location/type | Matching saved records remain | Case-insensitive local search | ☐ Pass ☐ Fail — |
| P10-04 | Filter All saved statuses | Resolved and Dismissed plus any legacy Escalated records appear | No active incidents are included | ☐ Pass ☐ Fail — |
| P10-05 | Filter Resolved | Only Resolved records appear | Correct status condition | ☐ Pass ☐ Fail — |
| P10-06 | Filter Dismissed | Only Dismissed records appear | Correct status condition | ☐ Pass ☐ Fail — |
| P10-07 | Filter Escalated and open a result | Only historical Escalated records appear; their Alert Review identifies the legacy workflow status | The new workflow cannot create this state | ☐ Pass ☐ Fail — |
| P10-08 | Produce no matching results | Empty-state message appears | No data is deleted | ☐ Pass ☐ Fail — |
| P10-09 | Tap card or View saved record | P08 opens with evidence/summary/action note and no response buttons | Correct final record is loaded | ☐ Pass ☐ Fail — |

### P11 — Settings and O11 Sign Out

**Visible content:** identity, email, assigned role and inherited sensitivity, admin links where authorized, and Sign out. Non-functional Application tiles are intentionally absent.

| Operation ID | User action or condition | Expected interface result | Expected system result | Result/evidence |
|---|---|---|---|---|
| P11-01 | Open as active non-admin | Identity/role/sensitivity show; Administration section is absent | No admin data is exposed by UI | ☐ Pass ☐ Fail — |
| P11-02 | Attempt admin API directly as non-admin | App/API shows no protected content | Backend returns 403 | ☐ Pass ☐ Fail — |
| P11-03 | Open as School Administrator | All three admin navigation cards appear | Admin flag comes from backend session | **Demo: PASS; Live: NOT RUN** — automated integration run 1 + run 2; Live admin authorization still required |
| P11-04 | Inspect the Settings page for removed Application tiles | Evidence access, Prototype View/build, and an empty Application section are absent | The UI does not imply a separate evidence authorization or prototype-information feature | ☐ Pass ☐ Fail — |
| P11-05 | Open authorised Alert Review evidence | Evidence remains reachable from the relevant Alert Review flow | Evidence access continues to use the protected API/session path; no Settings shortcut is required | ☐ Pass ☐ Fail — |
| O11-01 | Tap Sign out | Sign-out confirmation appears | Session remains until confirmed | ☐ Pass ☐ Fail — |
| O11-02 | Cancel sign-out | Settings remains visible | Firebase session remains | ☐ Pass ☐ Fail — |
| O11-03 | Confirm sign-out | Sign In appears and prior app data is cleared from controller | Firebase signs out; alerts/notifications are removed from active UI state | ☐ Pass ☐ Fail — |
| O11-04 | Sign in as a different user | Only the second user's permitted alerts/settings appear | No first-user data/role leakage | ☐ Pass ☐ Fail — |

### P12 — Roles and Sensitivity, O12 Edit Threshold, and O13 Confirmation

**Visible content:** fixed role cards for School Administrator, Security Personnel, and Teacher; descriptions, user counts, current sensitivity, and Edit threshold.

| Operation ID | User action | Expected interface result | Expected backend/SQLite result | Result/evidence |
|---|---|---|---|---|
| P12-01 | Open as administrator | All fixed roles and current data display | `GET /api/mobile/admin/roles` succeeds | ☐ Pass ☐ Fail — |
| P12-02 | Verify non-admin cannot reach page/API | No Settings link; direct API attempt returns 403 | No role data mutation | ☐ Pass ☐ Fail — |
| O12-01 | Tap Edit threshold | Sheet describes Sensitive, Moderate, and Certain; current value selected | No mutation yet | ☐ Pass ☐ Fail — |
| O12-02 | Cancel sheet | Role remains unchanged | No API/audit entry | ☐ Pass ☐ Fail — |
| O12-03 | Choose the same value and save | Sheet closes without redundant confirmation | No PATCH/audit change | ☐ Pass ☐ Fail — |
| O12-04 | Choose a different value | Confirmation shows old → new and affected user count | No mutation until confirmed | ☐ Pass ☐ Fail — |
| O13-01 | Cancel confirmation | Original sensitivity remains | No PATCH | ☐ Pass ☐ Fail — |
| O13-02 | Confirm update | Success snackbar; role badge and assigned-user sensitivity refresh | `PATCH /api/mobile/admin/roles/{id}` updates SQLite role and audit log | ☐ Pass ☐ Fail — |
| O13-03 | Refresh the signed-in session after the role sensitivity changes | New inherited sensitivity appears; the user can still see alerts at every sensitivity | Backend refreshes current user/role data; sensitivity updates notification eligibility, not visibility | ☐ Pass ☐ Fail — |
| P12-03 | Create a custom Role, then request deletion of that Role | Create form validates and saves; deletion requires confirmation and warns about affected users; built-in roles remain protected | Custom Role lifecycle and safeguards are enforced by the backend; deletion effects are auditable | **PASS (automated UI/API tests)** — widget suite covers create/delete confirmation; backend tests verify assigned-user handling and system-role protection |

### P13 — User Assignments, O14 Edit User, and O15 Confirmation

**Visible content:** explanation of pending Firebase identities, staff search, user cards, role, and local access status.

| Operation ID | User action | Expected interface result | Expected backend/SQLite result | Result/evidence |
|---|---|---|---|---|
| P13-01 | Open as administrator | Users load with Pending first, then by display name/email | `GET /api/mobile/admin/users` succeeds | ☐ Pass ☐ Fail — |
| P13-02 | Search by name | Matching users remain | Local filtering only | ☐ Pass ☐ Fail — |
| P13-03 | Search by email | Matching users remain | Local filtering only | ☐ Pass ☐ Fail — |
| P13-04 | Search by role | Matching assigned users remain | Local filtering only | ☐ Pass ☐ Fail — |
| P13-05 | Enter no-match query | Page remains stable with no incorrect cards | No user mutation | ☐ Pass ☐ Fail — |
| O14-01 | Tap a user | Edit sheet shows correct identity, role, and access status | No mutation yet | ☐ Pass ☐ Fail — |
| O14-02 | Select No role assigned | Review changes remains disabled | Current mobile workflow requires a role for save | ☐ Pass ☐ Fail — |
| O14-03 | Select role and Pending | Review confirmation summarizes role/sensitivity/status | No mutation before confirmation | ☐ Pass ☐ Fail — |
| O14-04 | Select role and Active | Confirmation summarizes intended access | No mutation before confirmation | ☐ Pass ☐ Fail — |
| O14-05 | Select role and Disabled | Confirmation clearly identifies Disabled access | No mutation before confirmation | ☐ Pass ☐ Fail — |
| O14-06 | Cancel sheet | User card remains unchanged | No PATCH/audit entry | ☐ Pass ☐ Fail — |
| O15-01 | Cancel confirmation | Existing assignment remains | No mutation | ☐ Pass ☐ Fail — |
| O15-02 | Confirm assignment | Success snackbar; card/role counts refresh | `PATCH /api/mobile/admin/users/{id}` replaces primary role, updates status, and writes audit log | ☐ Pass ☐ Fail — |
| O15-03 | Activate new Pending user | User can sign in and sees Home Shell under assigned sensitivity | Status/role persisted | ☐ Pass ☐ Fail — |
| O15-04 | Disable an active non-admin user | Next session is blocked by P05; active APIs return 403 | Status persists as Disabled | ☐ Pass ☐ Fail — |
| O15-05 | Try to disable own administrator account | Failure is shown and current admin remains active | Backend returns 409; no mutation | ☐ Pass ☐ Fail — |
| O15-06 | Try to remove own administrator role | Failure is shown and role remains admin | Backend returns 409; no mutation | ☐ Pass ☐ Fail — |
| P13-06 | Verify non-admin cannot reach page/API | No Settings link; direct API returns 403 | No user data exposure/mutation | ☐ Pass ☐ Fail — |

### P14 — Borderline Alert Settings and O16 Confirmation

**Visible content:** enable/disable switch, eligible recipient role checkboxes, and Save settings.

| Operation ID | User action | Expected interface result | Expected backend/SQLite/FCM policy result | Result/evidence |
|---|---|---|---|---|
| P14-01 | Open as administrator | Saved enabled state and recipients display | `GET /api/mobile/admin/borderline-settings` succeeds | ☐ Pass ☐ Fail — |
| P14-02 | Toggle Enabled off | Recipient controls disable visually | No persistence before Save | ☐ Pass ☐ Fail — |
| P14-03 | Toggle Enabled on | Recipient controls enable | No persistence before Save | ☐ Pass ☐ Fail — |
| P14-04 | Select/deselect roles | Checkbox states change correctly | No persistence before Save | ☐ Pass ☐ Fail — |
| P14-05 | Save enabled with no recipients | Error requests at least one role | No PUT/mutation | ☐ Pass ☐ Fail — |
| O16-01 | Save enabled with recipient(s) | Confirmation warns about recall/alert fatigue | No mutation before confirmation | ☐ Pass ☐ Fail — |
| O16-02 | Save disabled | Confirmation explains promoted alerts will stop | No mutation before confirmation | ☐ Pass ☐ Fail — |
| O16-03 | Cancel confirmation | Page keeps draft state but saved backend state is unchanged | No PUT/audit entry | ☐ Pass ☐ Fail — |
| O16-04 | Confirm save | Saving state then success snackbar | `PUT /api/mobile/admin/borderline-settings` updates global SQLite settings and audit log | ☐ Pass ☐ Fail — |
| O16-05 | Reopen page/restart app | Saved settings reappear | Persistence verified | ☐ Pass ☐ Fail — |
| P14-06 | Generate promoted borderline alert while enabled | Only selected roles that also satisfy sensitivity receive it | FCM/device selection applies borderline recipient policy | ☐ Pass ☐ Fail — |
| P14-07 | Generate promoted borderline alert while disabled | No role receives promoted-borderline notification | Detector's ordinary non-borderline alert behavior remains separate | ☐ Pass ☐ Fail — |
| P14-08 | Verify non-admin cannot reach page/API | No Settings link; direct API returns 403 | No settings exposure/mutation | ☐ Pass ☐ Fail — |

## 9. Cross-page and end-to-end workflows

These checks validate that pages are not only correct in isolation.

| Flow ID | Workflow | Expected end-to-end result | Result/evidence |
|---|---|---|---|
| E2E-01 | Firebase user first sign-in → Pending → admin assignment → active refresh | Same identity is linked to one SQLite user; access changes only after admin assignment and refresh | ☐ Pass ☐ Fail — |
| E2E-02 | Active sign-in → FCM permission → device registration | Login succeeds and valid token is stored without exposing it in UI/log evidence | ☐ Pass ☐ Fail — |
| E2E-03 | Detector creates urgent alert → backend persists evidence/notification → FCM → tap notification | Correct alert opens with protected evidence; all Active role-bearing users can review it even if they were not in the initial notification audience | ☐ Pass ☐ Fail — |
| E2E-04 | Open alert → Confirm and handle → resolve → Updates → Logs → reopen | One handler is recorded, optional comments may be blank, updates identify the actor, and resolved history is read-only | ☐ Pass ☐ Fail — |
| E2E-05 | Open alert → dismiss as false alarm → refresh/restart → Logs | Dismissal and optional comment persist; reminders stop; the incident leaves the Active filter | ☐ Pass ☐ Fail — |
| E2E-06 | Confirm only → Broadcast selected Roles → first repeat → all-user stage | Phase A stops; Phase B targets selected Roles regardless of sensitivity, then all eligible Active role-bearing users; dispatches are bounded and deduplicated | ☐ Pass ☐ Fail — |
| E2E-07 | Use optional in-app emergency-call simulation during confirmation | Confirmation/result are correct; only a simulation event is recorded; no dialler or real call occurs and reminders are unchanged | ☐ Pass ☐ Fail — |
| E2E-08 | Change role sensitivity → inspect alert visibility and notification eligibility | User continues to see all alerts; new initial sensitivity-targeted notifications follow the updated role sensitivity | ☐ Pass ☐ Fail — |
| E2E-09 | Configure borderline recipients → create borderline alert | Enabled/disabled state, selected eligible roles, sensitivity policy, notification history, and FCM delivery agree; alert visibility remains shared | ☐ Pass ☐ Fail — |
| E2E-10 | Mark all updates read → restart/re-login | Unread count remains zero for previously read visible notifications | ☐ Pass ☐ Fail — |
| E2E-11 | ID token expires during active use | Client refreshes once and continues, or shows a recoverable session error | ☐ Pass ☐ Fail — |
| E2E-12 | Backend becomes unavailable mid-session | Current page does not crash; saving controls recover; no false success or partial duplicate action | ☐ Pass ☐ Fail — |
| E2E-13 | Sign out user A → sign in user B | No role, alert, notification, search, or evidence leakage from A to B | ☐ Pass ☐ Fail — |

## 10. Backend and security verification points

For Live-mode checks, verify both the UI and the authoritative backend result.

| ID | Verification | Acceptance criterion | Result/evidence |
|---|---|---|---|
| B01 | Firebase ID token | Every mobile API/evidence request requires `Authorization: Bearer <ID token>` | ☐ Pass ☐ Fail — |
| B02 | Token retry | A 401 triggers at most one forced-refresh retry | ☐ Pass ☐ Fail — |
| B03 | Active access | Pending/Disabled/no-role users cannot access alerts, evidence, notifications, or device registration | ☐ Pass ☐ Fail — |
| B04 | Admin access | Admin endpoints reject active non-admin users with 403 | ☐ Pass ☐ Fail — |
| B05 | Shared visibility and sensitivity notifications | Every authenticated Active role-bearing user can list/open all alerts and evidence; sensitivity only changes notification recipients | ☐ Pass ☐ Fail — |
| B06 | Evidence | Evidence is served only through protected endpoint; local filesystem path is not returned to app | ☐ Pass ☐ Fail — |
| B07 | Workflow integrity | Backend enforces Dismiss, the three exclusive Confirm paths, Take ownership, and handler-only Resolve; stale/duplicate submissions cannot create conflicting state | ☐ Pass ☐ Fail — |
| B08 | Auditability | Role, user, borderline, alert-action, and simulation changes create audit records | ☐ Pass ☐ Fail — |
| B09 | FCM targeting and reminder phases | Phase A uses sensitivity for initial/first repeat; Phase B uses selected Roles without extra sensitivity filtering; stage two reaches all eligible Active role-bearing users; current eligibility and enabled devices are rechecked | ☐ Pass ☐ Fail — |
| B10 | FCM failure | Failed delivery is recorded but does not roll back the authoritative alert action | ☐ Pass ☐ Fail — |
| B11 | Notification read isolation | One user's read state does not mark another user's notifications read | ☐ Pass ☐ Fail — |
| B12 | Secrets/privacy | Screenshots, videos, reports, logs, and commits contain no password, ID token, FCM token, service-account key, private IP, or private local path | ☐ Pass ☐ Fail — |

## 11. Recommended device and state coverage

Run the applicable page checks against this minimum matrix:

| Dimension | Required coverage |
|---|---|
| Device/layout | Realme GT Neo 2 profile; 320×640 narrow profile; one common 360/390-width Android profile |
| Text scale | 1.0×, 1.3×, 1.5× |
| Orientation | Portrait required; landscape checked for graceful usability where Android permits it |
| Mode | Demo for rapid UI review; Live for all integration/security claims |
| User | Pending, Disabled, active Teacher, active Security Personnel, active School Administrator |
| Sensitivity | Sensitive, Moderate, Certain; verify viewing all sensitivities separately from each notification audience |
| Data | Loading, populated, long text, empty, filtered-empty, network failure, 401, 403, 404, 409, 422, media failure |
| Alert | Weapon, Violence, Physical-contact bullying; Awaiting confirmation, Confirmed awaiting handler, In progress, Resolved, Dismissed, plus read-only legacy Escalated; provisional and non-provisional |

## 12. Per-page review record template

Copy this block once for each Page ID/Overlay ID during the review.

```text
Page/Overlay ID:
Tester:
Date/time:
App mode/build:
Environment: EMU / CROSS-EMU / CROSS-PHONE / PHONE
Device/AVD name, Android version, resolution, density, and text scale:
User role/status/sensitivity:
Test data or alert ID:

Visual verdict (1–5):
Understandability (1–5):
Ease of use (1–5):
Responsiveness/feedback (1–5):
Functional verdict: PASS / FAIL / BLOCKED
Integration verdict: PASS / FAIL / NOT RUN
Required environment verdict satisfied: YES / NO

Passed operation IDs:
Failed operation IDs:
Observed result:
Expected result:
Screenshot/video/log/API/SQLite evidence:
Defect ID and severity:
Notes/recommendation:
Retest result:
```

## 13. Strict but reasonable acceptance standard

### 13.1 Individual test-case result definitions

| Result | Definition |
|---|---|
| **PASS** | The exact expected UI result and, where applicable, authoritative API/SQLite/FCM result occur; no unexpected error, overflow, data leak, duplicate action, stale state, or misleading success appears |
| **FAIL** | Any expected result is absent/incorrect, the wrong data changes, an unauthorized action succeeds, the UI crashes/freezes/overflows, or the result cannot be reproduced |
| **BLOCKED** | The test cannot execute because a documented prerequisite/environment is unavailable; Blocked is not Pass |
| **NOT RUN** | No execution evidence exists; Not Run is not Pass |
| **N/A** | The item demonstrably does not apply to the tested role/state/device, with a written reason |

A test case passes only when:

1. the user can complete the operation without undocumented assistance;
2. the visible result matches the checklist and does not merely look successful;
3. the authoritative backend result matches the UI for every Live mutation;
4. cancel/invalid-input paths create no unintended side effect;
5. the operation passes twice consecutively in the required environment without restarting unless restart is part of the test;
6. screenshots and/or recordings identify the build, role/state, device, and result; Live mutations also include API, SQLite, audit, or FCM evidence as applicable.

### 13.2 Functional, integration, and security gates

All of the following are mandatory; no percentage-based exception is allowed:

- **100% pass** for authentication, authorization, shared alert/evidence access, sensitivity-based notification selection, workflow-state integrity, SQLite persistence, audit logging, Broadcast/reminder targeting, account isolation, and emergency-call safety.
- Pending, Disabled, unauthenticated, or roleless users must not access protected alerts/evidence; an Active role-bearing user must be able to view alerts regardless of sensitivity.
- Every accepted Dismiss, Confirm path, Take ownership, or Resolve action must produce exactly one correct workflow state and auditable event; retry/race conditions must not create duplicate or conflicting decisions.
- Every cancelled or invalid operation must produce zero authoritative mutation.
- Emergency simulation must open the real dialler **zero times** and place **zero real calls** across all runs.
- No password, Firebase ID token, FCM token, service-account key, private IP, local filesystem path, or private evidence content may appear in committed test artifacts or defect reports.
- There must be no crash, unhandled exception, indefinite loading state, black screen, or data corruption in any mandatory workflow.

### 13.3 UI/UX and accessibility gates

- U01–U18 must be recorded for every relevant page/overlay in the required environments.
- There must be **zero Flutter overflow warnings**, clipped primary actions, overlapping controls, or unreachable essential content at 320×640, the standard Emulator profiles, and the Realme GT Neo 2.
- At 1.5× text scale, all workflows must remain completable. Non-essential secondary text may use a clearly visible ellipsis, but identity, safety status, form errors, and primary actions must remain understandable.
- Normal text should meet WCAG AA contrast of at least 4.5:1; large text and essential graphical controls should meet at least 3:1.
- Frequently used interactive controls should provide an effective target of approximately 48×48 logical pixels and visible pressed/selected/disabled feedback.
- Every page must score at least **4/5** for understandability and ease of use in the final review. Visual-quality scores below 4/5 require either correction or a documented Low-severity rationale accepted for the prototype.
- No Critical or High UI/usability defect may remain open. A Medium/Low limitation may remain only if it does not prevent, misdirect, or materially delay a safety workflow and is explicitly documented.

### 13.4 Responsiveness and reliability targets

Measure under a documented stable local Wi-Fi/LAN condition and identify obvious external-service delays separately.

| Interaction | Acceptance target |
|---|---|
| Tap/selection/local filter feedback | Visible response within 300 ms |
| Search/filter update on the loaded mobile dataset | Results update within 500 ms without dropped input |
| Sign-in or manual data refresh | Completes within 5 s under normal connectivity, otherwise shows continuing progress or a useful recoverable error |
| Dismiss/Confirm/Broadcast/Take ownership/Resolve/settings save | Loading feedback begins immediately; normal backend completion within 3 s on LAN |
| Evidence image | First usable image or explicit error within 5 s |
| Evidence video | Playable control/frame or explicit error within 8 s; no endless spinner |
| FCM controlled test | At least 95% arrive within 10 s and 100% within 30 s while online, over at least 20 documented sends; any miss must have delivery/error evidence and investigation |
| App stability | No crash/freeze during 30 minutes of mixed navigation, filtering, media, refresh, and role-appropriate actions |

Timing outside these targets is a Fail unless the delay is proven to be an external dependency and the interface still gives accurate, recoverable feedback. Such an exception must be documented; it cannot hide a local performance defect.

### 13.5 Overall release/development acceptance

The current development/evaluation build may be declared accepted only when:

- 100% of applicable Critical/High, security, data-integrity, alert-response, FCM-targeting, evidence-access, and emergency-safety cases pass;
- at least 95% of all other applicable executed cases pass;
- any remaining failure is Low severity, documented with impact/workaround, and does not block a core user journey;
- every **B — Both** item has separate Demo and Live evidence;
- every **PHONE** item and the phone portion of every **CROSS** item pass on the Realme GT Neo 2;
- all earlier defects marked fixed have a successful retest and relevant regression evidence.

An overall percentage must never override a failed safety, security, authorization, privacy, evidence, final-action, or emergency-call case.

## 14. Completion rule

A page is complete only when:

1. all applicable operation IDs have a recorded result;
2. U01–U18 have been reviewed at the required sizes/text scales;
3. failure, loading, empty, and success states have been observed where applicable;
4. Live-mode operations have matching API and SQLite/FCM evidence, not only a success snackbar;
5. no Critical/High defect remains open, and any accepted lower-severity limitation is documented;
6. the emergency-call simulation has been explicitly verified never to open the dialler or place a real call.

## 15. Emulator execution record — 6 September 2026 (updated 21 September 2026)

This section records the Emulator work actually completed so far. It is evidence for the current development build, not a claim that every checklist item has passed. The strict definitions in Section 13 still apply: an item remains **NOT RUN** unless its own result and evidence are recorded.

### 15.1 Environment

| Item | Recorded value |
|---|---|
| AVD | `Realme_GT_Neo_2_API_36` |
| Android target | API 36 |
| Device ID | `emulator-5554` |
| Display | 1080 × 2400 portrait |
| Build/application ID | `com.guardaid.prototype` |
| Mode | Demo mode; no real Firebase, SQLite, protected-media, or FCM claim is made by these runs |
| Date | 6 September 2026 |

### 15.2 Automated checks

| Check | Command/result | Status |
|---|---|---|
| Flutter widget suite | `flutter test` — 5 tests passed, exit code 0 | **PASS** |
| Flutter static analysis | `flutter analyze` — `No issues found!` | **PASS** |
| Android debug build/install | `flutter test ... -d emulator-5554` built and installed the APK successfully | **PASS** |
| Emulator integration suite, run 1 | `flutter test integration_test/emulator_operations_test.dart -d emulator-5554` — 4/4 tests passed, `All tests passed!`, exit code 0 | **PASS** |
| Emulator integration suite, run 2 | Same command repeated consecutively on the same AVD/build — 4/4 tests passed, `All tests passed!`, exit code 0 | **PASS** |
| Live-mode Emulator workflow | Local API port 8020 was not listening during this run; no Live Firebase/backend assertion was attempted | **NOT RUN** |
| Latest Flutter widget suite | `C:\flutter\bin\flutter.bat test --no-pub --concurrency=1` — 16 tests passed, exit code 0 | **PASS** |
| Latest Dart analysis | `C:\flutter\bin\cache\dart-sdk\bin\dart.exe analyze .` — `No issues found!` | **PASS** |
| Latest Emulator integration suite | `flutter test integration_test/emulator_operations_test.dart -d emulator-5554 --no-pub` — rerun 21 September 2026, 4 tests passed, exit code 0 | **PASS** |
| Live release startup smoke | `start_live_phone.ps1 -DeviceId emulator-5554 -BuildMode release` — backend healthy, release APK installed, notification permission handled, Live sign-in displayed; no account/API/FCM assertion | **PASS (startup only)** |

The repeatable integration workflow is implemented in [`integration_test/emulator_operations_test.dart`](integration_test/emulator_operations_test.dart). It covers startup and Demo sign-in, password-reset dialog, Home Shell navigation (including initials and a 320×640 surface), quick filter/search, advanced-filter sheet, Alert Review evidence/summary, invalid response validation, emergency-call safety warning, Updates/Mark-all confirmation, Logs, Settings, role threshold sheet, Pending/Active/Disabled user-assignment drafts, and Borderline settings.

### 15.3 Manual Emulator smoke observations

The following were opened and observed on the same AVD in Demo mode. “Smoke pass” means the visible route/control opened and behaved as expected once; it is not yet a final per-ID acceptance because the evidence/twice-consecutively rule has not been completed for every row.

| Area | Observed result | Status/evidence |
|---|---|---|
| Splash → Sign In | English splash and sign-in page displayed; Demo credentials were available | **PASS (smoke)** — `../chapter4_assets_final/emu_splash_full.png`, `../chapter4_assets_final/emu_login_after_restore_full.png` |
| Forgot password / password visibility / invalid sign-in | Reset dialog, show/hide password control, and incorrect-credential message were reachable | **PASS (smoke)** — `../chapter4_assets_final/emu_invalid_login_full.png` |
| Home Shell | Alert Centre, Updates, Logs, Settings tabs and alert metrics rendered | **PASS (smoke)** — `../chapter4_assets_final/emu_home_after_demo_login_full.png` |
| Alert Centre | Weapon quick filter, camera search, More filters, status/threshold/camera controls, and date-range picker were opened | **PASS (smoke)** |
| Alert Review | Demo evidence preview, AI scene summary, dismiss/resolve/escalate sheets, empty-reason validation, and emergency-call warning were observed | **PASS (smoke)**; no real dialler was opened |
| Updates | Mark-all-as-read confirmation and resulting zero-unread state were observed | **PASS (smoke)** — `../chapter4_assets_final/emu_updates_mark_all_read_full.png` |
| Incident Logs | Metrics, saved records, status filtering/search surface, and record opening were observed | **PASS (smoke)** |
| Settings / sign-out | Admin links, sign-out confirmation, and return to Sign In were observed | **PASS (smoke)** |
| Roles and Sensitivity | Fixed-role explanation, threshold edit sheet, and cancel path were observed | **PASS (smoke)** |
| User Assignments | Pending user card, edit sheet, role selector, and cancel path were observed | **PASS (smoke)** — `../chapter4_assets_final/emu_user_assignment_sheet_full.png` |
| Borderline Alert Settings | Enable/disable toggle, eligible-role controls, save confirmation, and cancel path were observed | **PASS (smoke)** |

### 15.6 Latest structured-summary and safety smoke pass — 21 September 2026

The following additional checks were performed on the same `emulator-5554` Demo session after the structured-summary and notification-tap changes. The screenshots are local development evidence only; they do not close Live/FCM requirements.

| Check | Observed result | Status/evidence |
|---|---|---|
| Home alert cards | Confidence, sensitivity and status use separate aligned metric cards; compact summary shows `Observed` and an `Uncertainty` badge; raw JSON is absent | **PASS (smoke)** — `tmp_guardaid_emulator_resolved_home.png` |
| Alert Review structured summary | `Observed`, short `Key evidence` bullets, `Uncertainty` and the AI verification disclaimer render as readable sections; no braces, escaped JSON or parser-error text | **PASS (smoke)** — `tmp_guardaid_emulator_decision.png` |
| Pending/unavailable summary | Pending text is shown without blocking evidence access or response actions; malformed data uses `AI scene summary is unavailable. Review the video evidence.` | **PASS (smoke + widget assertion)** — `tmp_guardaid_emulator_pending_summary.png` |
| No-decision state | Alert Review displays `No decision recorded yet` rather than inventing an actor or timestamp | **PASS (smoke)** — `tmp_guardaid_emulator_detail.png` |
| Simulated emergency call | Confirmation and completion dialogs explicitly state that no real call/dialler is used | **PASS (smoke)** — `tmp_guardaid_emulator_call_dialog.png`, `tmp_guardaid_emulator_call_result.png` |
| Live FCM delivery and v7 sound | Not exercised in this Demo-only pass | **NOT RUN** — requires dedicated Live accounts/devices and foreground/background/terminated checks |

### 15.4 Current checklist disposition

| Disposition | Meaning for this record |
|---|---|
| **PASS (smoke)** | The named Emulator workflow/control was exercised and showed the expected client-side result. It does not close a Live/API/SQLite/FCM assertion. |
| **PASS** | Reserved for an individual checklist row after its own strict evidence is recorded; no broad page-level smoke result is silently promoted to this status. |
| **NOT RUN** | Checklist rows not explicitly named in Sections 15.2–15.5, including Live/API/SQLite/FCM assertions, untested empty/loading/error permutations, repeated/long-text accessibility checks, and device-specific cases. |
| **OUT OF SCOPE FOR EMULATOR-ONLY CLOSURE** | Live-only backend/security/FCM/media claims and every CROSS/PHONE final-acceptance portion remain open even though some UI can be displayed on the AVD. |

Section 15.5 is a historical record of the checklist revision executed on 21 September 2026: at that time, 71 D — Demo, EMU-only IDs were marked PASS after two consecutive AVD integration runs. The 7 October workflow update changed the meaning of several P07/P08/O05/O06/O07/P09/P10 cases, and later admin work changed P12-03. Therefore, affected rows in the old overlay are historical observations, not current PASS evidence; retest them against the current criteria above. The earlier 4 B — Both IDs were Demo: PASS; Live: NOT RUN at that time. Live-only, CROSS, PHONE, backend, media, persistence, FCM, and any superseded row remain open until current evidence is recorded.

This is a closure of the 71 Emulator-sufficient client-side criteria only. It is not a claim that the whole 215-criterion checklist, Live mode, or physical-phone acceptance has passed.

### 15.5 Historical Emulator-only closure matrix — 71 IDs (21 September 2026)

The following matrix is the per-ID result overlay for the rows in Sections 8–9. All rows below are D-only in the mode mapping, so Demo evidence is sufficient for this specific client-side criterion. Evidence is the same two consecutive full-suite runs recorded in Section 15.2; the test source is [`integration_test/emulator_operations_test.dart`](integration_test/emulator_operations_test.dart). These results do not satisfy any corresponding B/L/Live requirement.

| # | Checklist ID | Result | Evidence |
|---:|---|---|---|
| 1 | P01-01 | **PASS** | Demo startup/sign-in assertion; full suite runs 1–2 |
| 2 | P03-01 | **PASS** | Demo credentials/prototype notice; full suite runs 1–2 |
| 3 | O01-01 | **PASS** | Reset dialog opens; full suite runs 1–2 |
| 4 | O01-02 | **PASS** | Reset cancellation returns to sign-in; full suite runs 1–2 |
| 5 | P06-02 | **PASS** | Four-tab navigation; full suite runs 1–2 |
| 6 | P06-03 | **PASS** | Notification icon routes to Updates; full suite runs 1–2 |
| 7 | P06-04 | **PASS** | Two-part user name renders as `AM`; full suite runs 1–2 |
| 8 | P06-08 | **PASS** | 320×640 emulator surface keeps NavigationBar usable; full suite runs 1–2 |
| 9 | P07-06 | **PASS** | Camera-name search; full suite runs 1–2 |
| 10 | P07-07 | **PASS** | Location search; full suite runs 1–2 |
| 11 | P07-08 | **PASS** | Incident-type search; full suite runs 1–2 |
| 12 | P07-09 | **PASS** | No-match search state; full suite runs 1–2 |
| 13 | P07-10 | **PASS** | All quick-filter state; full suite runs 1–2 |
| 14 | P07-11 | **PASS** | Weapon quick filter; full suite runs 1–2 |
| 15 | P07-12 | **PASS** | Violence/Bullying quick filter; full suite runs 1–2 |
| 16 | P07-13 | **PASS** | Open quick filter; full suite runs 1–2 |
| 17 | P07-14 | **PASS** | Combined filtered-result workflow; full suite runs 1–2 |
| 18 | P07-15 | **PASS** | Alert card opens review route; full suite runs 1–2 |
| 19 | O03-01 | **PASS** | Advanced filter sheet opens; full suite runs 1–2 |
| 20 | O03-02 | **PASS** | Incident-status selector; full suite runs 1–2 |
| 21 | O03-03 | **PASS** | Threshold-mode selector; full suite runs 1–2 |
| 22 | O03-04 | **PASS** | Camera-source selector; full suite runs 1–2 |
| 23 | O03-05 | **PASS** | Cancel discards filter draft; full suite runs 1–2 |
| 24 | O03-06 | **PASS** | Apply updates filter count/results; full suite runs 1–2 |
| 25 | O03-07 | **PASS** | All-values clear advanced criteria; full suite runs 1–2 |
| 26 | P08-04 | **PASS** | Ready scene description is displayed; full suite runs 1–2 |
| 27 | P08-07 | **PASS** | Demo evidence preview has no network URL; full suite runs 1–2 |
| 28 | P08-13 | **PASS** | App-bar Back leaves Alert Review; full suite runs 1–2 |
| 29 | O05-01 | **PASS** | Dismiss sheet opens with reason field; full suite runs 1–2 |
| 30 | O05-02 | **PASS** | Empty dismiss reason is rejected; full suite runs 1–2 |
| 31 | O05-03 | **PASS** | Dismiss sheet cancellation; full suite runs 1–2 |
| 32 | O06-01 | **PASS** | Resolve sheet opens; full suite runs 1–2 |
| 33 | O06-02 | **PASS** | Empty resolve reason is rejected; full suite runs 1–2 |
| 34 | O07-01 | **PASS** | Escalation recipients are shown; full suite runs 1–2 |
| 35 | O07-02 | **PASS** | Recipient checkbox state changes; full suite runs 1–2 |
| 36 | O07-03 | **PASS** | No-recipient escalation remains open; full suite runs 1–2 |
| 37 | O07-04 | **PASS** | Empty escalation message is rejected; full suite runs 1–2 |
| 38 | O08-01 | **PASS** | Emergency simulation safety warning; full suite runs 1–2 |
| 39 | O08-02 | **PASS** | Emergency simulation cancellation; full suite runs 1–2 |
| 40 | P10-01 | **PASS** | Logs show final-status records; full suite runs 1–2 |
| 41 | P10-03 | **PASS** | Saved-record search; full suite runs 1–2 |
| 42 | P10-04 | **PASS** | All saved statuses selector; full suite runs 1–2 |
| 43 | P10-05 | **PASS** | Resolved filter; full suite runs 1–2 |
| 44 | P10-06 | **PASS** | Dismissed filter; full suite runs 1–2 |
| 45 | P10-07 | **PASS** | Escalated filter/no-result state; full suite runs 1–2 |
| 46 | P10-08 | **PASS** | No matching log state; full suite runs 1–2 |
| 47 | P11-04 | **PASS** | Settings integration assertion confirms Evidence access, Prototype view/build and the empty Application section are absent; latest Emulator suite 4/4 |
| 48 | P11-05 | **PASS** | Alert Review evidence preview remains reachable after Settings cleanup; latest Emulator suite 4/4 |
| 49 | P12-01 | **PASS** | Administrator roles page; full suite runs 1–2 |
| 50 | P12-03 | **PASS** | Fixed-role prototype boundary; full suite runs 1–2 |
| 51 | O12-01 | **PASS** | Threshold edit sheet/options; full suite runs 1–2 |
| 52 | O12-02 | **PASS** | Threshold sheet cancellation; full suite runs 1–2 |
| 53 | O12-03 | **PASS** | Same-value save closes without redundant confirmation; full suite runs 1–2 |
| 54 | O12-04 | **PASS** | Different-value confirmation shows old/new choice; full suite runs 1–2 |
| 55 | O13-01 | **PASS** | Sensitivity confirmation cancellation; full suite runs 1–2 |
| 56 | P13-01 | **PASS** | Administrator user list; full suite runs 1–2 |
| 57 | P13-02 | **PASS** | User name search; full suite runs 1–2 |
| 58 | P13-03 | **PASS** | User email search; full suite runs 1–2 |
| 59 | P13-04 | **PASS** | User role search; full suite runs 1–2 |
| 60 | P13-05 | **PASS** | No-match user search; full suite runs 1–2 |
| 61 | O14-01 | **PASS** | User edit sheet identity/controls; full suite runs 1–2 |
| 62 | O14-02 | **PASS** | No-role draft disables Review changes; full suite runs 1–2 |
| 63 | O14-03 | **PASS** | Pending assignment review; full suite runs 1–2 |
| 64 | O14-04 | **PASS** | Active assignment review; full suite runs 1–2 |
| 65 | O14-05 | **PASS** | Disabled assignment review; full suite runs 1–2 |
| 66 | O14-06 | **PASS** | User edit-sheet cancellation; full suite runs 1–2 |
| 67 | O15-01 | **PASS** | Assignment confirmation cancellation; full suite runs 1–2 |
| 68 | P14-02 | **PASS** | Borderline setting disabled toggle; full suite runs 1–2 |
| 69 | P14-03 | **PASS** | Borderline setting enabled toggle; full suite runs 1–2 |
| 70 | P14-04 | **PASS** | Recipient checkbox draft state; full suite runs 1–2 |
| 71 | P14-05 | **PASS** | Empty-recipient save validation; full suite runs 1–2 |

## Alert Centre update — 2 October 2026

P07-04 and P07-05 now use pull-to-refresh and foreground five-second polling; the explicit Refresh icon no longer exists. Opening Alert Review also records that user's viewed state without changing the incident status. Historical integration results above remain historical evidence and do not establish acceptance of these new behaviours.

Local automated verification: Flutter analysis passed, all 27 Flutter tests passed (including six refresh/read tests), all 26 SQLite repository tests passed, and the runtime-settings round-trip test passed. New tests cover stable scrolling, four-second arrival notices, the View entry, lifecycle pausing, request coalescing, failure recovery, and per-user persisted read state. This round did not run Emulator or physical-device acceptance tests; the affected manual rows remain unchecked.

See [Alert Centre refresh update](ALERT_CENTRE_REFRESH_UPDATE_2026-10-02.md) for implementation checks and remaining device acceptance steps.
