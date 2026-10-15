# GuardAId Alert Notification Sound — Implementation Plan

## 1. Requirement

Urgent GuardAId alerts should use a short, recognisable burst sound rather than a long melody:

```text
beep — short pause — beep — stop
```

Each individual notification must play exactly two beeps in one burst. The sound must then end; it must not continuously loop or repeat for the same notification.

This requirement applies to urgent alerts for:

- Weapon detection
- Violence detection
- Physical-contact bullying detection

Routine updates, status messages and ordinary app events must continue using the normal notification sound.

## 1.1 Current implementation status

The first v6 implementation is retained as a legacy asset. User listening feedback found it too quiet, so new urgent alerts now use the revised v7 sound/channel:

- `mobileapp/android/app/src/main/res/raw/guardaid_urgent_beep_v7.wav` is an original mono 44.1 kHz, 0.96-second PCM WAV;
- its 10 ms RMS profile contains two 0.30-second beep regions separated by 0.18 seconds of silence;
- peak level is 98% of PCM full scale, compared with 52% for v6; measured whole-file RMS is about 6 dB higher;
- Flutter foreground and backend FCM urgent paths use `guardaid_urgent_alerts_v7`;
- decision updates continue to use `guardaid_updates_v1`;
- foreground decision payloads override incident type so a decision update cannot select the urgent channel.

Android preserves a channel's sound after creation, so v7 is a new channel ID. Installing/running the updated app creates it without requiring app-data clearing. The v6 channel and file remain unchanged for old installs. Device playback and perceived loudness still require physical-device acceptance; the waveform measurements only prove the packaged audio level and pulse shape.

On 26 September 2026 the v7 sound was generated in response to listening feedback that v6 did not stand out enough. It uses a 2.3 kHz tone with a restrained second harmonic to make the two beeps sharper on small phone speakers. It is an original GuardAId sound and does not reproduce the official Amber Alert/EAS signal.

## 2. Safety and usability principles

- Use an original GuardAId sound, not a copied official Amber Alert/EAS recording or another company’s notification sound.
- Make the two-beep pattern clearly noticeable but short enough not to create unnecessary alarm fatigue.
- Keep the urgent channel separate from the routine updates channel.
- Do not use a full-screen intent, dialler, emergency call or any action outside GuardAId.
- The sound must obey Android user controls, notification permission, Do Not Disturb rules and device volume settings.
- The sound is an attention signal only; it does not change alert severity or alert routing.

## 3. Target audio behaviour

### 3.1 Pattern

Recommended default pattern:

```text
Beep 1: short tone
Pause: short silence
Beep 2: same or slightly rising tone
End: silence; no loop
```

Target total duration: approximately 0.8–1.5 seconds. The exact duration may be tuned after listening tests, but it must remain a single two-beep burst.

### 3.2 Audio asset

- Store a small original WAV asset under `android/app/src/main/res/raw/`.
- Use a lowercase Android-safe filename, for example `guardaid_urgent_beep_v7.wav`.
- Prefer mono PCM WAV with a standard sample rate supported by Android.
- Avoid clipping while keeping the waveform sufficiently audible.
- Do not add digital silence or hidden repeated sections that could make Android appear to loop the sound.

### 3.3 Notification semantics

- One FCM notification equals one two-beep burst.
- Foreground local notifications and background/terminated FCM notifications must use the same urgent channel and sound asset.
- A duplicate notification must not create extra beeps beyond the normal notification/cooldown policy.
- Routine updates must not use the urgent sound.

## 4. Android channel change

Android persists a notification channel’s sound and importance after the channel is created. Therefore, changing the sound must use a new channel ID instead of trying to overwrite the existing channel.

Planned channel:

```text
guardaid_urgent_alerts_v7
```

Implementation requirements:

1. Flutter creates `guardaid_urgent_alerts_v7` with max importance, sound enabled and vibration enabled.
2. Foreground notification details reference the same channel and the new two-beep resource.
3. Backend FCM payloads use `guardaid_urgent_alerts_v7` for weapon, violence and physical-contact bullying alerts.
4. Routine notifications continue using `guardaid_updates_v1`.
5. The mobile run guide documents that rebuilding/running the updated App is required to create v7; do not ask users to clear app data or uninstall merely to create the channel.
6. Do not require uninstalling or clearing data unless a device has a manually locked notification-channel setting that prevents verification.

The exact channel version may be incremented if another sound change is made later. Never silently reuse an old channel ID for a different sound.

## 5. Implementation order

1. Create and listen to the proposed two-beep WAV asset locally.
2. Confirm the asset contains exactly two audible tone regions and no loop/repetition.
3. Change the Flutter urgent channel ID and sound resource.
4. Change foreground local notification details to the new channel/resource.
5. Change backend FCM channel selection to the new urgent channel ID.
6. Update the mobile run guide and notification troubleshooting notes.
7. Run static checks and notification-related automated tests.
8. Verify on an emulator first, then perform a controlled physical-phone check within GuardAId only.

## 6. Acceptance criteria

### Audio asset

- Listening to the file produces exactly two clear beeps followed by silence.
- The file does not loop when played once.
- The sound is distinguishable from routine notifications.
- It is audible at ordinary device volume without clipping or harsh distortion.

### App behaviour

- A foreground urgent FCM message produces exactly two beeps.
- A background urgent FCM message produces exactly two beeps.
- A terminated-app urgent FCM message produces exactly two beeps.
- A routine update produces no urgent two-beep sound.
- Tapping the notification opens the correct GuardAId alert.
- Notification permission denial does not crash the App or change backend alert creation.
- Existing alert cooldown and duplicate suppression rules remain unchanged.

### Channel/version verification

- Flutter and backend use the same urgent channel ID.
- The device shows the new channel after the updated App is installed/run.
- The old channel is not used for new urgent messages.
- No external dialler, full-screen emergency UI or unrelated App is opened.

## 7. Out of scope

- Copying or reproducing an official Amber Alert/EAS signal.
- Changing alert thresholds, recipient roles, cooldown or merge-gap logic.
- Making the sound override Android Do Not Disturb or user notification settings.
- Sending repeated sound notifications to compensate for a missed or muted notification.
- Adding emergency calling behaviour.
