# GuardAId Mobile Alert UI and Decision Notification — Implementation Plan

## 1. Scope

This plan records four related mobile-app improvements:

1. Remove the non-functional `Evidence Access` and `Prototype View`/`Prototype build` entries from Settings > Application.
2. Improve the information layout of each Alerts page card, especially confidence, sensitivity mode and status.
3. Show clearly who performed the latest alert decision and what decision was performed in Alert Review.
4. Notify the other users who received the original alert when a user dismisses, resolves or escalates it. This decision-update notification must use the normal notification sound, not the urgent alert sound.

The changes must remain aligned with the existing DPP mobile workflows, Firebase Authentication, SQLite persistence, FCM delivery and one-primary-role policy.

This document is an implementation plan. The work should be implemented and checked in the order below; creating this file does not itself change the application.

## 1.1 Implementation status

The source changes described here have been implemented locally. The backend suite now includes structured scene-summary, recipient-snapshot, actor-exclusion, escalation de-duplication, urgent-channel and no-empty-audience coverage. The latest backend run passed 37 tests. The direct Dart SDK analyzer reports no issues, and the Flutter widget suite passes 16 tests with `--no-pub --concurrency=1`, including the authorised notification-tap routing path and urgent/routine channel separation. The mobile parser also enforces the same concise contract as the backend: at most 12 description words/120 characters and at most 10 words/120 characters per evidence item.

The following follow-up requirements are now explicitly covered by the implementation and checklist:

- concise typed `sceneSummary` rendering on both Alert cards and Alert Review;
- explicit `No decision recorded yet` and separate simulated-call activity;
- original-recipient snapshot, actor exclusion, unrelated-user exclusion and escalation de-duplication;
- no untargeted notification row when no eligible device remains;
- routine decision updates on `guardaid_updates_v1`, independent of incident type.
- foreground local-notification taps and notification-launch payloads are routed through the same `alertId` stream as FCM taps.
- the unavailable-summary fallback is identical in backend and mobile: `AI scene summary is unavailable. Review the video evidence.`

## 1.2 Verification status

Automated verification currently passes:

```text
guardaid_monitor: ..\\.venv\\Scripts\\python.exe -m pytest -q tests  -> 37 passed
mobileapp: C:\\flutter\\bin\\cache\\dart-sdk\\bin\\dart.exe analyze .  -> No issues found
mobileapp: C:\\flutter\\bin\\flutter.bat test --no-pub --concurrency=1  -> 16 passed
mobileapp emulator: C:\\flutter\\bin\\flutter.bat test integration_test/emulator_operations_test.dart -d emulator-5554 --no-pub  -> 4 tests passed
v7 WAV: 0.96 seconds, mono 44.1 kHz PCM, exactly two 0.30-second beep regions, 98% peak, about 6 dB higher RMS than v6
```

Live/manual acceptance is still open. The documented Live runner did start the backend healthily and built/installed a debug APK on the detected RMX3370 device, but Flutter then stopped with `Error waiting for a debug connection: The log reader stopped unexpectedly`. A release Live build was subsequently installed in-place with `adb install -r`, launched only as `com.guardaid.prototype`, and remained alive; package-filtered logs showed successful Firebase initialisation and no crash. This confirms release startup, not FCM delivery or sound behaviour.

The standard Realme-sized API 36 emulator (`emulator-5554`) has now been used for an app-scoped manual pass. Structured summaries rendered correctly on Home and Alert Review without raw JSON, pending summaries kept evidence/actions available, the explicit no-decision state was visible, and the simulated-call confirmation/result stayed inside the prototype without opening a dialler. The resolved Home card also showed the revised confidence/sensitivity/status layout. These emulator observations do not prove Live FCM delivery or physical v7 playback. Foreground/background/terminated FCM delivery, physical notification-tap navigation and actual v7 playback therefore remain unverified and must not be reported as passed.

A further safe Live-startup check was completed on `emulator-5554` using the documented release runner. The backend became healthy on `127.0.0.1:8020`, the USB reverse tunnel was established, the release APK installed successfully, the Android notification permission prompt was accepted, and the app reached the Live Firebase sign-in screen with `com.guardaid.prototype` still alive. No Firebase account was entered, so this proves Live configuration/startup only; authenticated API access, FCM delivery and sound playback remain open.

On 21 September 2026 the documented local checks were rerun from the current worktree: the backend completed 37 tests, Flutter completed 16 widget tests, Dart analysis reported no issues, and the emulator integration suite completed all four tests with exit code 0 after rebuilding and installing the debug APK. These are repeatable local/demo checks only; they do not close the Live FCM, sound or physical-phone acceptance gates.

## 2. Existing implementation considerations

- The Settings page currently displays two informational tiles under `Application`: `Evidence access` and `Prototype build`. They do not provide an actual action or setting.
- Alert cards currently show status separately above a `Wrap` containing confidence, sensitivity and optional borderline badges. At narrow widths this can look uneven and visually crowded.
- The backend already records alert actions with `user_id`, action, note and timestamp, and can return `performed_by` using the user display name.
- The mobile API already returns an `actions` list, but the mobile model currently keeps only the latest action note. The actor and action must be retained as typed data.
- Initial alert delivery must preserve the exact users/devices that were eligible at alert time before decision-update notifications can be targeted correctly.
- Android urgent and routine notification channels already exist. Decision updates must use the routine updates channel.

## 3. Settings > Application cleanup

### 3.1 Required UI change

Remove both non-functional entries:

- `Evidence access`
- `Prototype view` / `Prototype build`

Since these are the only entries under `Application`, remove the empty `Application` heading and its spacing as well. Do not leave a blank section or an inactive-looking card.

Keep real settings and account actions, including:

- Profile/account information.
- Administration links available to the current permissions.
- Sign out.

### 3.2 Behaviour and documentation

- Do not add replacement toggles for these two entries.
- Do not remove backend evidence authorization or actual evidence access; only remove the non-functional informational UI.
- Update UI test checklists and screenshots/documentation that list these entries.
- Demo and Live Mode should both show the same cleaned-up Settings page.

### 3.3 Completion criteria

- No `Evidence access`, `Prototype view` or `Prototype build` tile is visible under Settings.
- No empty `Application` section remains.
- Evidence playback and protected API access continue to work from Alert Review.
- Settings does not gain overflow or excessive blank space after removal.

## 4. Alerts page card layout

### 4.1 Layout goal

The card should be scannable without making the three values look like unrelated pills. Confidence, sensitivity and status need a consistent visual hierarchy and enough breathing room.

Recommended structure:

```text
┌────────────────────────────────────────────┐
│ [incident icon]  Violence              Open │
│                  Block B Corridor           │
├────────────────────────────────────────────┤
│ Confidence       Sensitivity                │
│ 100%              Certain                    │
├────────────────────────────────────────────┤
│ Location: Block B · Level 2                 │
│ Detected: 20 Sep 2026, 10:32                │
└────────────────────────────────────────────┘
```

The exact visual implementation may use a responsive `Wrap`, two-column metric rows or a compact grid. It must not force three long badges into one line.

### 4.2 Required presentation rules

- Keep the alert type and camera name as the primary heading.
- Place the current status (`Open`, `In review`, `Resolved`, `Dismissed`, `Escalated`) in a stable position near the heading.
- Present confidence and sensitivity as labelled metrics rather than relying only on colour or icon meaning.
- Display confidence as `100%` when it is an exact whole percentage; otherwise use one decimal place, such as `82.4%`.
- Keep `Certain`, `Moderate` and `Sensitive` clearly labelled as sensitivity mode, not as confidence.
- Keep the optional `Borderline review` indicator visually secondary.
- Use consistent left alignment for labels and values where possible.
- Use semantic colours with text labels; the design must remain understandable without colour perception.
- Avoid a right-aligned status row followed by a separate uneven badge row.
- Use responsive wrapping/vertical fallback for 320×640 screens and 1.3x/2.0x font scale.
- Preserve tap target size, card spacing, accessibility semantics and the existing alert opening behaviour.

### 4.3 Completion criteria

- Confidence, sensitivity and status are visually distinct and easy to scan.
- No badge overlap, clipped text or awkward one-item wrapping occurs.
- The card remains readable for long camera names, long locations and high font scale.
- The layout looks balanced for weapon, violence and physical-contact bullying alerts.
- No alert data or filter behaviour changes.

## 5. Alert Review decision attribution

### 5.1 Data model

Introduce a typed mobile action record, for example:

```text
AlertActionRecord
  action: dismiss | resolve | escalate | other recorded action
  performedBy: display name
  performedByEmail: optional secondary fallback
  note: optional reason/message
  createdAt: timestamp
```

The API should continue returning the existing action history and add/retain typed fields as needed. Do not rely on a single free-form `actionNote` for actor attribution.

The display name is the primary actor label. Email is only a secondary fallback when the display name is unavailable.

### 5.2 Alert Review UI

At the top information area, show the latest decision clearly:

```text
Latest decision
Resolved by Alice Tan
20 Sep 2026, 10:32
Reason: Staff intervention was completed.
```

Use equivalent wording for:

- `Dismissed by <display name>`
- `Escalated by <display name>`

If the alert has no recorded decision, show:

```text
No decision recorded yet
```

If more than one action exists, show the latest final decision prominently and provide a compact `Decision history` section for the earlier records. Do not hide the actor/action behind a raw JSON object.

### 5.3 Rules

- Use the action record timestamp, not the alert detection timestamp, for a decision.
- Use the backend-recorded actor, never the currently signed-in user as an assumption.
- Treat `simulated_emergency_call` as a separate recorded operation, not as a resolve/dismiss/escalate decision.
- Keep the existing action confirmation, reason requirement and permission checks.
- A final decision remains read-only according to existing backend policy; the UI must not imply that another user can overwrite it.

## 6. Decision-update notifications

### 6.1 User-facing requirement

When an authorised user performs `dismiss`, `resolve` or `escalate` on an alert, other users who received the original alert should receive a normal notification explaining:

- Who made the decision.
- What decision was made.
- Which alert it concerns.

Illustrative notification content:

```text
Title: Alert resolved
Body: Alice Tan resolved the Violence alert at Block B Corridor.
```

For other actions:

```text
Alice Tan dismissed the Physical-contact bullying alert at Cafeteria Camera 2.
Alice Tan escalated the Violence alert at Block B Corridor.
```

Do not put the full reason or evidence path in the push payload. The user can open Alert Review to see the reason and evidence.

### 6.2 Exact recipient rule

The target is the snapshot of users who were eligible for and received the original alert notification, excluding the user who just made the decision.

This must not be calculated from all current roles because role assignments or sensitivity settings may have changed after the alert was sent.

Required behaviour:

1. When the original alert notification is created, persist its exact eligible `user_id` recipients in `notification_recipients` (or an equivalent user-recipient table).
2. Keep device delivery records separately so one user with multiple devices is deduplicated as a recipient but can still receive on each enabled device.
3. When a decision is recorded, read the original alert-recipient snapshot.
4. Exclude the decision actor by `user_id`, not by display name or email.
5. Send to enabled devices belonging to the remaining original recipients who are still permitted to receive notifications.
6. If no other original recipient remains, do not send FCM and do not create an untargeted notification row that could appear as a global update; the saved action remains the source of truth.
7. Do not send retroactive decision pushes for old alerts that have no original recipient snapshot.

### 6.3 Escalation recipients

For `escalate`, preserve the existing selected-role recipient workflow. The decision-update notification to original recipients is a separate concern and must not be lost if the selected escalation roles differ from the original alert audience.

The implementation must deduplicate device sends if a user is both an original recipient and a selected escalation recipient. No user should receive two identical decision-update notifications for one action.

### 6.4 Normal notification sound

Decision-update notifications must use the routine channel:

```text
guardaid_updates_v1
```

They must not use `guardaid_urgent_alerts_v7` or any other urgent channel. In foreground handling, `source=alert_decision` must override the incident type when selecting the sound; a decision about a violence/weapon alert is still a routine update notification.

The notification data should contain only the minimum routing values, for example:

```json
{
  "source": "alert_decision",
  "alertId": "alert_123",
  "action": "resolve"
}
```

The mobile app should open the corresponding Alert Review page when the notification is tapped.

### 6.5 Notification text and localisation

- Keep notification titles and bodies in English to match the current app requirement.
- Use the saved display name as the actor label.
- Fall back to a safe generic label such as `An authorised user` if no display name exists; do not make an email the primary label unless necessary as a final fallback.
- Do not expose Firebase UID, database ID, local file path or service-account data.

## 7. Backend implementation order

1. Confirm the existing action record query returns action, actor display name, actor fallback email, note and timestamp.
2. Add typed API serialization for action history without breaking the existing `actions` field.
3. Extend initial alert notification persistence to snapshot exact user recipients.
4. Add a repository method to retrieve original alert recipients by alert ID.
5. Refactor decision notification dispatch to target the snapshot recipients and exclude the actor.
6. Keep selected-role escalation recipients working independently.
7. Mark decision-update payloads with `source=alert_decision` and use the routine channel.
8. Ensure repeated/final action protection prevents duplicate decision notifications.
9. Add backend tests before changing mobile rendering.

## 8. Mobile implementation order

1. Remove the two non-functional Settings tiles and the empty section.
2. Add typed action-record parsing to `GuardAlert`.
3. Add latest-decision and decision-history presentation to Alert Review.
4. Refine the Alerts card header and confidence/sensitivity/status metrics responsively.
5. Update foreground FCM classification so `alert_decision` uses the routine channel/sound.
6. Ensure notification taps open the correct Alert Review screen and refresh its action history.
7. Update Demo Mode fixtures so the UI can demonstrate an actor/action without requiring FCM.
8. Update Live Mode integration behaviour and run documentation.

## 9. Tests and acceptance criteria

### 9.1 Settings

- Evidence Access and Prototype View/Prototype build are absent.
- No empty Application section remains.
- Sign out and real administration links still work.
- Evidence playback remains available from Alert Review.

### 9.2 Alerts layout

- 100% confidence, `Certain`, and `Open` are aligned and readable.
- Long values wrap safely without overlapping badges.
- The layout passes 320×640 and 1.3x/2.0x font-scale checks.
- The status remains understandable without colour alone.

### 9.3 Decision attribution

- A resolved alert shows `Resolved by <name>` and the action timestamp.
- A dismissed alert shows `Dismissed by <name>`.
- An escalated alert shows `Escalated by <name>`.
- The reason/note remains visible where appropriate.
- No-decision and malformed/legacy action data have safe empty states.
- The actor shown is the backend-recorded actor, not an assumed current user.

### 9.4 Decision notifications

- Two users receive the original alert; one resolves it; the other receives exactly one normal update notification naming the actor and action.
- The actor does not receive a redundant decision-update notification on another device unless product policy explicitly changes.
- A user who did not receive the original alert does not receive the recipient-snapshot decision update.
- Decision notifications use `guardaid_updates_v1`, not the urgent beep/alarm channel.
- Foreground, background and terminated-app handling all use the routine sound.
- Tapping the update opens the correct alert and shows the decision record.
- Repeated final actions are rejected and do not create duplicate decision notifications.
- FCM failure is recorded without undoing the saved action or corrupting the alert.
- Demo Mode can show the decision-update card/fixture without claiming real FCM delivery.

## 10. Out of scope

- Changing violence, bullying or weapon thresholds.
- Changing the one-primary-role policy.
- Allowing a final alert decision to be overwritten.
- Replacing Firebase Authentication or SQLite.
- Sending evidence media or local file paths through FCM.
- Playing the urgent two-beep sound for decision updates.
