# GuardAId Firebase 安全配置教程

> **已被新架构取代：请勿继续按照本文建立 Firestore 或 Cloud Storage。** GuardAId 已在 2026-07-17 改为 SQLite + local filesystem，Firebase 只保留 Authentication 与 Cloud Messaging。当前有效教程是 `FIREBASE_AUTH_FCM_SETUP_TUTORIAL.md`。本文其余内容只保留为设计演进记录。

更新日期：2026-07-16

这份教程的目标不是“尽快把所有东西传上云”，而是让每一步的资料去向、费用与权限都可理解、可撤销。当前 WebUI 默认使用 `demo` mode；在您主动完成本教程并设定环境变量之前，不会连接 Firebase，也不会把视频送出本机。

## 0. 先理解当前的两个运行选择

### 选择 A：本机 Demo mode（目前建议）

- 费用：不需 Firebase、不需 billing account，费用为零。
- 资料去向：上传视频只复制到本机 `guardaid_monitor/runtime_data/`。
- 限制：重启服务后 Firestore-style metadata 不持久化；这是开发模式，不是 DPP 的正式部署架构。
- 适用：UI、camera CRUD、配置、分段、N-of-M、cooldown 与模型 worker 开发。

### 选择 B：真实 Firebase mode（需要正式联调时才开启）

- Firestore 保存 structured metadata；Cloud Storage 保存视频、clip 与 screenshot；Auth 负责用户身份；FCM 负责手机通知。
- 从 2026-02-03 起，Cloud Storage for Firebase 要求项目采用 Blaze pay-as-you-go plan。新 bucket 即使有某些 no-cost usage，仍须绑定 Cloud Billing account。因此“真实 Firebase + 视频存储”不能诚实地称为完全不需 billing 的免费方案。请参考 [Firebase 官方 Storage billing FAQ](https://firebase.google.com/docs/storage/faqs-storage-changes-announced-sept-2024) 与 [Firebase pricing](https://firebase.google.com/pricing)。
- FCM 本身列为 no-cost；Firestore 也有每天重置的免费额度，包括一个 free database、1 GiB storage、50,000 reads/day、20,000 writes/day、20,000 deletes/day、10 GiB/month outbound。请参考 [Firestore usage and limits](https://firebase.google.com/docs/firestore/quotas)。

结论：现在继续使用 Demo mode 最安全。等 WebUI、detector workers 与 mobile app 都需要端到端联调时，再由您决定是否启用 Blaze。

## 1. 在本机先运行 Demo mode

在 PowerShell 中执行：

```powershell
cd "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\guardaid_monitor"
& "C:\tmp\guardaid-vlm-venv\Scripts\python.exe" -m pip install -r requirements.txt
$env:GUARDAID_PERSISTENCE_MODE="demo"
& "C:\tmp\guardaid-vlm-venv\Scripts\python.exe" -m uvicorn app.main:app --port 8020
```

每一步的作用：

1. `cd` 只切换当前目录，不会复制或上传资料。
2. `pip install` 安装后端依赖；它会连接 PyPI 下载套件，但不会读取或上传您的 CCTV 视频。
3. `GUARDAID_PERSISTENCE_MODE=demo` 明确禁止 app 初始化 Firebase adapter。
4. `uvicorn` 只在本机启动 HTTP server；默认地址是 `127.0.0.1`，同一局域网的其他设备不能直接连接。

关闭 PowerShell 或按 `Ctrl+C` 会停止 server。要删除 Demo 上传资料，可从 Cameras 页面删除各 camera；也可在 server 停止后检查并手动删除 `runtime_data/`。

## 2. 建立专用 Firebase project（暂不上传真实资料）

只有决定开始 cloud integration 时才做本节。

1. 打开 [Firebase Console](https://console.firebase.google.com/) 并选择 **Create a project**。
2. 使用独立名称，例如 `guardaid-prototype-<随机后缀>`，避免和个人正式项目混在一起。
3. Google Analytics 对本原型不是必需，可选择关闭。这会减少额外 analytics data collection；不影响 Firestore、Storage、Auth 或 FCM。
4. 建立后先记录 `Project ID`，不要上传任何视频。

这一步实际做了什么：建立一个同时属于 Firebase 与 Google Cloud 的空 project。Project ID 不是密码，可以出现在 client config；但 project 的 IAM 权限与之后生成的私钥必须保密。

## 3. 决定 region 与费用边界

Firestore database location 建立后不能随意迁移，因此不要随手按下一步。

- 对马来西亚本地 prototype，较近 region 通常有较低延迟；但实际学校资料的存放地区应由项目的 privacy/data-residency 要求决定。
- Cloud Storage 的 Google Cloud “Always Free” region 只涵盖官方列出的美国地区；不要为了省一点 prototype 费用而把真实学生 CCTV 传到不符合资料政策的地区。
- 最安全做法是只上传合成、公开许可或已去识别化的短片。不要把个人照片、身份证明、浏览器 profile、本机目录截图、service-account JSON 或完整私人资料集上传。

若启用 Blaze：

1. 只把这个独立 prototype project 连到 billing account。
2. 在 Google Cloud Billing 建立很低的 monthly budget alerts，例如 50%、90%、100%。
3. 必须理解 budget alert 只是通知，不会自动停止服务或形成 hard cap。官方特别说明 budget 不会自动限制支出：[Cloud Billing budgets](https://cloud.google.com/billing/docs/how-to/budgets)。
4. 定期检查 Firebase Console 的 Firestore Usage 和 Google Cloud Billing reports。

## 4. 建立 Firestore（Production mode）

1. Firebase Console → **Build → Firestore Database → Create database**。
2. 选择 **Production mode**，不要选择开放式 test mode。
3. 认真选择 location 后建立 database。
4. 先不要在 console 手工建立 documents；backend 首次启动会建立 `systemSettings/global`。

这一步实际做了什么：建立 GuardAId structured metadata database。视频本身不应放进 Firestore。

当前 repo 的 `firestore.rules` 是 default-deny：浏览器与 mobile client 全部不能直接读写。Phase 1 的请求只通过 trusted FastAPI backend。Firebase server/Admin libraries 会绕过 Firestore Security Rules，因此 service account 只能放在 trusted backend；这也是为什么 browser 绝不能收到它。官方说明见 [Firestore role-based access](https://firebase.google.com/docs/firestore/solutions/role-based-access)。

未来 mobile app 需要直接读取 alert 时，必须先完成 Firebase Auth、role schema 与 Security Rules emulator tests，再把特定 collection 的最小 read permission 打开；不要直接改成 `allow read, write: if true`。

## 5. 建立 Cloud Storage（会触发 Blaze 要求）

1. Firebase Console → **Build → Storage → Get started**。
2. Console 若要求升级 Blaze，请先回到第 3 节确认 billing 与 budget alerts；不接受就停止并继续 Demo mode。
3. 选择符合项目 privacy/latency 要求的 location。
4. 建立后记录 bucket 名，新的默认格式通常为 `PROJECT_ID.firebasestorage.app`。

这一步实际做了什么：建立会接收模拟 CCTV sources、evidence clips 与 screenshots 的 object bucket。它不会自动上传本机任何文件；只有 GuardAId 处于 `firebase` mode 且用户在 UI 上传时，指定文件才会离开本机。

当前 `storage.rules` 同样 default-deny。媒体由 trusted backend proxy，不设 public URL，也不允许匿名 list/read/write。

### 部署目前的 default-deny rules

`firestore.rules`、`storage.rules` 与 `firebase.json` 已在本目录准备好。安装 [Firebase CLI](https://firebase.google.com/docs/cli) 并登录后，在 `guardaid_monitor` 目录执行：

```powershell
firebase deploy --only firestore:rules,storage --project YOUR_PROJECT_ID
```

这个命令只把两份 Security Rules 发布到您明确指定的 Firebase project；不会上传 CCTV 视频、Python code 或 service-account JSON。部署后，普通 browser/mobile Firebase client 会被拒绝，trusted Admin backend 仍可依 IAM 运作。若 CLI 显示的 project ID 不是您刚建立的 prototype project，请取消，不要继续。

## 6. Firebase Authentication

1. Firebase Console → **Build → Authentication → Get started**。
2. 在 Sign-in method 中只启用实际会用到的 provider；prototype 可先启用 **Email/Password**。
3. 建立测试账号时不要复用您的学校、Google 或银行密码。
4. 不要启用匿名登录；监控警报属于受限制资料。

这一步实际做了什么：允许系统以后验证 admin/reviewer/mobile user 是谁。Auth 只确认身份；是否可以看某个 alert 仍由 role metadata、Security Rules 与 backend authorization 决定。

当前第一版 WebUI 尚未把 Auth login 接上，所以不要把它暴露到公网。它应继续只监听 `127.0.0.1`。

## 7. 取得 backend credential——最高风险的一步

Firebase Admin SDK 官方要求对 service-account key 保持极高安全意识：[Admin SDK setup](https://firebase.google.com/docs/admin/setup)。只有在您准备好运行真实 Firebase mode 时才生成。

1. Firebase Console → Project settings → **Service accounts**。
2. 选择生成新的 private key；浏览器会下载一个 JSON。
3. 在 repository 外建立专用目录，例如：

```text
C:\secure\guardaid-secrets\guardaid-admin.json
```

4. 把下载的 JSON 移到该目录。不要打开后截图、贴进聊天、email、Git、OneDrive public share 或放在 `guardaid_monitor/`。
5. repo 的 `.gitignore` 已排除常见 service-account 文件名，但 `.gitignore` 不是保险箱；关键仍是让文件从一开始就不进入 repository。

这份 JSON 实际是什么：它包含可代表 trusted backend 的 private key，不是普通 Firebase web config。泄露者可能绕过 client Security Rules。若怀疑泄露，应立即在 Google Cloud IAM → Service Accounts → Keys 删除该 key，再生成新 key。

更成熟的部署应使用 Application Default Credentials / workload identity，避免长期下载 key；本机 prototype 才使用 file-based credential。

## 8. 只在当前 PowerShell session 启用 Firebase

先安装依赖，然后逐项设置：

```powershell
cd "C:\Users\YSTeh\Documents\INTI\2026_2027_Y3\Python\6000CEM\guardaid_monitor"
& "C:\tmp\guardaid-vlm-venv\Scripts\python.exe" -m pip install -r requirements.txt
$env:GUARDAID_PERSISTENCE_MODE="firebase"
$env:GUARDAID_FIREBASE_PROJECT_ID="YOUR_PROJECT_ID"
$env:GUARDAID_FIREBASE_STORAGE_BUCKET="YOUR_PROJECT_ID.firebasestorage.app"
$env:GUARDAID_FIREBASE_CREDENTIALS="C:\secure\guardaid-secrets\guardaid-admin.json"
& "C:\tmp\guardaid-vlm-venv\Scripts\python.exe" -m uvicorn app.main:app --port 8020
```

这些 `$env:` 值只存在于当前 PowerShell process 与其启动的 server；关闭窗口后不会永久写入 Windows 环境。不要把真实 key path 或 JSON 内容写进共享脚本。

打开 `http://127.0.0.1:8020/api/runtime` 后，应看到：

```json
{"persistenceMode":"firebase","cloudConnected":true}
```

先上传一段无隐私、很短的测试视频。然后检查：

- Firestore `cameras` collection 是否只有 metadata；
- Storage `cameras/<cameraId>/source/` 是否只有那一个测试 object；
- Firestore 是否没有本机绝对路径或 service-account 内容；
- Cameras 页面删除后，document 与 source object 是否都消失。

## 9. FCM 配置何时进行

FCM 在 Firebase pricing 中列为 no-cost，但 mobile app 尚未开发到 device-token registration 时，现在不需要生成额外 server key，也不要寻找旧式 legacy server key。

未来正确流程：

1. Flutter app 登录 Firebase Auth。
2. App 取得 FCM registration token，通过 authenticated API 交给 backend。
3. Backend 把 token 与 user/device metadata 安全写入受限 collection。
4. AlertService 建立 alert/evidence 后立即排队 notification；VLM scene description 异步完成，不阻塞 alert 或 FCM。
5. Push payload 只含 `alertId`、type、camera label、time、severity 等最少资料；不含完整画面、private URL、本机路径、credential 或敏感长描述。
6. App 收到 push 后再次通过 Auth/authorization 取得详情。

FCM 费用现况可查 [Firebase pricing — Cloud Messaging](https://firebase.google.com/pricing)。

## 10. App Check 与公网部署

在 WebUI 或 mobile app 直接访问 Firebase 之前，应加入 App Check。App Check 与 Authentication 是互补的：Auth 识别用户，App Check 证明请求来自可信 app。官方说明见 [Firebase App Check](https://firebase.google.com/docs/app-check)。

Web 可使用 reCAPTCHA Enterprise；官方目前提供每月 10,000 次 assessment 的 no-cost quota。先观察 metrics，再开启 enforcement，避免误挡真实用户。当前 localhost-only WebUI 不应为了 App Check 而提前公开部署。

## 11. 清理、撤销与事故处理

测试完成后：

1. 在 Cameras 页面删除 cloud test camera，确认 Storage object 一并删除。
2. 删除 Firestore 中测试 alerts/users/notifications；保留 audit 数据前先确认没有私人资料。
3. Firebase Authentication 删除不再需要的测试账号。
4. Google Cloud IAM 删除不再使用的 service-account key。
5. 若不再使用整个 project，在 Project settings 删除 project；删除前先确认没有其他 app 共用它。
6. 若仍保留 Blaze project，继续检查 billing reports；budget alerts 不是 hard cap。

若 key 曾经被贴进 Git 或聊天，不只是删除文件：必须撤销该 key，因为 Git history 或第三方系统可能仍保留副本。

## 12. 本项目不会收集或上传的资料

GuardAId 第一版 backend 不会主动读取：

- 浏览器 history、cookies 或 saved passwords；
- Documents/Desktop 中未由用户选择的文件；
- Windows account password；
- Hugging Face token；
- Firebase credential 以外的 cloud credential；
- webcam、microphone 或实时桌面画面。

只有用户通过 Cameras upload 明确选择的视频会被处理。Demo mode 留在本机；Firebase mode 会把该文件上传至您指定的 bucket。任何真实学生 CCTV 使用前，还需要学校授权、retention period、access review、incident handling 与 data-subject/privacy policy；技术上的“可以上传”不等于伦理或法规上的“已经获准上传”。
