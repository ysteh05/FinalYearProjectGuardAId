# GuardAId Mobile UI / Usability Review

**日期：** 2026-09-05  
**状态：** 原始 review findings 已完成实现并在 emulator 重新走查；DPP 尚未完成的功能仍保留为 scope note。本文现在同时包含 DPP coverage audit。  
**目的：** 记录 emulator 走查结果、19 项 UI/usability 改进，以及改进后的验证证据。

## 1. Review environment

- **App package:** `com.guardaid.prototype`
- **运行模式：** Flutter Demo mode（`GUARDAID_LIVE_MODE=false`）
- **Emulator profile：** `Realme_GT_Neo_2_API_36_DriveD`
- **Android：** API 36, Google APIs, portrait
- **逻辑显示：** 1080 × 2400, 420 dpi
- **Firebase / live backend：** 本次没有使用；没有发送真实 FCM，也没有打开真实 emergency dialer。
- **已走查页面：** Login、Alert Centre、Alert Review、Updates、Incident Logs、Settings、Roles & Sensitivity、User Assignment、Borderline Alert Settings。

> Screenshots were captured locally during the review and were not added to the repository. The app was reviewed in Demo mode only.

## 2. Priority summary

| ID | 页面 / 问题 | 优先级 | 建议方向 |
|---|---|---:|---|
| UI-001 | Demo login 页面直接显示 demo password | P1 | 只在明确的 demo build 显示；live/release build 隐藏 password，改用 demo-account action 或受控说明 |
| UI-002 | Login 在高屏幕上上下留白较大，keyboard/短屏适配风险 | P1 | 使用 keyboard-safe scroll layout，并重新平衡 spacing |
| UI-003 | Alert Centre filter chips 右侧被截断（例如 `Oper...`） | P1 | 横向 scroll/wrap，并保留 padding 或 scroll cue |
| UI-004 | 固定 bottom navigation 覆盖列表底部内容 | P1 | 给所有底部列表增加 `bottom navigation height + safe-area` 的 bottom padding |
| UI-005 | 日期 `05/09/2026 · 02:13` 存在日/月歧义 | P2 | 采用 locale-aware formatter，例如 `5 Sep 2026, 02:13` |
| UI-006 | Alert card 同时显示 `Open` pill 和 `Open alert` action | P2 | 保留一个主要 action，减少重复信息 |
| UI-007 | Alert Review 显示 `Threshold modeModerate`，缺少分隔符 | P1 | 改为 `Threshold mode: Moderate`，并检查所有 metadata 拼接 |
| UI-008 | Evidence preview 看起来像不可互动的 dark placeholder | P1 | 加入 play/view affordance、媒体类型、时长，以及 loading/error/empty state |
| UI-009 | Escalate / emergency simulation 缺少一致的 confirmation 与 loading 防重复点击 | P1 | 提交前显示对象、动作和结果；执行中 disable action；simulation 永不打开真实 dialer |
| UI-010 | Updates 的 `Mark all as read` 不够像 button；通知 icon 区分度低 | P1/P2 | 增大 hit target、提供 success/undo feedback；按 weapon/bullying/status 使用区分 icon |
| UI-011 | Incident Logs 的 resolved record 仍显示 `Open alert` | P2 | 改为 `View record` / `Review evidence`；统计卡要明确是 filter 还是 passive stat |
| UI-012 | Settings 滚动时 `Administration` 标题被固定 header 裁掉 | P1 | 调整 sticky header、scroll padding 或 safe-area 处理 |
| UI-013 | Roles 页面没有 DPP 所述的 create-role action | P1（scope decision） | 若角色创建仍在 scope，加入 `Create role`；否则在 DPP/README 明确 prototype 为 fixed roles |
| UI-014 | 修改 sensitivity 会影响整组 assigned users，但没有影响范围确认 | P1 | Save 前显示 role、old → new mode、受影响用户数量 |
| UI-015 | Pending user 的 assignment sheet 默认 `School Administrator` + `Active` | P0/P1（安全） | 默认应为 `No role assigned` + `Pending/Disabled`；明确选择前禁用 Save |
| UI-016 | User assignment 直接 Save 角色/权限；dropdown 只有名称 | P1/P2 | 加 confirmation summary；dropdown 同时显示 sensitivity/简短说明；补 Cancel 与语义标签 |
| UI-017 | Borderline setting 没说明启用后可能增加 alert volume | P1/P2 | 显示 alert-fatigue/extra-alert impact、Enabled/Disabled text 与保存反馈 |
| UI-018 | Accessibility/system consistency 尚未充分体现 | P1 | icon semantics、至少 48dp hit target、font scaling 1.3–2.0、contrast、统一 loading/error states |
| UI-019 | Root 页面按 Back 可能直接离开 app | P2 | root back 增加 exit confirmation，或保证返回行为与导航栈一致 |

## 3. Screen-by-screen findings

### Login

整体视觉层次、品牌识别和输入框可读性良好。主要风险是 demo credentials 被直接展示，以及高屏幕中的大面积空白。短屏或弹出 keyboard 时需要确认 sign-in button、forgot-password action 和 password field 不会被遮挡。建议为 sign-in 增加明确的 loading/failed state，避免重复提交。

### Alert Centre

核心信息（active alerts、camera、search、category filters、severity card）容易理解。但 filter row 发生横向截断，alert list 又会被 bottom navigation 覆盖。卡片中状态 pill 与 `Open alert` 重复；日期格式也应统一。警报的描述若被 ellipsis 截断，必须能从详情页查看完整内容。

### Alert Review / evidence

页面已经具备 incident metadata、evidence、AI scene summary 和 human decision support 的结构。当前最明显的文字 bug 是 `Threshold modeModerate`。Evidence preview 应明确这是 image 还是 video，并提供 tap/play、loading、failed-to-load 和 unavailable 状态。Escalate 与 simulated emergency call 属于高风险动作，必须有确认和提交中状态。

### Updates

Unread count、unread dot 和 `Mark all as read` 的信息架构清楚。建议把 `Mark all as read` 做成真正的 button，并提供 undo snackbar。不同事件类型应使用不同 icon 或颜色，帮助用户快速判断优先级。

### Incident Logs

Summary statistics、search、status filter 和 incident cards 基本清晰。列表底部存在 bottom-nav overlap；resolved record 使用 `Open alert` 会让人误以为仍是未处理警报。需要明确“统计卡是否可点击筛选”，并保证 title wrap 和较大字体下仍然可读。

### Settings

Profile、Administration、Application 分组清晰。滚动时 `Administration` 被 fixed header 裁切，是确定的布局问题；底部的 Evidence access/list content 也需要 safe-area padding。Sign out 应保持确认流程。若 informational cards 不可点击，应让其视觉和 accessibility semantics 清楚表达这一点。

### Roles & Sensitivity

现有 role card、sensitivity mode 和解释文字易于理解。按照 DPP，若“管理员创建新 role”仍是功能范围，当前页面缺少入口；这是需要先确认 scope 的功能差距。Sensitivity 修改会影响同一 role 的所有用户，应在保存前显示影响范围并要求确认。Bottom sheet 建议提供明确 Cancel。

### User Assignment

Pending/active 状态、search 和 per-user edit 结构良好，但 pending user 默认被填成最高权限并且 active，是最需要先修正的安全/可用性风险：误按 Save 可能错误授予权限。必须采用安全默认值，要求管理员明确选择 role 和 access state，并在 Save 前显示确认摘要。

### Borderline Alert Settings

Enable switch、eligible roles、sensitivity 和 checkbox layout 清楚。启用 VLM-assisted borderline alerts 可能增加告警量，页面应直接说明这一点。Switch 旁应有 `Enabled` / `Disabled` 文本；保存时应提供成功反馈，并防止重复点击。

## 4. Change scope used for implementation

1. **Layout/insets：** 修正 bottom navigation overlap、Settings sticky header clipping、filter-chip overflow、短屏和 keyboard-safe scrolling。
2. **Copy/format：** 修正 `Threshold mode: Moderate`，统一 locale-aware date/time，并改善 resolved record、media type 和 action wording。
3. **Safety/permission：** 修正 pending-user 的安全默认值；为 role/sensitivity、assignment、escalation 和 emergency simulation 加确认摘要与 loading guard。
4. **Evidence and state feedback：** 为 video/image evidence 加 play/view affordance，以及 loading、failure、empty、unavailable 状态；所有重要 Save/Mark-as-read action 加结果反馈。
5. **Accessibility：** 检查 semantics、icon labels、48dp hit targets、font scaling、contrast 和 keyboard navigation。
6. **DPP alignment decision：** 先确认是否要实现 create-role；不实现的话，应在 DPP 与 README 记录 fixed-role prototype 的边界。

## 5. Original review boundary (historical)

原始 review 阶段没有修改 Dart、Flutter widget、Android manifest 或 Firebase 配置；以下 implementation 是在原始 review 之后完成的。实现范围仍然没有改变 database、FCM、backend URL 或 authentication flow，也没有发送真实 notification 或打开真实 phone dialer。没有提交或 push GitHub。

## 6. Suggested acceptance checks before implementation

- 在 1080×2400 与较短的 720×1600 profile 各走查一次。
- Android font scale 设为 1.3× 和 2.0×，确认没有 clipping/overlap。
- 验证 pending user 在未明确选择 role/access 前不能被保存为 active administrator。
- 验证 evidence 不可用、加载失败、长视频和不同媒体类型都有可理解的状态。
- 验证 root back、sign out、emergency simulation 都不会造成意外离开或真实拨号。

## 7. DPP compliance audit

### 7.1 Overall answer

**目前这份 review md 原本只记录了 UI/usability findings，并不足以证明 mobile app 已经满足 DPP。重新对照 DPP 后，结论是：不满足全部 DPP plan；目前属于“主要移动端流程已建立，但仍有明确的功能缺口、架构调整和未完成的验证活动”。**

这次 emulator review 只能证明 Demo mode 的界面和本地样例流程可以展示，不能证明 Firebase、FastAPI、SQLite、FCM、evidence server、AI/VLM backend 在 live mode 已完成端到端工作。以下状态基于 DPP Sections 1.4, 1.6, 3.5, 3.6 and 3.7，以及当前 Flutter code 的静态检查和 Demo mode 走查。

### 7.2 Requirement coverage matrix

| DPP requirement / artefact | Current mobile status | Coverage | Remaining work or required DPP note |
|---|---|---:|---|
| Weapon alerts and evidence review | Alert Centre、detail page、weapon evidence model/UI 已有；Demo 使用静态 sample data | Partial | 需要 live backend/API、真实 evidence response、notification-to-detail 验证 |
| Violence / physical-contact bullying alerts | Alert cards、confidence、borderline/provisional label、detail flow 已有 | Partial | 需要以真实检测输出完成 integration/system test；非 physical bullying 不在 scope |
| VLM scene description | detail page 可显示 `ready`、`pending` 和 description；Demo 有静态/待处理样例 | Partial | mobile 只负责呈现；需验证 live VLM output、保存、失败和 response-time |
| VLM-assisted borderline alert setting | Admin 页面可 enable/disable 并选择 recipient roles | Partial | 需要验证 backend promotion logic、eligible-role routing、recall/precision evaluation |
| Certain / Moderate / Sensitive modes | 三种模式与 role inheritance UI/API 已有 | Partial | 目前仅有三组 fixed roles；需完成 threshold behavior 与 audit verification |
| Login / password reset / pending / disabled states | Flutter flow 与 Firebase repository 已有 | Partial | Demo 不使用 Firebase；live Firebase Auth、token refresh、pending/disabled end-to-end 仍需测试 |
| Alert feed filtering | 有 search、type 和 open filter | Partial | DPP Figure 3.25 还规划 status、threshold mode、date range、camera-location filtering；目前未全部实现 |
| Alert detail and evidence playback | metadata、VLM summary、image/video widget、action buttons 已有 | Partial | Demo evidence 大多是 placeholder/no URL；DPP 的可播放/全屏 review flow 需 live evidence 验证 |
| Dismiss / resolve / escalate with confirmation | 主要 action、reason、confirmation flow 已有 | Mostly covered | 需要 system test 验证状态同步、通知、saved log 和 duplicate-submit handling |
| Emergency response | escalation 与 simulation-only emergency call 已有 | Partial | DPP prototype 有 emergency response/one-tap page；当前调整为不打开 dialer，需在 DPP 记录 simulation-only boundary |
| Notifications / FCM | live repository 有 FCM permission、token registration、opened-message handling | Partial | Demo 不发送 FCM；需真实 Firebase project、backend send path、device delivery 和 permission test |
| Saved incident logs | Logs、status metrics、search、evidence reopening structure 已有 | Partial | 需验证所有 action、borderline records、evidence reference 与 audit trail 持久化 |
| Role management | 可编辑现有 role sensitivity | Partial | DPP Figure 3.31 明确包含 create new role；当前没有 Create role flow |
| User assignment | 可搜索、分配 role、修改 active/disabled status | Partial | DPP/deployment 还规划 user account deletion；当前 mobile UI 没有 delete-user action，且 pending default 有安全问题 |
| Discipline-team role | DPP deployment/notification flow 提及 discipline team members | Not covered in current demo | 当前 Demo 只有 School Administrator、Security Personnel、Teacher；须决定加入 role，或将其记录为 scope adjustment |
| Logout | confirmation 与 sign-out path 已有 | Covered in UI | 仍需 live token/session、notification token unregister/cleanup 验证 |
| Firebase Auth + Firestore + Cloud Storage + FCM architecture | 当前 mobile code 使用 Firebase Auth/FCM + FastAPI；repository 不直接访问 Firestore/Storage | **Adjusted, not original-DPP aligned** | 项目已经决定 operational data 改用 SQLite、evidence 改用 server/local filesystem；DPP Sections 3.5.4/3.6.1/3.8 必须相应更新，不能继续宣称 Firestore/Cloud Storage 已实现 |
| Class diagram | Dart 有 typed `User`, `Role`, `Alert`, `EvidenceRecord`, `Notification` 等 domain classes | Partial | 概念上保留 DPP domain intent；需把 SQLite/API/Firebase Auth/FCM 的边界和调整写回 DPP class/architecture description |
| Unit / integration / system / UAT / usability / security testing | 本次只有 emulator heuristic review | Not evidenced | DPP Section 3.7 的测试计划尚未由本次 review 完成；需要另行执行并记录 test cases、结果和 UAT feedback |

### 7.3 Important DPP deviations already approved in project notes

`DPP_MOBILE_ALIGNMENT.md` 已记录若干有意调整，这些调整不是本次 UI review 自动视为“已满足原 DPP”，而是需要在正式 DPP revision 中同步：

1. **Firestore / Cloud Storage → SQLite / server local filesystem。** Firebase 保留 Authentication 和 FCM；alert records、roles、settings、logs 等由 API/SQLite 提供，evidence 由受保护的 server filesystem 提供。
2. **Fixed roles for the prototype。** 当前 app 只分配既有 role，不支持 DPP Figure 3.31 的 create-role flow；必须在 DPP 中明确这是 prototype boundary，或之后补上。
3. **Simulation-only emergency call。** 为避免误拨真实紧急电话，mobile app 只记录 simulated action 并显示英文提示，不调用 Android dialer。这是安全调整，不是原型图的逐字实现。
4. **No public self-registration / first-admin bootstrap。** 新 Firebase identity 先进入 pending，由第一位 bootstrap admin 在受控流程中分配 role；这需要在 DPP authentication/admin flow 中注明。
5. **Android-first prototype。** Flutter 保留跨平台结构，但当前 review 只覆盖 Android emulator；iOS/APNs 不属于本次 prototype 验证。

### 7.4 What “fulfilled” should mean before claiming DPP completion

不应仅凭页面能够打开就声称 mobile plan 已完成。至少需要以下证据后，才可以把相应条目标记为 fulfilled：

- 每个 DPP Figure 3.23–3.34 flow 都有可重复的 step-by-step demo 或 automated/manual test record。
- live mode 能完成 Auth → API token verification → SQLite records → protected evidence → FCM notification → app open/detail 的链路。
- role/sensitivity、borderline eligibility、dismiss/resolve/escalate、saved logs 和 admin audit records 均有 integration/system test。
- create-role、user deletion、discipline-team role 等未实现项已经实现，或在 DPP revision 明确标为 approved prototype adjustment。
- 完成 DPP Section 3.7 规划的 model/VLM evaluation，以及 mobile 相关 UAT、usability 和 basic security test；本文件的 emulator review 只能作为 usability pre-review，不能替代这些测试。

## 8. Updated conclusion for approval

本 review md 现在同时记录了：

- 当前 UI/usability 问题；
- 原始 DPP 需求与当前 mobile implementation 的 coverage；
- 已批准但仍需写回 DPP 的 SQLite/local-filesystem、simulation-only、fixed-role 等调整；
- 尚未验证的 live integration 与 evaluation/testing 工作。

因此，**当前 app 适合作为 DPP-aligned prototype baseline，且本轮 19 项 UI/usability findings 已完成实现和 emulator re-review；但不能表述为已完成 DPP 的全部 mobile app plan。** create-role、user deletion、额外 filters、discipline-team role、live integration 和正式 testing/evaluation 仍按 DPP audit 保留为后续工作或需在 DPP revision 中明确的 scope adjustment。

## 9. Post-implementation UI/usability verification

**验证日期：** 2026-09-05  
**Build：** Flutter debug APK，`com.guardaid.prototype`  运行于 `Realme_GT_Neo_2_API_36_DriveD`（Android API 36，1080 × 2400，420 dpi，portrait）。

验证方式包括静态检查、`flutter analyze --no-pub`、debug APK build/install，以及 emulator Demo mode 的页面和交互走查。截图保存在本机临时目录，没有加入 repository。

| ID | 结果 | 验证摘要 |
|---|---|---|
| UI-001 | Pass | Demo card 不再显示 password；只提示本地 review 的 prefilled credentials，live mode 不显示该 card。 |
| UI-002 | Pass | Login 使用 keyboard-safe scroll/inset layout；emulator portrait 页面可完整查看 sign-in flow。 |
| UI-003 | Pass | Alert filters 改为 responsive wrap，`All`、`Weapon`、`Violence / Bullying`、`Open` 均完整可见；`More filters` 另提供 status、threshold mode、camera source 和 date range。 |
| UI-004 | Pass | 页面列表加入 bottom-navigation 与 safe-area padding；bottom bar 不透明，末端内容可继续滚动查看。 |
| UI-005 | Pass | 日期统一为例如 `5 Sep 2026, 03:53`，消除日/月歧义。 |
| UI-006 | Pass | Alert Centre 移除重复的 `Open alert` action；状态 pill 保留为状态显示。 |
| UI-007 | Pass | Alert detail metadata 显示 `Threshold mode: Moderate`，label/value 分隔清楚。 |
| UI-008 | Pass | Evidence 显示 media type、demo/unavailable 状态和 playback 条件；已有 loading/error path，并补上 semantics。 |
| UI-009 | Pass | Escalate/emergency actions 有 confirmation、提交中状态和 disabled guard；emergency 仍为 simulation-only。 |
| UI-010 | Pass | `Mark all as read` 使用明确 button/icon、confirmation/snackbar；通知按事件类型使用不同 icon/color。 |
| UI-011 | Pass | Incident Logs 使用 `View saved record`，不再把 resolved record 写成 `Open alert`；统计卡以 passive semantics 呈现。 |
| UI-012 | Pass | Settings 的 Administration/Application sections 在 emulator scroll 时不再被 header 错误裁切。 |
| UI-013 | Pass with scope note | 页面明确说明 prototype 使用 fixed roles，role create/delete 尚未启用；这不是 DPP create-role 功能已完成的声明。 |
| UI-014 | Pass | sensitivity 保存前显示 role、旧/新 mode 和受影响用户数量，并要求确认。 |
| UI-015 | Pass | pending user 默认是 `No role assigned` + `Pending approval`，未选择 role 时不能 review/save。 |
| UI-016 | Pass | assignment dropdown 显示 role+sensitivity；提供 Cancel、Review changes 和保存前 summary confirmation。 |
| UI-017 | Pass | Borderline setting 显示 Enabled/Disabled 和 alert-volume impact；保存有 progress/success/error feedback。 |
| UI-018 | Pass for reviewed profile | Icon buttons 使用至少 48dp hit target；cards/action controls 有 semantics/tooltip。当前证据覆盖 reviewed Android profile，font-scale 1.3–2.0 和更多设备仍建议在正式 accessibility test 中补测。 |
| UI-019 | Pass | root Android Back 显示 `Leave GuardAId?` confirmation；只有 `Exit app` 才退出。 |

### Verification limitations

- 本次验证仍是 Demo mode；Firebase Auth、FastAPI、SQLite、FCM、真实 evidence server 和 live VLM/backend 端到端链路不在本轮证明范围内。
- `flutter analyze --no-pub` 通过，debug APK 成功 build/install。Login widget test 通过；main mobile-shell overflow test 在本机测试 harness 中未自行结束，因此没有把它报告为 automated pass，emulator 走查结果作为该项的主要证据。
- 本节验证 UI/usability implementation，不替代 DPP Section 3.7 所要求的 integration、system、UAT、security 和 model/VLM evaluation。
