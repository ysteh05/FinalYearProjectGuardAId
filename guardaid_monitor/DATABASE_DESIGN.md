# GuardAId Normalized SQLite Database Design

更新日期：2026-10-11

## 1. Design objective

SQLite 是当前 operational prototype 的 structured source of truth。媒体保存在 server filesystem；Firebase 只提供 Authentication 与 Cloud Messaging。Schema 采用第三范式为主，UI 所需的组合字段由 SQL join/aggregate 产生，而不是在多张 table 重复保存。

当前 database file：

```text
runtime_data/database/guardaid.db
```

当前媒体根目录：

```text
runtime_data/media/
├── camera_sources/<camera-id>/...
└── evidence/YYYY/MM/<alert-id>/...
```

Database 只保存以上 media root 之下的 POSIX-style relative path；不得保存 drive letter、用户目录或 `..`。

## 2. Identity and authorisation

### `users`

| Column | Constraint | Purpose |
|---|---|---|
| `id` | PK | GuardAId internal user ID |
| `firebase_uid` | UNIQUE, NOT NULL | Firebase Auth external identity |
| `email_cache` | nullable | Display/audit cache，不用于验证密码 |
| `display_name` | nullable | GuardAId display label |
| `status` | CHECK | pending / active / disabled |
| timestamps | NOT NULL | lifecycle |

Firebase password、password hash、refresh token 和 provider credential 不进入 SQLite。手机把 Firebase ID token 送给 FastAPI；backend 验证后取得 `uid`，再以 `users.firebase_uid` 查业务角色。

### `roles` and `user_roles`

`roles` 保存 DPP prototype 的 School Administrator、Security Personnel、Teacher，并保存 inherited `sensitivity_mode`（sensitive / moderate / certain）。`user_roles` 实现 users 与 roles 的 normalized relation；当前 mobile UI 每位 user 分配一个 primary role，schema 仍允许未来扩展。角色和 sensitivity 只能由 authorised administrator API 修改，不能放在 client-controlled profile field。

自2026-10-07起，所有已认证、Active且有有效Role的用户均可查看全部警报、详情及对应证据；不要求用户拥有FCM设备，也不要求曾收到该警报通知。Role sensitivity仅控制通知收件资格，不再限制警报可见范围。通知读取状态与警报浏览状态仍按用户独立保存。Pending、Disabled或无Role用户不能访问受保护的警报及证据API。

### `devices`

一位 user 可有多台手机。每个 record 保存 platform、device label、FCM registration/Firebase installation identifier、last seen、enabled 与 timestamps。Token 必须 unique；FCM 回报 unregistered 后停用。

## 3. Camera and media

### `locations`

Location text 只存一次。`normalized_name` unique，用于避免 `Block A Level 2` 因大小写或空格产生重复。

### `cameras`

保存 camera identity、`location_id`、enabled、status 与 timestamps。Camera 不直接保存 filename、content type、duration 或路径。

### `media_files`

所有 physical file 的共同 metadata：relative path、original filename、media kind、MIME、bytes、SHA-256、created time。一个 physical file 只有一个 record。

### `camera_sources`

把 camera 与当前 uploaded video/未来 RTSP source 连接。Uploaded source 引用 `media_file_id`；RTSP source 只保存不含 password 的 endpoint reference 或 secret reference。`is_active` 支持替换 source 而保留历史。

## 4. Reproducible model policy

### `model_versions`

保存 task、model name、version、framework、quantisation 与 checkpoint reference。Checkpoint reference 是 server-controlled logical reference，不是用户电脑绝对路径。

### `prompt_versions`

保存 task、version、SHA-256 与 frozen content。`borderline_binary_prompt_v1` 与
`scene_description_prompt_v2` 分开；历史 `scene_description_prompt_v1` 记录只保留作审计，
读取路径仍兼容其旧 scene-description 数据。

### Versioned configuration

`config_versions` 是 immutable header；`violence_configs`、`weapon_configs`、`vlm_configs` 与 `alert_notification_escalation_configs` 与它一对一。每次保存 Config UI 都新增 version，不更新旧 version。Alert/detection 引用当时 version，因此以后可以重现决定。`vlm_configs.scene_description_frames` 独立保存 scene description 的固定均匀帧预算（默认20）；它不取代或改变 borderline review 使用的 `input_fps`。

`alert_notification_escalation_configs` 保存 unanswered alert 的两阶段通知时长。默认第一次重发为首次通知后60秒，第二阶段全体升级为首次通知后120秒；两项作为同一份不可变配置版本保存。Web Config 以分钟呈现，后端以秒校验和保存，且要求第二阶段晚于第一阶段。

## 5. Alert lifecycle

### `alerts`

保存 stable incident record：camera、incident type、workflow summary、legacy sensitivity label、Borderline promotion flag、opening detector/prompt/config references 及 timestamps。新流程不会用 `sensitivity_mode` 决定警报是否入库或谁能查看；达到最低检测门槛并通过相应确认后，Web 与 Mobile 都记录／展示该警报。角色 sensitivity 仅用于普通手机通知。`is_provisional` 与旧行保留作兼容读取，不再用于新流程的提前告警或通知；VLM 复核状态另由 `borderline_review_status` 表达，负面结果不会删除警报或改动人工处理状态。

以下 API 字段不作为 base columns 重复保存：

```text
cameraName        <- cameras.name
location          <- locations.name
firstDetectedAt   <- MIN(detection_events.detected_at)
lastDetectedAt    <- MAX(detection_events.detected_at)
maxConfidence     <- MAX(detection_events.confidence)
detectionCount    <- COUNT(detection_events.id)
sceneDescription  <- latest ready vlm_descriptions
```

### `detection_events`

每次被合并进 alert 的真实 detector/VLM-confirmed event 各占一行，保存 confidence、segment time、detected time、decision source、model 和 config。同一 camera/type 的 source-time 区间相接、重叠或间隔不超过 `incident_merge_gap_seconds` 时，追加到同一 alert；当前暴力／身体霸凌和武器配置均为 15 秒。这个合并间隔按视频 source time 计算，连续检测可滚动延长同一个事件。

新流程在每次检测新建或合并进 alert 后，使用该检测事件保存的分数、类别分数、资格结果及配置版本重新计算普通通知接收者；只为尚未收到该 alert 首次通知的账号排队。事件合并与账号／阶段幂等负责防重，不用 camera/type cooldown 丢弃新的 alert 或新加入的账号。SQLite 中每个检测事件保留其自身来源时间、模型／配置版本与 qualification，不以 incident 聚合后的最高分伪装成逐次证据。

### `borderline_review_tasks` and legacy `borderline_reviews`

`borderline_review_tasks` 是 operational review 的持久化事实来源：唯一 detection-window key、alert、片段路径、状态、尝试次数、结论、错误类别与 model/prompt/config snapshot。任务先入库再交给单一 VLM worker；重启时继续可重试的任务。Web master switch 开启后，每个落在 `[最低 violence threshold, 最高 threshold)` 的窗口均进入复核，是否存在接收账号不影响任务创建。有效正面结果取得补充通知资格；负面、解析错误及调用失败不会排队补充 beep。`supplemental_notification_status` 用于限制历史结果重放，并在关闭 master switch 时原子抑制尚未处理的补充通知。旧 `borderline_reviews` 表继续保留审计／兼容记录。

`borderline_role_settings` 保存管理者执行整组覆盖操作时使用的角色默认值；`borderline_user_settings` 保存账号当前有效开关及来源。个别修改覆盖默认值，之后的角色整组操作会再次覆盖该角色现有成员。新成员按目标角色默认值初始化。Web 全局开关与每账号接收设置分别保存。

### `evidence_records`

把 alert 与一个 `media_files` record 相连，另存 evidence type、source time range、captured time 与 retention/delete-after time。Violence/physical bullying 通常是 clip；weapon 通常是 screenshot。

### `vlm_descriptions`

保存 description job 状态、model/prompt、structured JSON、display text、error 与 timestamps。允许同一 alert 重试或重新生成；Alert API 选择最新成功版本。

## 6. Notification and audit

### `notifications`

每次 alert notification intent 只保存一次最小 payload 与 created time。

### `notification_deliveries`

每个 notification-device pair 保存独立 delivery state、provider message ID、attempt count、last attempt 与 error code。这样相同 payload 不会因为五台手机而复制五次。

### `notification_recipients` and `notification_reads`

`notification_recipients` 以 role 或 specific user 表示一条 notification 的 authorised audience；没有 recipient rows 的旧 notification 视为原有 broadcast。`notification_reads` 只保存 notification-user pair 与 read time，避免在 shared notification row 重复建立 per-user copy。

### Unanswered-alert reminder schedule and dispatch — schema v10 legacy

Schema v10 originally added `alert_notification_escalation_configs`,
`alert_notification_escalation_schedules`, and
`alert_notification_escalation_dispatches`. These tables and rows are retained for
historical compatibility; new dispatch processing uses the normalized v11 phase
and outbox tables below.

### Mobile response workflow and durable notification outbox — schema v11

Migration 011 adds:

| Table | Purpose |
|---|---|
| `mobile_alert_workflows` | One current workflow state per alert, including handler, selected Broadcast Roles, active reminder phase and revision. Existing `alerts.status` remains as a compatible summary status. |
| `mobile_alert_workflow_events` | Idempotent actor-attributed response events and optional comments; legacy `alert_actions` are not rewritten. |
| `mobile_alert_reminder_phases` | A persisted Phase A (`awaiting_decision`) or Phase B (`awaiting_handler`) timing snapshot per alert. |
| `mobile_alert_reminder_dispatches` | Unique stage 1/stage 2 claim and outcome per phase; overdue stage 2 supersedes a not-yet-sent stage 1. |
| `mobile_notification_outbox` | Durable notification content, channel, idempotency key, data payload and retry state. |
| `mobile_notification_outbox_recipients` | Per-user target snapshot for an outbox notification. |
| `mobile_notification_outbox_deliveries` | Per-device sent/failed result, retry count and provider message ID. |

All authenticated Active users with a valid role can view every alert and its
evidence from the outset; access does not depend on sensitivity or FCM. For
Phase A initial notification recipients are the union of accounts qualified by
their actual role thresholds and accounts added after a valid Borderline-positive
result. The durable `alert_notification_audience` table records each account's
qualification reasons and first-notice state; first-repeat recipients use that
actual audience. Phase A stage 2 includes all currently Active
role-bearing users with enabled devices. For Phase B, the Confirm-only initial
Broadcast and stage 1 use the selected roles without an extra sensitivity filter;
stage 2 includes all currently Active role-bearing users with enabled devices.
The audience is revalidated at queue/delivery time, and these push recipients are
not access-control grants.

The workflow's integer `revision` is a concurrency token. A Mobile App action
includes the revision it displayed; inside the same write transaction, the API
rejects a new action with HTTP 409 if the stored revision has since changed.
The App then reloads the current alert so the user can review it before retrying.
Idempotency-key lookup occurs first, allowing a retry of an already committed
request to return its original no-op result without writing duplicate events or
dispatches.

The Phase A clock starts with the initial alert-notification attempt. Confirming
and opening Phase B starts a new phase clock. Dismissal, confirmation-and-handle,
taking ownership, or resolving cancels/completes the active phase. Reading, adding
a comment, or recording an in-app simulated call does not stop the phase. Defaults
are 60 seconds and 120 seconds from the current phase anchor, saved with that
phase; the second delay must be later than the first. If the backend was offline
past both deadlines, only the highest due stage is queued.

The initial urgent channel is two beeps; stage 1 uses
`guardaid_urgent_repeat_v1` (three beeps); stage 2 uses
`guardaid_urgent_escalated_v1` (four beeps). Routine decision/ownership updates
use `guardaid_updates_v1`. Android channel sounds are installed by the Mobile App,
not edited by the dashboard. The outbox guarantees transactional persistence and
bounded backend retry; FCM acceptance is not proof that the operating system
displayed or the user heard a notification. Before an initial push, the outbox
rechecks current account qualification, Borderline availability and the alert
workflow status/revision so a queued push from a superseded workflow is not sent.

### `alert_actions`

保存历史 authorised user 对 alert 的 acknowledge、review、dismiss、resolve、escalate、`simulated_emergency_call` 与 note；v11不覆盖或改写这些历史行。新的确认/接手/解决流程写入 `mobile_alert_workflow_events`。Emergency simulation 明确记录没有打开 dialler、没有拨出真实电话。

### `mobile_settings`

保留旧版 Mobile Borderline 设置接口所需的兼容字段，以及不可关闭的
`emergency_call_simulation_only=1` safety invariant。旧 role ID JSON 只作兼容读写；
新版管理界面与通知策略以 `borderline_role_settings`、`borderline_user_settings`
为账号接收资格来源，以 `vlm_configs.borderline_review_enabled` 为 Web master switch。
设置变更仍写入 `audit_logs`，并使用独立 revision 防止旧页面覆盖较新的个别例外。

### `audit_logs`

保存 security/config/admin 操作。敏感 token、service-account 内容、完整 evidence 和密码不得进入 audit detail。

## 7. Key cardinalities

```text
locations       1 ── * cameras
cameras         1 ── * camera_sources
media_files     1 ── 0..1 camera_sources
users           * ── * roles          via user_roles
users           1 ── * devices
cameras         1 ── * alerts
alerts          1 ── * detection_events
alerts          1 ── * evidence_records
media_files     1 ── 0..1 evidence_records
alerts          1 ── * vlm_descriptions
alerts          1 ── * notifications
notifications   1 ── * notification_deliveries
devices         1 ── * notification_deliveries
notifications   1 ── * notification_recipients
notifications   * ── * users          via notification_reads
alerts          1 ── * alert_actions
config_versions 1 ── 1 violence_configs
config_versions 1 ── 1 weapon_configs
config_versions 1 ── 1 vlm_configs
config_versions 1 ── 1 alert_notification_escalation_configs
alerts          1 ── 0..1 alert_notification_escalation_schedules
alerts          1 ── 0..2 alert_notification_escalation_dispatches
```

## 8. Deliberate derived DTO fields

FastAPI 可返回一个方便 UI 的 flattened `Camera` 或 `Alert` JSON，但这些是 query DTO，不表示 database 也应 flattened。例如 Alert API 可以同时返回 camera name、max confidence、description 与 evidence list；repository 负责 joins/aggregates。

唯一允许的 controlled redundancy 是为了安全审计保留不可变 historical snapshot，例如 notification payload 与 action detail。它们表示当时实际发送/执行的内容，而不是当前 master data 的复制品。

## 9. SQLite operational rules

- `PRAGMA foreign_keys=ON`
- `PRAGMA journal_mode=WAL`
- `PRAGMA busy_timeout=5000`
- 所有 multi-table write 使用 transaction
- Database 和 media 一起备份，且记录一致的 backup timestamp
- 不在 network share 上由多台 server 同时打开同一个 SQLite file
- 未来 scale-out 时迁移 PostgreSQL，保留 PK/FK/service interface

Implementation schema 由 `migrations/001_initial.sql` 至 `014_borderline_notification_eligibility.sql` 顺序建立；当前 schema version 为 14。Version 8 adds detection debug mode; version 9 adds per-user alert viewing records; version 10 adds the legacy unanswered-alert schedule; version 11 adds response workflow phases and durable notification outbox; version 12 adds detection qualifications, Borderline tasks and persisted audiences; version 13 separates evidence status; version 14 makes positive-review supplemental notification eligibility durable and replay-safe. Migrations are applied automatically when the backend starts.

当前的 `users`、`user_roles`、`devices` 与 `audit_logs` 已足以支持 Mobile App 管理员新增/关联 Firebase 用户、分配一个 role、停用/重新启用账号及留存审计，因此该功能不新增 migration。Firebase Auth identity 由 server-side Admin SDK 建立；SQLite transaction 负责 GuardAId 用户、单一 role 和 audit 记录。停用时保留用户与 role、将状态改为 `disabled` 并关闭已登记 FCM device；重新启用后，用户的 App session refresh 会重新登记 device。

## 10. Per-user alert viewing — 2026-10-02

`alert_reads` records whether an individual operator has opened Alert Review.

| Column | Constraint | Purpose |
|---|---|---|
| `user_id` | FK → users, composite PK, cascade on deletion | Operator who viewed the alert |
| `alert_id` | FK → alerts, composite PK, cascade on deletion | Viewed incident |
| `read_at` | NOT NULL | First successful view-record time |

The `(user_id, alert_id)` primary key makes repeat viewing idempotent. Viewing does
not change incident status, create a decision, send a notification, or change
`notification_reads`. Existing alerts start as unviewed because previous viewing
history was not recorded. The protected `POST /api/mobile/alerts/{id}/read` applies
the same active-account and valid-role checks as Alert Review. Sensitivity controls notifications only. Mobile list
and detail responses expose a derived, user-specific `isRead` field.
