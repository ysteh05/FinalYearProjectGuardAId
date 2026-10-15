# Alert Centre refresh and unread indicators

Implemented: 2 October 2026.

## Behaviour

- The explicit Refresh button has been removed. Pull down to refresh manually.
- Alert Centre checks for updates every five seconds while its tab is active, its route is visible, and the App is in the foreground. Checks pause on other tabs, behind detail/filter routes, and in the background.
- Slow requests do not overlap. Automatic network failures keep existing content without repeated error pop-ups; manual failures show a retry message.
- Search and filters remain unchanged. When browsing older alerts, existing cards keep their position and content height. Newly arrived cards are queued instead of being inserted above the current view.
- A non-blocking arrival notice lasts four seconds. A compact `N new alerts · View` button remains while matching new alerts are queued. Tapping it returns to the latest alerts. Returning near the top or pulling to refresh also applies the latest list.
- Access restrictions still take effect immediately: records that are no longer accessible are removed even while browsing.
- Alert cards show a red dot until that user opens Alert Review and the read request succeeds. A failed request does not falsely mark an alert as viewed.
- Viewing an alert does not resolve, dismiss or escalate it, and does not mark an Updates notification as read. Alerts are incident/evidence records; Updates are notification/activity records.

## Backend and deployment

SQLite migration 009 adds `alert_reads`, keyed by user and alert. This persists the viewed state independently for each user. Older alerts initially appear unviewed because earlier versions did not record this history.

The authenticated read endpoint is `POST /api/mobile/alerts/{alert_id}/read`. It checks the same account and incident-access rules as Alert Review before saving the state.

Restart the backend and rebuild/install the Mobile App using the existing Mobile Phone Run Guide. The backend applies the migration automatically. No new Firebase setup, manual database command, uninstall or clear-data step is required.

## Completed implementation checks

| Check | Evidence | Result |
|---|---|---|
| No explicit Refresh button; pull-to-refresh works | Flutter widget test | PASS |
| Five-second polling; inactive tab, covered route and background pause | Flutter widget test | PASS |
| Slow refresh cannot create overlapping requests | Flutter widget test | PASS |
| New alerts preserve the visible card and scroll offset while browsing | Flutter widget test at 320 × 640 and 2× text scale | PASS |
| Four-second notice disappears; View entry remains and opens the latest list | Flutter widget test | PASS |
| Background failure retains search, data and position; later refresh recovers | Flutter widget test | PASS |
| Opening Alert Review marks the alert viewed; decisions and Updates unread count are unchanged | Flutter widget test | PASS |
| Failed read request does not clear the dot | Flutter widget test | PASS |
| Read state is per-user, persistent and idempotent; unauthenticated/Pending requests are rejected | Backend API/SQLite test | PASS |
| Runtime settings survive schema 009 initialization | Backend settings round-trip test | PASS |
| Flutter static analysis | No issues found | PASS |
| Full local Flutter test suite | 27 tests passed | PASS |
| SQLite repository suite | 26 tests passed | PASS |

These are local automated checks, not a claim that a new Emulator or physical-phone acceptance run has passed. No connected phone was operated during this change. Real-device acceptance should confirm natural pull gestures, visual placement, and Live Mode updates after installing the new build.

## Suggested device acceptance

1. Scroll down, keep a search/filter selected, and generate an accessible test alert. Confirm the current view does not jump and the temporary notice appears.
2. Tap the new-alert entry. Confirm it opens the newest matching alerts without clearing filters.
3. Open one alert, return, and restart the App. Confirm its red dot stays cleared for that user but remains for a different user who has not viewed it.
4. Disconnect the backend temporarily. Confirm polling does not wipe content or repeatedly interrupt the user; manual pull shows an error. Restore connectivity and confirm recovery.
5. Switch tabs, open Alert Review and background the App. Confirm no unnecessary Alert Centre polling while it is hidden.

Physical-device agent operations remain strictly restricted to `com.guardaid.prototype`; do not navigate to system settings, notification shade or another App.
