# GuardAId Mobile UI Review and GitHub Baseline

This guide creates a private, rollbackable baseline of the Flutter mobile app before UI changes.
It also explains how to expose the running phone screen for review.

## A. Let Codex inspect the phone UI

For layout review, offline Demo mode is sufficient and avoids Firebase/FCM side effects:

```powershell
Set-Location "<PROJECT_ROOT>\mobileapp"
flutter pub get
$adb = "<ANDROID_SDK>\platform-tools\adb.exe"
& $adb devices
flutter run -d "YOUR_DEVICE_ID"
```

Demo credentials are `admin@guardaid.demo` and `GuardAId123!`.

For authenticated live screens, first follow `../guardaid_monitor/MOBILE_PHONE_RUN_GUIDE.md`,
then use:

```powershell
flutter run -d "YOUR_DEVICE_ID" `
  --dart-define=GUARDAID_LIVE_MODE=true `
  --dart-define=GUARDAID_BACKEND_URL=http://localhost:8020
```

Capture the current physical-device screen into the shared workspace:

```powershell
$adb = "<ANDROID_SDK>\platform-tools\adb.exe"
$outDir = "<PROJECT_ROOT>\mobileapp\ui_review"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
& $adb -s "YOUR_DEVICE_ID" exec-out screencap -p > (Join-Path $outDir "current.png")
```

Tell Codex the saved path and the page/state represented by the image, for example
`login`, `Alert Centre`, `alert detail`, `Settings`, `pending approval`, or an overflow case.
Screenshots can also be attached directly in chat. Keep the phone connected and the app open if
you want a live screen inspection; reconnecting the phone may change the device ID.

## B. Create the GitHub baseline (recommended: mobileapp only)

1. On GitHub, create a new **Private** empty repository, for example `guardaid-mobile`.
   Do not add a README, `.gitignore`, or license because this folder already contains them.
2. In PowerShell, initialise only the `mobileapp` folder:

```powershell
Set-Location "<PROJECT_ROOT>\mobileapp"
git init
git branch -M main
git add -A
git status --short
```

Before committing, confirm that the status does **not** include `build/`, `.dart_tool/`,
`android/app/google-services.json`, or `lib/firebase_options.dart`. These are ignored by the
existing mobile `.gitignore`.

3. Save the baseline locally and label it:

```powershell
git commit -m "Baseline mobile app before UI review"
git tag -a before-ui-review -m "Clean baseline before Codex UI changes"
```

4. Connect the new GitHub repository and upload the snapshot:

```powershell
git remote add origin "https://github.com/YOUR_GITHUB_USERNAME/guardaid-mobile.git"
git push -u origin main
git push origin before-ui-review
```

GitHub may open a browser or Credential Manager prompt. Do not paste a GitHub password or
personal-access token into chat, source files, or a command that will be saved in shell history.
GitHub Desktop can perform the same sign-in and push flow without manually handling a token.

## C. Meaning of the Git operations

- `git init`: creates local version-control metadata.
- `git add -A`: stages the current files for the next snapshot; ignored secrets/build outputs stay out.
- `git status`: review exactly what will be saved.
- `git commit`: creates a local immutable checkpoint.
- `git tag`: gives the checkpoint an easy name for rollback/reference.
- `git remote add origin`: records the GitHub destination.
- `git push`: uploads commits/tags to GitHub; only this step creates the remote backup.

After later UI changes, inspect and save them with:

```powershell
git status
git diff
git add -A
git commit -m "Refine mobile UI layout"
git push
```

If a change needs review before committing, leave it uncommitted; Codex can inspect `git diff`.
Do not use `git reset --hard` or `git clean` as a shortcut because they can remove uncommitted work.

## D. What must not be uploaded

Never commit Firebase Admin service-account JSON, passwords, FCM registration tokens, private `.env`
files, CCTV videos, evidence images, SQLite databases, model weights, or generated build folders.
The Firebase client config is already ignored in this Flutter project.

For a full-project GitHub backup rather than a mobile-only backup, first create a separate root
`.gitignore` for model/data/credential exclusions and confirm the scope; do not run `git add -A`
from the 6000CEM root until that review is complete.
