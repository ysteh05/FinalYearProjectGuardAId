# Web / Mobile契约及改写范围清单

本清单静态读取main.py的全部HTTP路由以及Dart GuardAidRepository接口，未启动应用。每项有目标职责及必须保留的行为。表中direct repository calls只列该路由直接调用，不代表展开全部helper调用；不能把清单当作已通过回归。

## 当前HTTP路由：44个（含首页）

| 路由 | 当前实现 / direct repository calls | 目标与验收重点 |
|---|---|---|
| `GET /api/health` | [health](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:116)；`service/helper` | schema_migrations/model_versions/config_versions + runtime服务。保留readiness/worker状态和schema真实版本；不能伪造已执行升级。 |
| `GET /api/runtime` | [runtime_status](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:120)；`schema_version` | schema_migrations/model_versions/config_versions + runtime服务。保留readiness/worker状态和schema真实版本；不能伪造已执行升级。 |
| `GET /api/cameras` | [list_cameras](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:150)；`list_cameras` | locations/cameras/camera_sources/media_files。身份、当前来源、替换/删除、视频/帧预览、文件访问及错误码保留。 |
| `POST /api/cameras` | [create_camera](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:157)；`save_camera` | locations/cameras/camera_sources/media_files。身份、当前来源、替换/删除、视频/帧预览、文件访问及错误码保留。 |
| `PATCH /api/cameras/{camera_id}` | [update_camera](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:187)；`get_camera, save_camera` | locations/cameras/camera_sources/media_files。身份、当前来源、替换/删除、视频/帧预览、文件访问及错误码保留。 |
| `PUT /api/cameras/{camera_id}/video` | [replace_camera_video](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:198)；`get_camera, remove_inactive_camera_sources, save_camera` | locations/cameras/camera_sources/media_files。身份、当前来源、替换/删除、视频/帧预览、文件访问及错误码保留。 |
| `DELETE /api/cameras/{camera_id}` | [delete_camera](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:245)；`delete_camera` | locations/cameras/camera_sources/media_files。身份、当前来源、替换/删除、视频/帧预览、文件访问及错误码保留。 |
| `GET /api/cameras/{camera_id}/media` | [camera_media](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:260)；`get_camera` | locations/cameras/camera_sources/media_files。身份、当前来源、替换/删除、视频/帧预览、文件访问及错误码保留。 |
| `GET /api/cameras/{camera_id}/frame` | [camera_frame](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:271)；`get_camera` | locations/cameras/camera_sources/media_files。身份、当前来源、替换/删除、视频/帧预览、文件访问及错误码保留。 |
| `GET /api/alerts` | [list_alerts](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:313)；`list_alerts, list_borderline_review_summaries` | alerts/detection_events/reviews/evidence/descriptions/alert_actions/alert_reads/phases + identity JOIN。旧status派生、每次版本/分数/窗口、最新描述和完整历史保持；Active有角色可看全部警报。 |
| `GET /api/evidence/{evidence_id}/media` | [evidence_media](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:355)；`list_alerts` | evidence_records/media_files/alerts + 媒体服务。Mobile沿用active+role授权；视频/截图响应、路径containment及到期清理保留。 |
| `GET /api/settings` | [get_settings](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:374)；`get_settings` | config_versions/model_versions/prompt_versions。完整配置按版本单行保存；DTO字段不改，master switch仍独立。 |
| `PUT /api/settings` | [save_settings](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:378)；`cancel_queued_borderline_review_tasks, get_settings, save_settings` | config_versions/model_versions/prompt_versions。完整配置按版本单行保存；DTO字段不改，master switch仍独立。 |
| `POST /api/simulations/{camera_id}/plan` | [plan_simulation](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:413)；`get_camera, get_settings` | 现有SimulationPlanner/InferenceOrchestrator；持久结果写alerts/detection_events/reviews/evidence/descriptions。进度、ETA、取消、debug由当前内存服务负责；API及运行行为保留。 |
| `POST /api/analysis/{camera_id}` | [start_analysis](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:420)；`service/helper` | 现有SimulationPlanner/InferenceOrchestrator；持久结果写alerts/detection_events/reviews/evidence/descriptions。进度、ETA、取消、debug由当前内存服务负责；API及运行行为保留。 |
| `GET /api/analysis` | [list_analysis_jobs](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:433)；`service/helper` | 现有SimulationPlanner/InferenceOrchestrator；持久结果写alerts/detection_events/reviews/evidence/descriptions。进度、ETA、取消、debug由当前内存服务负责；API及运行行为保留。 |
| `GET /api/analysis/{job_id}` | [get_analysis_job](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:437)；`service/helper` | 现有SimulationPlanner/InferenceOrchestrator；持久结果写alerts/detection_events/reviews/evidence/descriptions。进度、ETA、取消、debug由当前内存服务负责；API及运行行为保留。 |
| `DELETE /api/analysis/{job_id}` | [cancel_analysis_job](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:444)；`service/helper` | 现有SimulationPlanner/InferenceOrchestrator；持久结果写alerts/detection_events/reviews/evidence/descriptions。进度、ETA、取消、debug由当前内存服务负责；API及运行行为保留。 |
| `GET /api/mobile/session` | [mobile_session](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:722)；`borderline_user_enabled, get_settings` | users/roles/user_roles + Firebase验证。Firebase UID唯一；provider缓存完整；pending/disabled状态及权限返回保留。 |
| `PUT /api/mobile/device` | [register_mobile_device](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:764)；`register_device` | devices。token唯一、可转移当前owner、启用/lastSeen及发送前校验保留。 |
| `GET /api/mobile/alerts` | [list_mobile_alerts](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:780)；`list_alerts, list_read_alert_ids` | alerts/detection_events/reviews/evidence/descriptions/alert_actions/alert_reads/phases + identity JOIN。旧status派生、每次版本/分数/窗口、最新描述和完整历史保持；Active有角色可看全部警报。 |
| `GET /api/mobile/alerts/{alert_id}` | [get_mobile_alert](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:806)；`list_alerts, list_read_alert_ids` | alerts/detection_events/reviews/evidence/descriptions/alert_actions/alert_reads/phases + identity JOIN。旧status派生、每次版本/分数/窗口、最新描述和完整历史保持；Active有角色可看全部警报。 |
| `POST /api/mobile/alerts/{alert_id}/read` | [mark_mobile_alert_read](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:822)；`mark_mobile_alert_read` | alert_reads/alerts。首次已读唯一、重复read幂等；read不停止提醒。 |
| `GET /api/mobile/alerts/{alert_id}/broadcast-preview` | [preview_mobile_alert_broadcast](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:832)；`list_alerts, mobile_alert_broadcast_preview` | alerts/roles/user_roles + 当前授权。沿用可选角色、有效用户计数及权限预览，提交时重验，preview不写phase。 |
| `POST /api/mobile/alerts/{alert_id}/actions` | [submit_mobile_alert_action](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:846)；`get_settings, list_alerts, list_read_alert_ids, record_mobile_workflow_action` | alerts/alert_actions/phases/phase_roles/notifications/outbox/recipients/audit_logs。全部旧/新动作、revision409、key幂等、同事务Phase A/B转换及模拟通话保留。 |
| `POST /api/mobile/alerts/{alert_id}/simulate-emergency-call` | [simulate_mobile_emergency_call](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:899)；`get_settings, record_mobile_workflow_action` | alerts/alert_actions/phases/phase_roles/notifications/outbox/recipients/audit_logs。全部旧/新动作、revision409、key幂等、同事务Phase A/B转换及模拟通话保留。 |
| `GET /api/mobile/evidence/{evidence_id}/media` | [mobile_evidence_media](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:931)；`list_alerts` | evidence_records/media_files/alerts + 媒体服务。Mobile沿用active+role授权；视频/截图响应、路径containment及到期清理保留。 |
| `GET /api/mobile/notifications` | [list_mobile_notifications](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:956)；`list_mobile_notifications` | notifications/notification_recipients/notification_reads。feed按feed_visible及显式visibility查询；mark-all-read只更新本用户，read不停止phase。 |
| `POST /api/mobile/notifications/mark-all-read` | [mark_mobile_notifications_read](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:974)；`mark_mobile_notifications_read` | notifications/notification_recipients/notification_reads。feed按feed_visible及显式visibility查询；mark-all-read只更新本用户，read不停止phase。 |
| `GET /api/mobile/admin/roles` | [list_mobile_roles](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:982)；`list_mobile_roles` | roles/user_roles/users/audit_logs；删除时phase_roles/recipients关联清理。系统角色/最后管理员保护、授予权限上限、sensitivity用途保留；restricted空名单不变broadcast。 |
| `GET /api/mobile/roles` | [list_mobile_recipient_roles](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1007)；`list_mobile_roles` | roles/user_roles/users/audit_logs；删除时phase_roles/recipients关联清理。系统角色/最后管理员保护、授予权限上限、sensitivity用途保留；restricted空名单不变broadcast。 |
| `PATCH /api/mobile/admin/roles/{role_id}` | [update_mobile_role](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1027)；`update_mobile_role, user_has_permission` | roles/user_roles/users/audit_logs；删除时phase_roles/recipients关联清理。系统角色/最后管理员保护、授予权限上限、sensitivity用途保留；restricted空名单不变broadcast。 |
| `DELETE /api/mobile/admin/roles/{role_id}` | [delete_mobile_role](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1060)；`delete_mobile_role` | roles/user_roles/users/audit_logs；删除时phase_roles/recipients关联清理。系统角色/最后管理员保护、授予权限上限、sensitivity用途保留；restricted空名单不变broadcast。 |
| `POST /api/mobile/admin/roles` | [create_mobile_role](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1075)；`create_mobile_role, user_has_permission` | roles/user_roles/users/audit_logs；删除时phase_roles/recipients关联清理。系统角色/最后管理员保护、授予权限上限、sensitivity用途保留；restricted空名单不变broadcast。 |
| `GET /api/mobile/admin/users` | [list_mobile_users](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1117)；`list_mobile_users` | users/user_roles/roles/devices/audit_logs + Firebase Admin。创建/关联、默认个人偏好、停用撤销设备、primary role、资料更新、邮件流程与权限保留。 |
| `POST /api/mobile/admin/users` | [create_mobile_user](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1140)；`assert_mobile_user_role_assignable, create_mobile_user, list_mobile_users, mobile_user_by_email` | users/user_roles/roles/devices/audit_logs + Firebase Admin。创建/关联、默认个人偏好、停用撤销设备、primary role、资料更新、邮件流程与权限保留。 |
| `PATCH /api/mobile/admin/users/{user_id}` | [update_mobile_user](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1254)；`assign_mobile_user, revoke_mobile_user` | users/user_roles/roles/devices/audit_logs + Firebase Admin。创建/关联、默认个人偏好、停用撤销设备、primary role、资料更新、邮件流程与权限保留。 |
| `POST /api/mobile/admin/users/{user_id}/deactivate` | [deactivate_mobile_user](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1275)；`deactivate_mobile_user` | users/user_roles/roles/devices/audit_logs + Firebase Admin。创建/关联、默认个人偏好、停用撤销设备、primary role、资料更新、邮件流程与权限保留。 |
| `PATCH /api/mobile/profile` | [update_mobile_profile](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1289)；`update_mobile_profile` | users/user_roles/roles/devices/audit_logs + Firebase Admin。创建/关联、默认个人偏好、停用撤销设备、primary role、资料更新、邮件流程与权限保留。 |
| `GET /api/mobile/admin/borderline-settings` | [get_mobile_borderline_settings](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1304)；`get_borderline_settings_revision, get_mobile_settings, get_settings, list_borderline_role_settings` | users/roles/mobile_settings/config_versions/audit_logs。角色整组覆盖/个人覆盖、全局revision CAS/409与权限检查保留。 |
| `PUT /api/mobile/admin/borderline-settings/roles/{role_id}` | [update_mobile_borderline_role](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1323)；`set_borderline_role_enabled` | users/roles/mobile_settings/config_versions/audit_logs。角色整组覆盖/个人覆盖、全局revision CAS/409与权限检查保留。 |
| `PUT /api/mobile/admin/borderline-settings/users/{user_id}` | [update_mobile_borderline_user](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1343)；`set_borderline_user_enabled` | users/roles/mobile_settings/config_versions/audit_logs。角色整组覆盖/个人覆盖、全局revision CAS/409与权限检查保留。 |
| `PUT /api/mobile/admin/borderline-settings` | [save_mobile_borderline_settings](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1363)；`get_borderline_settings_revision, has_individual_borderline_settings, set_borderline_roles_enabled` | users/roles/mobile_settings/config_versions/audit_logs。角色整组覆盖/个人覆盖、全局revision CAS/409与权限检查保留。 |
| `GET /` | [index](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/guardaid_monitor/app/main.py:1399)；`service/helper` | 现有静态Web资源。保留首页及静态资源挂载。 |

## Mobile repository契约：30项

| 接口成员 | 当前依赖 | 保留点 |
|---|---|---|
| [isDemo](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:13) | `现有repository模式` | demo与Firebase实现继续分别提供。 |
| [openedAlertIds](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:14) | `现有FCM打开消息流` | 公开alert ID及深链接保持。 |
| [takeInitialAlertId](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:15) | `现有FCM初始消息` | 首次打开alert ID保持；不增加DB表。 |
| [signIn](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:16) | `Firebase认证 + /api/mobile/session` | 账号/权限/status返回保持；认证密码/token不存SQLite。 |
| [refreshSession](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:17) | `/api/mobile/session` | 保留当前session刷新/停用处理。 |
| [sendPasswordReset](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:18) | `Firebase Auth` | 外部认证邮件流程保留。 |
| [signOut](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:19) | `Firebase Auth + devices` | 保留退出及当前设备状态处理；旧delivery归属不改。 |
| [loadAlerts](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:20) | `/api/mobile/alerts` | alert DTO、派生status与广泛警报浏览授权保持。 |
| [loadAlertDetails](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:21) | `/api/mobile/alerts/{alert_id}` | 全部证据、检测统计、描述、动作、phase及revision保持。 |
| [markAlertRead](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:22) | `/api/mobile/alerts/{alert_id}/read` | alert_reads独立，不停止phase。 |
| [loadNotifications](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:23) | `/api/mobile/notifications` | 通知feed与已读保持，显式visibility处理角色删除。 |
| [loadRoles](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:24) | `/api/mobile/roles` | 可见角色及当前权限边界保持。 |
| [loadUsers](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:25) | `/api/mobile/admin/users` | 用户管理权限、排序/DTO保持。 |
| [submitAlertAction](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:26) | `/api/mobile/alerts/{alert_id}/actions` | legacy入口、note、响应历史和状态保持。 |
| [loadBroadcastRoleOptions](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:32) | `/api/mobile/alerts/{alert_id}/broadcast-preview` | 候选角色及有效人数保持，提交重新验证。 |
| [submitWorkflowAction](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:33) | `/api/mobile/alerts/{alert_id}/actions` | 完整状态机、key、expectedRevision及409保持。 |
| [recordSimulatedEmergencyCall](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:41) | `/api/mobile/alerts/{alert_id}/simulate-emergency-call` | 只模拟；历史显示/去重契约保持。 |
| [markAllNotificationsRead](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:42) | `/api/mobile/notifications/mark-all-read` | notification_reads独立且用户隔离。 |
| [updateRoleSensitivity](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:43) | `/api/mobile/admin/roles/{role_id}` | 仅普通推送资格，不收窄警报浏览。 |
| [createRole](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:44) | `/api/mobile/admin/roles` | 角色权限/命名/default偏好、审计保持。 |
| [deleteRole](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:50) | `/api/mobile/admin/roles/{role_id}` | 最后管理员/系统角色保护；历史snapshot保留。 |
| [updateUserAssignment](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:51) | `/api/mobile/admin/users/{user_id}` | primary role UI及DB M:N语义、状态和默认偏好保持。 |
| [createUser](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:56) | `/api/mobile/admin/users` | Firebase创建/关联、本地映射及错误码保持。 |
| [sendUserSetupEmail](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:62) | `Firebase Auth邮件流程` | 外部邮件和失败显示保持，不需要新增临时DB实体。 |
| [deactivateUser](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:63) | `/api/mobile/admin/users/{user_id}/deactivate` | 停用/撤销device、权限保护和审计保持。 |
| [updateProfile](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:64) | `/api/mobile/profile` | 本地编辑长度与provider同步缓存区别处理。 |
| [updateBorderlineSettings](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:65) | `/api/mobile/admin/borderline-settings` | 旧全局/角色集合兼容入口保持。 |
| [loadBorderlineSettings](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:69) | `/api/mobile/admin/borderline-settings` | Web master与账号/角色偏好及revision明确分开。 |
| [setBorderlineRoleEnabled](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:70) | `/api/mobile/admin/borderline-settings/roles/{role_id}` | 整组覆盖及revision409保持。 |
| [setBorderlineUserEnabled](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/mobileapp/lib/src/repository.dart:75) | `/api/mobile/admin/borderline-settings/users/{user_id}` | 个人覆盖/来源及revision409保持。 |

## 改写必须成组完成

| 代码区域 | 必要适配 |
|---|---|
| SQLiteRepository配置/身份/偏好 | 五配置JOIN改单行快照；角色/个人偏好一对一JOIN改主表字段；global revision CAS仍同事务。 |
| SQLiteRepository警报读取/动作 | workflow JOIN改alert字段，旧status派生；动作双写结束；legacy展示去重与新key/revision事务保持。 |
| Orchestrator复核 | task输入、claim、retry、terminal/supplemental持久化同一review；结束legacy结果mirror写入，audit_only不进入worker。 |
| 通知outbox worker | JOIN message获取canonical payload；queued_target名单、send-time owner校验、逐设备结果单写；保留FCM错误分类与退避/lease恢复。 |
| escalation worker | 新phases/dispatches/audience SQL；Phase B live角色FK；保留无消息stage、late-repeat及过期替代语义，结束旧plan双写。 |
| main.py DTO/权限 | DTO保持，visibility_policy显式查询；补齐必需模型/量化/context非空验证；警报与通知可见规则分别保持。 |
| Flutter FirebaseApiRepository/Models/Demo | 后端DTO保持可减少改动，仍逐成员回归；deep links/notification payload/409/枚举兼容；demo不新增SQLite。 |
| migrations/runtime health/media | 真正新schema ledger、无损备份/迁移/回退；filesystem处理仍由媒体服务负责；不得造假metadata或升级记录。 |

实施验收需覆盖响应字段、排序/分页、错误码、访问权限和写入副作用。DB结构可以表示全部已识别功能，但在repository/worker适配完成前，不能直接启用本DDL。
