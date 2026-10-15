# GuardAId Firebase Authentication + FCM 安全配置教程

更新日期：2026-07-17

## 1. 这次 Firebase 会做什么、不做什么

Firebase 只负责：

- Authentication：验证 Web/mobile user 的登录身份并签发 ID token；
- Cloud Messaging：向已经登记的手机 app instance 发送最小 alert notification。

Firebase 不负责：

- database；
- CCTV source video；
- evidence clip 或 screenshot；
- VLM result；
- model checkpoint；
- local filesystem backup。

以上资料继续保存在 `runtime_data/database/guardaid.db` 与 `runtime_data/media/`。

Firebase 官方把 Cloud Messaging 列为 no-cost product，Spark plan 不需要 payment information；大多数 Authentication provider 也可在 Spark 使用。不要启用 Cloud Storage、Cloud Functions、付费 Google Cloud service 或主动链接 Cloud Billing account。官方现行说明：[Firebase pricing plans](https://firebase.google.com/docs/projects/billing/firebase-pricing-plans) 与 [Firebase pricing](https://firebase.google.com/pricing)。

## 2. 目前何时需要您配置

Mobile UI、Firebase client adapter 与 backend mobile API 已完成。Demo mode 仍不需要 Firebase；只有准备运行 live mode 时才需要配置：

1. 使用真实 Email/Password 登录；
2. 由后端验证 Firebase ID token；
3. 登记 Android FCM registration value；
4. 测试真实 push notification 与 authenticated evidence。

在此之前维持：

```powershell
$env:GUARDAID_AUTH_MODE="disabled"
$env:GUARDAID_NOTIFICATION_MODE="console"
```

## 3. 建立免费的专用 Firebase project

1. 打开 [Firebase Console](https://console.firebase.google.com/)。
2. Create project，使用独立名称，例如 `guardaid-prototype-<random>`。
3. Google Analytics 对本项目不是必要，可关闭以减少额外 analytics collection。
4. 不建立 Firestore database。
5. 不进入 Storage/Get Started。
6. 不链接 Cloud Billing account。

实际作用：建立一个空的 Firebase identity/messaging tenant。Project ID 不是密码；但 project IAM 与后续 private key 必须保护。

## 4. 启用 Authentication

1. Firebase Console → Build → Authentication → Get started。
2. Sign-in method → Email/Password → Enable。
3. 不启用 Phone Authentication，以免涉及 SMS billing。
4. 建立 prototype account 时使用专用测试密码，不复用学校、银行或主要 Google account 密码。

Firebase Auth 保存 password credential；SQLite 不保存 password 或 password hash。SQLite 只以 `users.firebase_uid` 对应 Firebase account，并保存 GuardAId role/status。

兼容流程：

```text
Mobile login
  → Firebase returns ID token
  → Mobile sends Authorization: Bearer <ID token> through HTTPS
  → FastAPI verifies signature/expiry/project
  → decoded uid maps to SQLite users.firebase_uid
  → SQLite user_roles decides access
```

Firebase 官方支持把 client ID token 送给 custom backend，并在 server 取出 `uid`：[Verify Firebase ID tokens](https://firebase.google.com/docs/auth/admin/verify-id-tokens)。

新登录用户第一次访问 backend 时会以 `pending` 状态写入 SQLite；在 admin 明确赋予 role/active status 前，不应读取 alert evidence。不要把“成功 Firebase 登录”等同于“已获 GuardAId 授权”。

## 5. 注册 Android/Flutter app

1. Firebase Project settings → General → Add app → Android。
2. Android package name 必须填写 `com.guardaid.prototype`；确定后不要随意更改。
3. 在本机安装 FlutterFire CLI，然后从 `mobileapp` 执行 configuration workflow：

```powershell
dart pub global activate flutterfire_cli
cd "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\mobileapp"
flutterfire configure --platforms=android --out=lib/firebase_options.dart
```

4. 在互动选项中选择刚建立的专用 Firebase project 与 `com.guardaid.prototype` Android app；不要选择 Firestore、Storage、Analytics 或其他不在 scope 的产品。
5. Workflow 会以真实 Firebase client options 取代 app 内的 safe placeholder `lib/firebase_options.dart`，并确保 Android configuration/Gradle setup 与当前 FlutterFire plugins 同步。若另外下载 `google-services.json`，其正确位置是 `mobileapp/android/app/google-services.json`。
6. `firebase_options.dart` 与 `google-services.json` 是 client configuration，不是 service-account private key，但仍不要贴到公开聊天；本 project 已把它们列入 `.gitignore`。
7. `firebase_core`、`firebase_auth` 与 `firebase_messaging` 已加入 `pubspec.yaml`，不要再手动加入 Firestore 或 Cloud Storage package。

iOS 还需要 Apple/APNs setup；如果本次 project 只演示 Android，可以不配置 iOS。

## 6. 后端 service account——必须保密

后端需要 Firebase Admin credential 来验证 ID token、发送 FCM，以及在Mobile App内创建、查找用户，
并在本地SQLite写入失败时撤销刚创建的Firebase身份：

1. Firebase Console → Project settings → Service accounts。
2. 只有准备进行真实联调时才 Generate new private key。
3. 把 JSON 移到 repository 之外，例如：

```text
C:\secure\guardaid-secrets\guardaid-auth-fcm-admin.json
```

4. 不得把 JSON 放入 `guardaid_monitor`、Git、报告附件、截图、email 或聊天。
5. 若怀疑泄露，应在 Google Cloud IAM 删除该 key；只删除本机文件不等于撤销。

这份 JSON 可以代表 trusted server。Browser/mobile 永远不应收到它。

新增用户时，使用该credential的server identity必须拥有Firebase Authentication user lookup/create/delete权限；
发送FCM仍需`cloudmessaging.messages.create`。先让Firebase project owner检查该service account当前IAM角色，
不要为了省事授予Owner或Editor。预定义的Firebase Authentication Admin
(`roles/firebaseauth.admin`)包含Authentication资源的完整读写权限；如要进一步最小化权限，project owner
可按实际SDK调用评估custom role。FCM发送权限名称及Firebase Auth角色权限以Google官方IAM清单为准：
[Firebase Authentication roles and permissions](https://cloud.google.com/iam/docs/roles-permissions/firebaseauth)、
[Firebase Cloud Messaging roles and permissions](https://docs.cloud.google.com/iam/docs/roles-permissions/firebasecloudmessaging)。
如果当前credential只有FCM权限，App内建号会由backend安全拒绝；不要把service-account JSON复制到手机，也不要
在未确认权限范围前随意增加项目级Owner/Editor。

## 7. 启用 Auth 与 FCM adapter

只在当前 PowerShell session 设置：

```powershell
cd "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\guardaid_monitor"
$env:GUARDAID_PERSISTENCE_MODE="sqlite"
$env:GUARDAID_AUTH_MODE="firebase"
$env:GUARDAID_NOTIFICATION_MODE="firebase"
$env:GUARDAID_FIREBASE_PROJECT_ID="YOUR_PROJECT_ID"
$env:GUARDAID_FIREBASE_CREDENTIALS="C:\secure\guardaid-secrets\guardaid-auth-fcm-admin.json"
& "C:\tmp\guardaid-vlm-venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8020
```

没有 `STORAGE_BUCKET`、Firestore URL 或 database URL，因为本架构不使用这些服务。

关闭 PowerShell 后 `$env:` setting 消失；credential 文件仍在您指定的安全目录。

## 7.1 建立第一个 School Administrator

第一个用户不能等待另一个 administrator approve，因此使用只能成功一次的本机 bootstrap：

1. Firebase Console → Authentication → Users → Add user，建立你的 authorised prototype account。
2. 从该 user row 复制 Firebase `UID`。UID 不是密码，但必须与正确 account 对应。
3. 在 backend PowerShell session 执行：

```powershell
cd "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\guardaid_monitor"
$env:GUARDAID_PERSISTENCE_MODE="sqlite"
& "C:\tmp\guardaid-vlm-venv\Scripts\python.exe" -m app.bootstrap_admin `
  --firebase-uid "PASTE_FIREBASE_UID" `
  --email "YOUR_AUTHORISED_EMAIL" `
  --display-name "YOUR_DISPLAY_NAME"
```

实际作用：在 SQLite 建立/启用对应 user，分配 `role_school_administrator`，并写入 audit log。若数据库中已经存在 active School Administrator，command 会拒绝执行，不会建立第二个 bootstrap account。

后续日常新用户由有`can_manage_users`权限的operator在App → Settings → User assignments → Add user完成：

1. 输入Display name、Email并选择一个role；确认后Firebase身份与SQLite Active用户一起创建。
2. App自动请求Firebase密码设置/重设邮件；Firebase接受请求不等于保证邮件送达，失败时可在用户菜单重发。
3. 若同Email已属于GuardAId SQLite，App直接打开既有user assignment；若仅在同一Firebase project存在，App要求管理员明确确认Link existing account。
4. Demo Mode只演示内存UI，不建Firebase身份，也不发邮件。

Firebase Console只保留为创建第一位School Administrator的初始化步骤。后续仍不开放public self-registration；
首次登录的其他既有Firebase用户若尚未关联SQLite，会进入`pending`，由授权管理员分配role并激活。

## 7.2 运行 Flutter live mode

实体 Android 手机应先通过 USB 建立 `adb reverse` tunnel，让 App 在不开放 LAN port 的情况下访问电脑的 localhost backend：

```powershell
& "C:\Users\YSTeh\AppData\Local\Android\Sdk\platform-tools\adb.exe" devices
& "C:\Users\YSTeh\AppData\Local\Android\Sdk\platform-tools\adb.exe" reverse tcp:8020 tcp:8020
& "C:\Users\YSTeh\AppData\Local\Android\Sdk\platform-tools\adb.exe" reverse --list
```

然后运行：

```powershell
cd "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\mobileapp"
flutter run `
  --dart-define=GUARDAID_LIVE_MODE=true `
  --dart-define=GUARDAID_BACKEND_URL=http://127.0.0.1:8020
```

不提供 `GUARDAID_LIVE_MODE=true` 时，App 保持离线 demo mode，不初始化 Firebase。`127.0.0.1:8020` 仅在 USB tunnel 存在时转发至电脑；手机重连或 ADB 重启后须再次执行 reverse command。不要在当前 combined WebUI/API 仍有 unauthenticated routes 时把 Uvicorn port 开放到整个 LAN；先完成全 route authentication、HTTPS 与 firewall boundary，再进行 LAN evidence 联调。

日常逐步启动、健康检查与关闭流程另见 `MOBILE_PHONE_RUN_GUIDE.md`。`run_demo.ps1` 会明确关闭 Firebase Auth 与真实 FCM，因此不能用于 mobile live mode。

## 8. Mobile token registration

手机登录后取得 Firebase ID token 和 FCM registration/Firebase Installation value，然后调用：

```http
PUT /api/mobile/device
Authorization: Bearer <Firebase ID token>
Content-Type: application/json

{
  "registrationValue": "<FCM value>",
  "installationId": "<optional installation ID>",
  "platform": "android",
  "deviceLabel": "Principal test phone"
}
```

Backend 会验证 ID token，把 Firebase `uid` 映射到 SQLite user，再把 device 归属该 user。FCM identifier 不得写入普通 application log。手机每次取得更新值时应重新登记，并更新 timestamp；过期或 FCM 返回 unregistered 时停用。官方建议 server 保存 registration timestamp 并移除 stale/invalid registration：[FCM registration management](https://firebase.google.com/docs/cloud-messaging/manage-tokens)。

## 9. Notification payload

只发送最低必要资料：

```json
{
  "alertId": "alert_123",
  "type": "violence",
  "cameraLabel": "Block A Corridor",
  "detectedAt": "2026-07-17T10:30:00Z",
  "severity": "high"
}
```

不要发送：

- local file path；
- evidence binary/base64；
- service credential；
- Firebase registration value；
- 真实学生姓名；
- 未经授权的完整 VLM description。

FCM 只通知“有 alert”。手机随后带 ID token 向 GuardAId HTTPS API 取详情与 evidence。

## 10. 校园 LAN 与校外访问

当前 WebUI route 尚未全部加入 Firebase Auth/role enforcement，因此继续只绑定 `127.0.0.1`。不要为了让手机访问而直接改成 `0.0.0.0` 并开放 Windows Firewall。

完成全 API authentication 后：

- 校园内：server 使用固定 private IP/hostname；校园 routing/firewall 只允许指定 staff VLAN/Wi-Fi subnet 访问 HTTPS 443。手机与 server 不必在同一个 access point，只要同一校园网络允许互相路由。
- 校外：优先通过学校管理的 VPN 或 ZTNA；Principal 先建立安全 tunnel，再以 Firebase login + SQLite role 访问同一个 internal HTTPS API。
- 若必须公网：使用正式 domain、受信任 TLS、reverse proxy/WAF、rate limiting、Firebase ID-token verification、SQLite role check、audit 与短期 evidence authorization。不得直接公开 Uvicorn port、SQLite、media directory、SMB 或 RTSP。

Firebase Auth/FCM 不会自动让手机访问本机文件；安全的 HTTPS backend endpoint 才负责 evidence streaming。

## 11. 费用与资料去向检查

Firebase Console 中应只有：

- Authentication users；
- registered Android/iOS app configuration；
- FCM messaging capability。

不应出现：

- Firestore database；
- Cloud Storage bucket；
- Cloud Functions deployment；
- linked billing account。

会离开本机的资料只有 Firebase login/account metadata、ID-token validation traffic、FCM registration/installation identifiers 和最小 push payload。CCTV、evidence、SQLite database 与 model output 保留在 GuardAId server。
