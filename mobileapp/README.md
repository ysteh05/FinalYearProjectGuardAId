# GuardAId Mobile

Flutter Android prototype for role-based school safety alert review and response. The interface is fully English and follows DPP Figures 3.23–3.34.

## Implemented flows

- splash, sign-in, password reset, pending approval and disabled-account states;
- shared Alert Centre and evidence review for all Active role-assigned users, with search and filters;
- inherited role sensitivity for notification delivery only; FCM device registration is not required to view incidents;
- authenticated image/video evidence and asynchronous VLM scene descriptions;
- dismiss as false alarm or confirm an incident through one of three exclusive paths: request a handler with a role-targeted Broadcast, take ownership, or record that it is already resolved;
- handler ownership and later resolution, with optional comments and actor-attributed history;
- two bounded reminder phases for unanswered decisions and unclaimed Broadcasts; routine collaboration updates use a non-urgent channel;
- optional simulation-only emergency call confirmation — no dialler API is present and the simulation does not change incident/reminder state;
- legacy Escalate records remain readable, but the new workflow offers no Escalate action;
- updates/read state and saved incident logs;
- School Administrator role sensitivity, user assignment and borderline settings;
- Firebase Authentication, Firebase Cloud Messaging device registration and FastAPI ID-token requests in live mode.

## Demo mode

Demo mode is the default and never contacts Firebase or the GuardAId backend.

```powershell
cd mobileapp
flutter run
```

Demo credentials:

```text
Email:    admin@guardaid.demo
Password: GuardAId123!
```

The verified debug APK is written to:

```text
build/app/outputs/flutter-apk/app-debug.apk (relative to this project)
```

## Live mode

Do not enable live mode until Firebase and backend authentication are configured. Follow `../guardaid_monitor/FIREBASE_AUTH_FCM_SETUP_TUTORIAL.md` first.
For the exact three-terminal day-to-day startup order, use
`../guardaid_monitor/MOBILE_PHONE_RUN_GUIDE.md`.

Android application ID:

```text
com.guardaid.prototype
```

After `flutterfire configure` replaces `lib/firebase_options.dart`, connect the authorised physical Android phone over USB and create the local tunnel. Use your Android SDK's `platform-tools\adb.exe` path if `adb` is not on PATH:

```powershell
adb reverse tcp:8020 tcp:8020
```

Then start the app with:

```powershell
flutter run `
  --dart-define=GUARDAID_LIVE_MODE=true `
  --dart-define=GUARDAID_BACKEND_URL=http://localhost:8020
```

`localhost:8020` reaches the computer only while the USB `adb reverse` tunnel is active. This debug workflow keeps Uvicorn bound to the computer loopback interface and does not expose it to the LAN. Run the reverse command again after reconnecting the phone or restarting ADB. A later campus deployment still requires an authenticated HTTPS endpoint; do not expose an unauthenticated Uvicorn port or use public evidence URLs.

## First administrator

The first Firebase identity is promoted locally with the one-time backend command documented in the Firebase tutorial. The command refuses to run after an active School Administrator exists. Later identities enter SQLite as `pending` and are assigned a role in the app.

## Safety rule

“Simulate emergency call” only records an audit action and displays:

```text
Simulated emergency call has been placed. No real call was made.
```

The app does not import or call `url_launcher`, does not open the Android dialler and does not contact an emergency service.

## Alert Centre refresh and unread dots

Pull down from the top of Alert Centre to refresh. The page also refreshes every
five seconds while the Alerts tab is visible and the app is in the foreground.
Opening a detail page or filter sheet pauses polling. There is no separate refresh
button. Failed background requests keep the last loaded data.

When you are browsing older records, new alerts are queued to preserve the visible
content and scroll position. An arrival notice lasts four seconds, while a small
`New alerts · View` button remains for queued alerts matching your current filters.
Tap View, pull to refresh, or return to the top to display the latest cards.

A red dot means you have not opened that alert's review page. Live Mode saves this
state for your account in SQLite; Demo Mode keeps it in memory. Another person's
viewing does not remove your dot. Viewing an alert does not resolve it or mark its
Updates notifications as read.
