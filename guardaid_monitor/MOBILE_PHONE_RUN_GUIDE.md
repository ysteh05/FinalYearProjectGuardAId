# GuardAId 实体 Android 手机运行指南

本指南用于已经完成 Firebase 初次配置后的日常运行。完整的首次 Firebase
Authentication、FCM、service account 与首位管理员配置，请看
`FIREBASE_AUTH_FCM_SETUP_TUTORIAL.md`。

当前项目已经配置：

- Android application ID：`com.guardaid.prototype`
- Firebase project ID：`guardaid-prototype`
- `mobileapp/android/app/google-services.json`
- Flutter/Dart Firebase options由第4节从本机`google-services.json`读取，并在每次`flutter run`时通过`--dart-define`传入。
- CUDA/Qwen Python环境：`C:\tmp\guardaid-vlm-venv`

`google-services.json`只包含Android客户端配置，不是Firebase Admin service-account凭证。
该文件及其中的配置值必须留在本机，不要上传GitHub或贴到聊天中。当前Flutter代码不会只靠
`google-services.json`自动取得Dart端Firebase options。

## 1. 启动前

1. 用USB连接实体Android手机。
2. 手机开启Developer options和USB debugging。
3. 解锁手机；如出现USB debugging授权窗口，选择Allow。
4. 不要同时运行VLM evaluation dashboard。
5. 若需要Qwen推理，关闭Android Studio、PyCharm和不必要的浏览器页面，尽量保留
   3 GiB以上可用RAM。此值是加载前的安全门槛，不是Qwen的实际峰值内存上限；将门槛调低不会减少实际内存使用。

## 2. PowerShell窗口一：启动Firebase live backend

将service-account路径改成您实际保存在repository外的JSON位置：

```powershell
Set-Location "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\guardaid_monitor"

$env:GUARDAID_PERSISTENCE_MODE="sqlite"
$env:GUARDAID_AUTH_MODE="firebase"
$env:GUARDAID_NOTIFICATION_MODE="firebase"
$env:GUARDAID_FIREBASE_PROJECT_ID="guardaid-prototype"
$env:GUARDAID_FIREBASE_CREDENTIALS="C:\secure\guardaid-secrets\guardaid-prototype-firebase-adminsdk-fbsvc-e4b28ddbe4.json"
$env:GUARDAID_VLM_MIN_AVAILABLE_RAM_GB="3.0"

& "C:\tmp\guardaid-vlm-venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8020
```

看到以下信息后保持窗口开启：

```text
Uvicorn running on http://127.0.0.1:8020
```

不要用`run_demo.ps1`启动手机live mode，因为该脚本会把Firebase Auth设为disabled、
把notification设为console。

## 3. PowerShell窗口二：检查backend并建立USB tunnel

```powershell
Invoke-RestMethod "http://127.0.0.1:8020/api/health"
Invoke-RestMethod "http://127.0.0.1:8020/api/runtime" | ConvertTo-Json -Depth 8
```

然后检查手机：

```powershell
& "C:\Users\YSTeh\AppData\Local\Android\Sdk\platform-tools\adb.exe" devices
```

应显示一行以`device`结尾。若显示`unauthorized`，解锁手机并接受USB debugging授权。

建立端口转发：

```powershell
& "C:\Users\YSTeh\AppData\Local\Android\Sdk\platform-tools\adb.exe" reverse tcp:8020 tcp:8020
& "C:\Users\YSTeh\AppData\Local\Android\Sdk\platform-tools\adb.exe" reverse --list
```

`reverse --list`应包含`tcp:8020 tcp:8020`。手机拔插、ADB重启或电脑重启后，都要重新
执行这一步。

## 4. PowerShell窗口三：安装并运行手机App

每次按本指南通过`flutter run`启动或重新构建App时，都使用下面完整流程。Firebase options是
build-time参数；若只传入Live mode和backend URL，重新构建的App会出现`Live mode is not configured`。
此流程会在本机内存中读取`google-services.json`，不会将配置值写入文档或打印出来。

```powershell
Set-Location "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\mobileapp"
flutter devices

$deviceId = Read-Host "Enter the physical phone device ID shown above"
$googleServicesPath = ".\android\app\google-services.json"
if (-not (Test-Path -LiteralPath $googleServicesPath)) {
    throw "Missing local android/app/google-services.json. Complete the Firebase Android setup first."
}

$firebaseConfig = Get-Content -LiteralPath $googleServicesPath -Raw | ConvertFrom-Json
$firebaseClient = $firebaseConfig.client | Where-Object {
    $_.client_info.android_client_info.package_name -eq "com.guardaid.prototype"
} | Select-Object -First 1
if (-not $firebaseClient) {
    throw "google-services.json has no Android client for com.guardaid.prototype. Check the Firebase Android app configuration."
}

$firebaseApiKey = [string]$firebaseClient.api_key[0].current_key
$firebaseAppId = [string]$firebaseClient.client_info.mobilesdk_app_id
$firebaseSenderId = [string]$firebaseConfig.project_info.project_number
$firebaseProjectId = [string]$firebaseConfig.project_info.project_id
$firebaseStorageBucket = [string]$firebaseConfig.project_info.storage_bucket
if ([string]::IsNullOrWhiteSpace($firebaseApiKey) -or
    [string]::IsNullOrWhiteSpace($firebaseAppId) -or
    [string]::IsNullOrWhiteSpace($firebaseSenderId) -or
    [string]::IsNullOrWhiteSpace($firebaseProjectId)) {
    throw "Required Firebase client fields are missing. Re-check the local google-services.json; do not paste it into chat."
}

$flutterArgs = @(
    "run",
    "-d",
    $deviceId,
    "--dart-define=GUARDAID_LIVE_MODE=true",
    "--dart-define=GUARDAID_BACKEND_URL=http://127.0.0.1:8020",
    "--dart-define=FIREBASE_API_KEY=$firebaseApiKey",
    "--dart-define=FIREBASE_APP_ID=$firebaseAppId",
    "--dart-define=FIREBASE_MESSAGING_SENDER_ID=$firebaseSenderId",
    "--dart-define=FIREBASE_PROJECT_ID=$firebaseProjectId",
    "--dart-define=FIREBASE_STORAGE_BUCKET=$firebaseStorageBucket"
)
flutter @flutterArgs
```

第一次运行本节的新命令时，Flutter会重新构建并安装已带Firebase配置的App；相同application ID下
通常不需要先卸载App或清除数据。确认`flutter devices`显示的device ID属于实体手机，避免误选Emulator。
Urgent alerts use the revised GuardAId two-beep sound on Android notification channel
`guardaid_urgent_alerts_v7`. It is an original, sharper 2.3 kHz double beep, not a copy of the
official EAS broadcast signal: beep, a short pause, beep, then stop. The new WAV is 0.96 seconds and
uses 98% PCM peak level; its measured RMS is about 6 dB above the earlier v6 asset. Android keeps
the sound of an existing channel, so rebuild/re-run the App to create v7, then restart the backend so
Live Mode sends urgent alerts to the matching channel ID. This version change does not require
uninstalling the App or clearing its data. The sound still follows Android's notification/alarm volume
and Do Not Disturb settings; the app does not override those controls.

Mobile alert response now has explicit states: an Active role-bearing user can dismiss an alert as a
false alarm or confirm it as an incident. Confirmation offers three paths: request a handler by
broadcasting to one or more selected roles, take ownership immediately, or record that it is already
resolved. A handler can later mark the incident resolved. Comments are optional. Simulated emergency
calls remain in-app only; the dialler is never opened and no real call is made. The new UI no longer
offers Escalate, while historical Escalate records remain visible as legacy history.

All authenticated Active users with a valid role can view every alert, its review page and evidence
from the moment it exists. Role sensitivity affects notification recipients, not access to incident
records. A user with Certain sensitivity can therefore view a Sensitive alert even though they do not
receive its initial sensitivity-targeted push. Viewing an alert, FCM registration, notification
permission, or notification delivery success does not control access. Pending, Disabled, unauthenticated
and roleless accounts remain denied.

The Alerts page opens with the `Active` filter selected, showing incidents awaiting a decision, a
handler, or in progress. Use `All` when you need to search terminal incidents from the Alerts page;
the Logs tab remains the dedicated resolved/dismissed history view. This default filter does not
restrict which incidents an Active role-bearing user is authorised to review.

If another operator changes an incident while it is open on your phone, the server rejects a new
action based on the old revision. The App refreshes the latest alert and shows a conflict message;
review the updated state before choosing another action. A network retry of an action the server
already committed is treated idempotently and does not create a second decision or reminder.

There are two independent reminder phases, each with two cumulative deadlines. For an undecided alert,
the initial notification and first repeat go to users eligible under the alert's sensitivity and
borderline settings. The second repeat goes to every Active role-bearing user with an enabled device.
If an operator confirms and chooses Broadcast, the initial Broadcast and first repeat go to the selected
roles, without an additional sensitivity filter; the second repeat goes to every Active role-bearing
user with an enabled device. If both deadlines pass while the backend is stopped, restart sends only
the highest due stage rather than replaying both. Defaults are 60 and 120 seconds, configurable on the
web dashboard Config page, and the second delay must be later than the first. Every stage uses its own
idempotent persisted dispatch; failed FCM device deliveries are retried by the backend outbox.

The initial urgent notification uses the two-beep channel `guardaid_urgent_alerts_v7`; a first repeat
uses the three-beep channel `guardaid_urgent_repeat_v1`; the all-user stage uses the four-beep channel
`guardaid_urgent_escalated_v1`. Routine decision updates use `guardaid_updates_v1` and do not use the
urgent sound. When an operator makes a decision or takes ownership, other recipients with a prior
interest in that alert receive a routine update naming the actor and action; the actor does not receive
their own update. The notification opens the same Alert Review page.

To install the new three-/four-beep channels, rebuild or run the Mobile App once using the full command
in section 4 (or the one-command launcher in section 4.1). The App creates the new channels at startup;
no uninstall or data clearing is needed. Restart the backend after updating it. If the App has never
created these channel IDs, Android may fall back to its default channel sound until the updated App is
launched.
When another authorised user dismisses an alert, confirms an incident, takes ownership, or resolves an
incident, other users in the deduplicated prior interested audience receive one routine collaboration
update through `guardaid_updates_v1`; the actor is excluded, and it intentionally does not use the urgent
alert sound. The new workflow has no Escalate action; historical Escalate events remain readable.
Tapping a foreground local notification, or an FCM notification from the background/terminated state,
passes only its authorised `alertId` into the same in-app routing path and opens the matching Alert Review
after the current alert list is refreshed. A missing or unauthorised alert is ignored safely.

If `flutter run` builds/installs the APK but reports `Error waiting for a debug connection: The log reader
stopped unexpectedly`, treat the run as unsuccessful and do not claim Live FCM verification. Stop only that
Flutter run, confirm the same single device remains listed by `flutter devices`, then retry the documented
command. Do not uninstall the App, clear its data, or expose Firebase credentials to troubleshoot this error.
As a tooling-only fallback, the same runner accepts `-BuildMode release`; it builds and installs the Live APK
in place and launches only `com.guardaid.prototype` without waiting for a Flutter debug connection. This
confirms release startup but still requires the manual FCM and sound acceptance steps below.

Current builds use `scene_description_prompt_v2`. Alert Review and compact Alert cards read the
typed `sceneSummary` fields (`Observed`, optional `Key evidence`, and `Uncertainty`) and retain
`sceneDescription` as a compatibility field. Existing SQLite records are read compatibly; do not
delete the database or clear App data just to enable this display change.

之后如果仍按本节用`flutter run`启动，就继续执行本节完整命令块；它会自动从本机文件读取配置，
不需要手工复制或输入Firebase值。若只是点手机桌面上已经安装好的App图标启动，则不需要重新传入
`--dart-define`。但Live mode运行时backend仍须启动，USB tunnel也须按第3节在手机/电脑重连后重建。

### 4.1 单一PowerShell命令（替代三个窗口）

如果希望一次启动backend、health check、USB tunnel和Live Mode App，可以在项目根目录
（`6000CEM`）使用以下一行命令：

```powershell
& ".\guardaid_monitor\scripts\start_live_phone.ps1" -FirebaseCredentials "C:\secure\guardaid-secrets\guardaid-prototype-firebase-adminsdk.json"
```

请把路径替换成实际放在repository以外的Firebase Admin service-account JSON路径；不要把该路径或
JSON内容提交到GitHub。脚本会从本机的`mobileapp\android\app\google-services.json`读取Android
client configuration，不会把Firebase secret写入任何source file。若PowerShell阻止脚本执行，可使用
以下等价的一行命令：

如果已经把唯一的service-account JSON放在本指南约定的`C:\secure\guardaid-secrets`目录，也可以省略
`-FirebaseCredentials`；脚本只会在该目录恰好只有一个JSON时自动采用它，发现多个文件时会停止而不会猜测。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\guardaid_monitor\scripts\start_live_phone.ps1" -FirebaseCredentials "C:\secure\guardaid-secrets\guardaid-prototype-firebase-adminsdk.json"
```

脚本会自动选择唯一一个已授权的实体手机。如果`adb devices`显示多个手机或emulator，命令会安全停止，
并要求明确指定设备，例如：

```powershell
& ".\guardaid_monitor\scripts\start_live_phone.ps1" -DeviceId "RXXXXXXXX" -FirebaseCredentials "C:\secure\guardaid-secrets\guardaid-prototype-firebase-adminsdk.json"
```

运行期间脚本所在的PowerShell窗口保留给Flutter；Flutter按`q`退出后，脚本会自动停止它所启动的backend。
backend stdout/stderr保存在`guardaid_monitor\runtime_data\logs\live-backend.*.log`，不会上传GitHub。
如果启动失败，请先查看该两个log文件。手机重新插拔、ADB重启或电脑重启后，需要重新执行这一行，以重新
建立USB tunnel。首次Firebase配置、首次管理员bootstrap和手机的notification permission仍只需按本指南
相应章节完成一次。

首次运行请允许notification permission，然后使用Firebase Console中建立的Email/Password
account登录。App会自动取得FCM registration token并向backend登记，无需手工复制token。

## 5. 首位管理员（只做一次）

如果SQLite中还没有School Administrator，先从Firebase Console的Authentication > Users
复制该用户UID，然后在`guardaid_monitor`目录运行：

```powershell
& "C:\tmp\guardaid-vlm-venv\Scripts\python.exe" -m app.bootstrap_admin --firebase-uid "PASTE_FIREBASE_UID" --email "YOUR_AUTHORISED_EMAIL" --display-name "School Administrator"
```

若系统已有active School Administrator，不要重复执行；command会拒绝第二次bootstrap。

Live Mode使用的server credential除既有FCM/API所需权限外，还须能查找、创建及删除Firebase Authentication
用户；若新增用户时backend回报Firebase权限不足，请让Firebase project owner检查专用service account的IAM，
不要改成授予项目Owner/Editor，也不要把credential复制到手机。详细权限边界见
`FIREBASE_AUTH_FCM_SETUP_TUTORIAL.md`第6节。

首位School Administrator建立后，日常新增用户不需要进入Firebase Console：在App登录后打开
**Settings > User assignments > Add user**，填写姓名、Email并选择一个role。GuardAId server
会使用只保存在server上的Firebase Admin credential建立Firebase Authentication身份，同时在SQLite
transaction内建立Active用户及唯一role；App随后向该用户请求Firebase密码设置/重设邮件。

邮件请求被Firebase接受不代表收件箱必然收到邮件；用户应检查Spam/Junk。若邮件请求失败，用户仍
会保留为Active，管理员可在该用户的菜单选择**Resend setup email**。GuardAId不会显示或记录随机
初始密码。若Email已经属于同一个Firebase project但尚未关联GuardAId，App会要求管理员明确确认
**Link existing account**；正常新增不需要预先到Firebase Console建立账号。

只有首位School Administrator仍须先在Firebase Console建立Email/Password身份，再运行上方一次性
bootstrap command。管理员不能在App内建立另一个“首位管理员”，也不能修改自己的role或停用自己。
App内的新增、role变更、停用与重新启用均由server再次验证`can_manage_users`和role权限上限；Demo
Mode只在内存中演示，不会建立Firebase账号或发送邮件。

如果输入的Email已经是GuardAId用户，App不会再建立账号，而会直接打开该用户的现有assignment供管理员核对/调整。

## 6. 开始完整演示

电脑浏览器打开`http://127.0.0.1:8020`：

1. 在Cameras上传模拟CCTV视频。
2. 在Config确认violence、weapon、VLM、merge gap与cooldown设置。
3. 在Monitor开始播放与分析。
4. 手机保持登录；符合role/sensitivity的alert会经FCM通知。
5. 打开通知或App Alert Centre，以Firebase ID token向backend读取详情及本地evidence。

Firebase只处理身份与最小push payload。SQLite、CCTV、截图、视频证据和VLM输出留在电脑。

## 7. 关闭

使用单一命令时，在Flutter terminal按`q`；脚本会自动停止它启动的backend。
使用三个窗口的旧流程时，才需要在backend terminal按`Ctrl+C`。
可选择移除USB tunnel：

```powershell
& "C:\Users\YSTeh\AppData\Local\Android\Sdk\platform-tools\adb.exe" reverse --remove tcp:8020
```

## 8. 常见问题

- `adb`无法识别：使用本指南中的完整`adb.exe`路径。
- `unauthorized`：解锁手机，撤销并重新接受USB debugging授权。
- `SocketException/timeout`：先确认backend仍运行，再重新执行`adb reverse`。
- `Firebase ID token invalid or expired`：确认手机与电脑自动日期/时间已开启，退出App账号后重新登录。
- Alert通知声音：GuardAId用独立的`guardaid_urgent_alerts_v7`通知渠道和原创v7双beep声音（约0.96秒：beep、短暂停顿、beep、停止）；声音峰值接近PCM满量程，Android仍按系统通知/Alarm音量和勿扰设定播放。Routine decision update继续使用普通通知声。升级到v7不需要卸载或清除App资料。
- `failed to fetch`：通常代表backend已退出；查看backend terminal。Qwen加载前建议尽量释放内存；当前加载保护门槛为3 GiB，但模型实际峰值可能更高。
- 收不到FCM：确认App已获通知权限、用户为Active、role sensitivity符合alert，并且backend使用
  `GUARDAID_NOTIFICATION_MODE=firebase`而不是`console`。

## 9. 忘记登录Email或密码

先确认当前使用哪一种模式：

- Offline demo mode固定使用`admin@guardaid.demo` / `GuardAId123!`，它不是Firebase账号。
- Live mode使用Firebase Console > Authentication > Users中的Email/Password账号。

### 查回Email

1. 登录Firebase Console。
2. 打开`guardaid-prototype`project。
3. 进入Build > Authentication > Users。
4. 从Email列确认账号；同时核对UID，避免把另一名用户的账号误当成自己的。

有权限的School Administrator也可在GuardAId App的Settings > User assignments查看已经进入
SQLite的用户Email。App新增的账号在建立时就会写入SQLite；尚未关联到GuardAId的既有Firebase身份，
才需要由Firebase project owner在Console核实。

### 通过手机App重设密码

1. 确认App以live mode构建，而不是offline demo mode。
2. 在登录页点`Forgot password?`。
3. 输入Firebase Authentication中完全相同的Email。
4. 点`Send link`。
5. 检查收件箱及Spam/Junk，打开Firebase发送的链接并设置新密码。
6. 回到App，以新密码重新登录。旧Firebase ID token会由App重新取得，无需修改SQLite、role或UID。

Firebase和GuardAId都不会显示或寄回原密码；只能重设。SQLite不保存Firebase password或
password hash，因此不要尝试从`guardaid.db`寻找密码。

若已无法访问该Email mailbox，不要建立同名冒充账号或直接编辑SQLite。应由Firebase project
owner先确认用户身份，然后通过Firebase Console/Admin SDK执行受控的账号恢复；恢复时必须保持
原Firebase UID，才能继续对应现有SQLite role、alerts与audit记录。

## Alert Centre update — 2026-10-02

After this source update, restart the backend and rebuild/install the app using the
existing startup workflow in this guide. The current SQLite schema is v11;
unapplied versioned migrations (including v10/v11 when upgrading an older local
database) run automatically at backend startup. Before the first Live startup
against an existing database, make a private local backup of the SQLite database
and its evidence/media directory together, following `DATABASE_DESIGN.md` section
9. Keep that backup outside the repository and do not share or upload it. Do not
run ad-hoc SQL or delete the existing database. No separate migration command or
new Firebase configuration is needed for these workflow migrations.

The Alerts tab supports pull-to-refresh and foreground polling every five seconds.
While browsing older alerts, a four-second arrival notice and a small
`New alerts · View` entry announce newly queued cards without moving the viewport.
View, pull-to-refresh or scrolling back to the top applies the newest list. Polling
pauses on other tabs, covered routes and when the app is in the background.

The red dot on an alert is specific to the signed-in account. Opening Alert Review
saves it as viewed; this does not resolve the incident or mark an Updates message
as read. Old alerts initially have dots because the previous app did not record
alert viewing history. Demo viewing state lasts only for the in-memory session.
