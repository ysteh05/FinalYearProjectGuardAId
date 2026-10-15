# 31表数据库设计：易懂说明

这里说明最新31表重构方案。Entity是一类要记录的对象或关系，通常对应一张表；一行是一条记录；attribute是字段。PK是唯一身份，FK是指向另一表记录的编号。

Borderline表示检测结果处于边界、需要AI进一步检查；VLM是理解画面和文字的模型。JSON是保存在一个字段里的结构化内容。字段名含_id不自动代表有FK，以下外键列表才是实际关系。

本文逐项解释全部305个字段；完整范围、默认值、NULL及CHECK见DESIGN.md。时间用UTC保存，界面可转为马来西亚时间；系统时间与视频第几秒不同。

## 一次真实业务怎样经过这些表

人员登录→确定角色和设备；摄像头输入视频→模型按某个参数版本检测→形成警报；复核、证据和文字描述分别补充结果；通知队列送到设备；人员阅读、确认、接手及解决；无人响应时按阶段继续提醒。

activity是系统发生的活动，并不是这份设计中额外的一张activity表。人工处理警报放alert_actions，行政修改放audit_logs，阅读分别放alert_reads及notification_reads。

## 1. users

账号身份、状态、显示资料及所属个人Borderline偏好。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `firebase_uid` | Firebase认证系统中的用户编号；这里不保存密码。 |
| `email_cache` | 身份服务同步过来的邮箱显示资料。 |
| `display_name` | 界面上显示的名称。 |
| `status` | 账号待审批、启用或停用。 |
| `borderline_enabled` | 用户是否接收边界案例相关通知；空值表示个人偏好尚未形成明确记录。 |
| `borderline_source` | 个人偏好来自默认值、角色整组修改还是个人修改等。 |
| `borderline_updated_by_user_id` | 谁修改了Borderline偏好，连接 users.id。 |
| `borderline_updated_at` | 个人或角色Borderline偏好最近修改的时间。 |
| `created_at` | 记录建立的时间。 |
| `updated_at` | 记录最近更新的时间；旧投递记录未记载时可以为空。 |

**外键连接：**

- `users.(borderline_updated_by_user_id) → users.(id)`。

## 2. roles

角色授权能力、通知敏感度及角色Borderline默认值。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `name` | 系统内部角色名称，与界面显示名称分开。 |
| `display_name` | 界面上显示的名称。 |
| `description` | 角色的文字说明。 |
| `sensitivity_mode` | 敏感度标签；角色上的值影响普通推送资格，不决定警报浏览权限。 |
| `is_system` | 是否是受保护的系统内置角色。 |
| `can_manage_roles` | 是否允许管理角色。 |
| `can_manage_users` | 是否允许管理用户。 |
| `can_manage_borderline_settings` | 是否允许管理Borderline接收设置。 |
| `borderline_default_enabled` | 此角色新成员默认的Borderline接收开关，也用于整组设置。 |
| `borderline_updated_by_user_id` | 谁修改了Borderline偏好，连接 users.id。 |
| `borderline_updated_at` | 个人或角色Borderline偏好最近修改的时间。 |
| `compat_borderline_selected` | 兼容旧设置：此角色当时是否被选为Borderline接收角色。 |
| `created_at` | 记录建立的时间。 |
| `updated_at` | 记录最近更新的时间；旧投递记录未记载时可以为空。 |

**外键连接：**

- `roles.(borderline_updated_by_user_id) → users.(id)`。

## 3. user_roles

明确的用户↔角色分配关系。

**主键：** `user_id, role_id`。

| Attribute | 易懂含义 |
|---|---|
| `user_id` | 涉及哪位用户，连接 users.id；具体身份取决于本表用途。 |
| `role_id` | 涉及哪个角色，连接 roles.id。 |
| `assigned_by_user_id` | 谁分配了这个角色，连接 users.id；系统初始化时可以为空。 |
| `assigned_at` | 分配角色的时间。 |

**外键连接：**

- `user_roles.(user_id) → users.(id)`。
- `user_roles.(role_id) → roles.(id)`。
- `user_roles.(assigned_by_user_id) → users.(id)`。

## 4. devices

当前设备归属、FCM注册值、启用及在线状态。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `user_id` | 涉及哪位用户，连接 users.id；具体身份取决于本表用途。 |
| `installation_id` | 一次应用安装的标识。 |
| `fcm_registration_value` | FCM推送地址，服务器用它向设备发送通知。 |
| `platform` | 设备平台：Android、iOS或Web。 |
| `device_label` | 设备的显示名称，方便辨认。 |
| `enabled` | 这条用户设备的推送登记是否启用：1允许作为推送候选，0不向此登记推送；不表示手机是否开机，也不涉及摄像头。实际发送仍需检查账号等条件。 |
| `last_seen_at` | 设备最近一次向系统报到的时间，不等于持续实时在线。 |
| `created_at` | 记录建立的时间。 |
| `updated_at` | 记录最近更新的时间；旧投递记录未记载时可以为空。 |

**外键连接：**

- `devices.(user_id) → users.(id)`。

## 5. locations

摄像头共享的地点主数据。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `name` | 地点名称，例如North Gate。 |
| `normalized_name` | 按统一空白和大小写规则生成的地点检索名称，用来避免重复地点。 |
| `building` | 地点所属的建筑物。 |
| `floor` | 楼层名称，例如G、B1或2，不限定只能是数字。 |
| `description` | 地点的补充说明。 |
| `created_at` | 记录建立的时间。 |

**外键连接：**

本表没有外键；其他表仍可以引用本表。

## 6. cameras

稳定摄像头身份、地点及分析状态。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `location_id` | 摄像头在哪里，连接 locations.id。 |
| `name` | 摄像头名称。 |
| `enabled` | 这台摄像头在系统中是否启用；不表示物理摄像头的电源状态。 |
| `status` | 摄像头准备好、分析中、暂停或出错。 |
| `created_at` | 记录建立的时间。 |
| `updated_at` | 记录最近更新的时间；旧投递记录未记载时可以为空。 |

**外键连接：**

- `cameras.(location_id) → locations.(id)`。

## 7. media_files

实际媒体文件的唯一元数据。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `relative_path` | 文件在服务器媒体目录下的相对位置；视频图片本身仍在文件系统。 |
| `original_filename` | 上传时原来的文件名，只作显示资料。 |
| `media_kind` | 文件用途：摄像头输入视频、证据片段或证据图片。 |
| `content_type` | 文件格式，例如video/mp4或image/jpeg。 |
| `size_bytes` | 文件大小，单位为字节。 |
| `sha256` | 文件内容的摘要，用于识别内容；不是文件本身。 |
| `created_at` | 记录建立的时间。 |

**外键连接：**

本表没有外键；其他表仍可以引用本表。

## 8. camera_sources

摄像头输入来源及替换历史。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `camera_id` | 属于哪台摄像头，连接 cameras.id。 |
| `media_file_id` | 引用哪个文件的资料，连接 media_files.id。 |
| `source_type` | 输入来自上传视频，还是RTSP视频流。 |
| `rtsp_endpoint_ref` | 服务器视频流端点或安全引用；上传视频不使用。 |
| `duration_seconds` | 完整输入视频的时长，单位为秒；直播流可能没有固定时长。 |
| `is_active` | 是否为该摄像头当前使用的输入来源。 |
| `created_at` | 记录建立的时间。 |

**外键连接：**

- `camera_sources.(camera_id) → cameras.(id)`。
- `camera_sources.(media_file_id) → media_files.(id)`。

## 9. model_versions

按任务保存不可变模型版本身份与部署来源。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `task` | 模型用于哪种检测、AI复核或场景描述任务。 |
| `model_name` | 模型名称或供应商模型名称。 |
| `version` | 模型或提示词的版本标签。 |
| `framework` | 模型使用的技术框架，旧记录可能未登记。 |
| `quantization` | 模型采用的量化方式，用于减少运行所需资源。 |
| `checkpoint_ref` | 模型权重文件在服务器上的引用。 |
| `created_at` | 记录建立的时间。 |

**外键连接：**

本表没有外键；其他表仍可以引用本表。

## 10. prompt_versions

按用途保存提示词版本、内容及摘要。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `task` | 提示词用于二元复核还是场景描述。 |
| `version` | 模型或提示词的版本标签。 |
| `capture_state` | 是否保存了完整冻结的提示词；旧记录可能只有版本资料。 |
| `content` | 完整提示词正文，即发送给模型的指令。 |
| `content_sha256` | 提示词正文的内容摘要，用于识别内容。 |
| `created_at` | 记录建立的时间。 |

**外键连接：**

本表没有外键；其他表仍可以引用本表。

## 11. config_versions

同一版本的完整运行策略快照。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `version_number` | 这套运行参数的版本编号。 |
| `created_by` | 创建来源标签，可能是用户，也可能是本地初始化等系统来源。 |
| `created_by_user_id` | 能够确认创建者是本地用户时，连接 users.id。 |
| `created_at` | 记录建立的时间。 |
| `validation_profile` | 区分新配置和历史导入，以免错误改变旧类别或阈值精度。 |
| `detection_debug_mode_enabled` | 是否开启现有检测调试信息。 |
| `default_sensitivity` | 系统默认的敏感度。 |
| `violence_segment_seconds` | 暴力检测每段视频窗口的长度，单位为秒。 |
| `violence_stride_seconds` | 相邻检测窗口向前移动多少秒。 |
| `violence_detector_input_frames` | 每个窗口交给检测模型多少帧。 |
| `violence_sensitive_threshold` | 敏感模式的暴力判定阈值。 |
| `violence_moderate_threshold` | 中等模式的暴力判定阈值。 |
| `violence_certain_threshold` | 严格模式的暴力判定阈值。 |
| `violence_cooldown_seconds` | 保留的暴力旧冷却参数；不会用它恢复丢弃新警报的旧行为。 |
| `violence_merge_gap_seconds` | 相隔多少秒以内的暴力检测可合并为同一事件。 |
| `weapon_sample_fps` | 武器检测每秒抽取多少帧。 |
| `weapon_confidence_threshold` | 保留的武器旧总体阈值；当前按类别和模式使用的阈值另存。 |
| `weapon_nms_candidate_floor` | 进入检测框去重处理的最低候选分数。 |
| `weapon_nms_iou_threshold` | 检测框重叠到什么程度时需要去重。 |
| `weapon_n_required` | 最近M帧中至少检出多少帧，记为N。 |
| `weapon_m_window` | 用于N-of-M判断的最近帧数M。 |
| `weapon_cooldown_seconds` | 保留的武器旧冷却参数。 |
| `weapon_merge_gap_seconds` | 武器检测合并为同一事件的最大间隔。 |
| `weapon_class_thresholds_json` | 按敏感度与武器类别保存各自的分数阈值。 |
| `vlm_model_id` | VLM供应商模型名称或标识；这个字段不是 model_versions.id 的外键。 |
| `vlm_quantization` | VLM模型的量化设置。 |
| `vlm_input_fps` | VLM每秒接收多少帧。 |
| `vlm_video_token_budget` | 视频输入可使用的token预算。 |
| `vlm_max_output_tokens` | 最多允许模型输出多少token。 |
| `vlm_scene_description_frames` | 生成场景描述时抽取多少帧。 |
| `vlm_borderline_review_enabled` | Web配置中的AI边界案例复核总开关，与某人是否接收通知不同。 |
| `vlm_scene_description_enabled` | 是否启用场景文字描述。 |
| `binary_prompt_version_id` | 二元AI复核的提示词版本，连接 prompt_versions.id。 |
| `scene_prompt_version_id` | 场景描述的提示词版本，连接 prompt_versions.id。 |
| `same_audience_repeat_after_seconds` | 多少秒后向相关受众重复提醒。 |
| `all_users_escalation_after_seconds` | 多少秒后扩大到全部符合条件用户。 |

**外键连接：**

- `config_versions.(created_by_user_id) → users.(id)`。
- `config_versions.(binary_prompt_version_id) → prompt_versions.(id)`。
- `config_versions.(scene_prompt_version_id) → prompt_versions.(id)`。

## 12. alerts

稳定事件及其当前唯一手机处理状态。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `camera_id` | 属于哪台摄像头，连接 cameras.id。 |
| `incident_type` | 事件类别：暴力、身体接触霸凌或武器。 |
| `workflow_status` | 人的处理进展：等确认、等接手、处理中、已解决或误报等。 |
| `sensitivity_mode` | 敏感度标签；角色上的值影响普通推送资格，不决定警报浏览权限。 |
| `is_borderline_promoted` | 是否经过边界案例复核确认后被提升为警报。 |
| `is_provisional` | 旧流程的暂定警报标记，保留历史兼容。 |
| `borderline_review_status` | AI复核进行到哪一步，与人的处理进展分开。 |
| `evidence_status` | 证据仍在准备、已经可用或准备失败等。 |
| `opening_config_version_id` | 警报首次建立时使用的参数版本，连接 config_versions.id。 |
| `opening_model_version_id` | 警报首次建立时使用的检测模型版本，连接 model_versions.id。 |
| `binary_prompt_version_id` | 二元AI复核的提示词版本，连接 prompt_versions.id。 |
| `handler_user_id` | 目前负责处理警报的用户，连接 users.id。 |
| `active_phase_id` | 目前仍在计时的提醒轮次；与本警报id一起组成复合外键。 |
| `broadcast_role_snapshot_json` | 当时选择的角色名单副本，用于历史展示；当前有效关系另外保存。 |
| `revision` | 警报处理版本号，防止多人同时提交时相互覆盖。 |
| `created_at` | 记录建立的时间。 |
| `workflow_updated_at` | 人工处理流程最近更新的时间。 |
| `resolved_at` | 警报结束处理的时间；兼容现有解决或误报关闭的时间语义。 |

**外键连接：**

- `alerts.(camera_id) → cameras.(id)`。
- `alerts.(opening_config_version_id) → config_versions.(id)`。
- `alerts.(opening_model_version_id) → model_versions.(id)`。
- `alerts.(binary_prompt_version_id) → prompt_versions.(id)`。
- `alerts.(handler_user_id) → users.(id)`。
- `alerts.(active_phase_id,id) → notification_phases.(id,alert_id)`。

连接notification_phases的复合外键同时核对phase编号与alert编号，保证引用的是同一宗警报的提醒轮次。

## 13. detection_events

一次被警报接纳的检测事实。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `alert_id` | 属于哪一宗警报，连接 alerts.id。 |
| `model_version_id` | 实际使用哪个模型版本，连接 model_versions.id。 |
| `config_version_id` | 实际使用哪套参数版本，连接 config_versions.id。 |
| `confidence` | 本次检测的模型分数，0到1；不等于对事件真相的保证。 |
| `decision_source` | 本次结果来自直接阈值、AI复核提升还是武器N-of-M规则。 |
| `source_start_seconds` | 相关内容从原视频第几秒开始；未知可为空。 |
| `source_end_seconds` | 相关内容在原视频第几秒结束；未知可为空。 |
| `detected_at` | 这次检测结果记录的时间。 |
| `qualifications_json` | 本次检测当时计算的通知资格资料；不是当前授权依据。 |
| `weapon_class_scores_json` | 本次检测中，各武器类别的模型分数。 |

**外键连接：**

- `detection_events.(alert_id) → alerts.(id)`。
- `detection_events.(model_version_id) → model_versions.(id)`。
- `detection_events.(config_version_id) → config_versions.(id)`。

## 14. borderline_reviews

一次Borderline复核的输入、排队执行、结论及补充通知资格。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `record_origin` | 记录来自新流程任务/动作还是旧历史事实；各表具体分类不同。 |
| `legacy_review_id` | 能够证明旧结果与新任务是同一次复核时，保留下来的旧记录编号。 |
| `camera_id` | 属于哪台摄像头，连接 cameras.id。 |
| `alert_id` | 属于哪一宗警报，连接 alerts.id。 |
| `detection_event_id` | 这次复核针对哪次检测，连接 detection_events.id。 |
| `model_version_id` | 实际使用哪个模型版本，连接 model_versions.id。 |
| `prompt_version_id` | 实际使用哪份提示词版本，连接 prompt_versions.id。 |
| `config_version_id` | 实际使用哪套参数版本，连接 config_versions.id。 |
| `detector_confidence` | AI复核之前，检测模型给出的原始分数。 |
| `source_start_seconds` | 相关内容从原视频第几秒开始；未知可为空。 |
| `source_end_seconds` | 相关内容在原视频第几秒结束；未知可为空。 |
| `context_path` | 提供给复核模型的视频片段在服务器上的位置。 |
| `status` | AI复核排队、执行、正面/负面结论、失败或取消。 |
| `attempt_count` | 尝试执行此复核任务多少次，最多3次。 |
| `raise_alert` | 复核是否给出需要警报的正面结论；没有结论时为空。 |
| `uncertainty` | 模型认为结果的不确定程度：低、中或高。 |
| `brief_reason` | 模型给出的简短判断理由。 |
| `error_code` | 这项记录的错误原因；没有错误或未记录时为空。 |
| `vlm_settings_json` | 执行这次复核时真正使用的VLM参数副本。 |
| `supplemental_notification_status` | 复核后的补充通知是否待处理、已处理或被禁用，避免重启后重复推送。 |
| `created_at` | 记录建立的时间。 |
| `started_at` | 任务或计时阶段开始的时间。 |
| `completed_at` | 任务或计时阶段结束的时间。 |
| `reviewed_at` | 旧复核结果原本登记的复核时间，与新任务完成时间分别保留。 |

**外键连接：**

- `borderline_reviews.(camera_id) → cameras.(id)`。
- `borderline_reviews.(alert_id,camera_id) → alerts.(id,camera_id)`。
- `borderline_reviews.(detection_event_id) → detection_events.(id)`。
- `borderline_reviews.(model_version_id) → model_versions.(id)`。
- `borderline_reviews.(prompt_version_id) → prompt_versions.(id)`。
- `borderline_reviews.(config_version_id) → config_versions.(id)`。

## 15. evidence_records

警报证据关系、来源区间及保留期限。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `alert_id` | 属于哪一宗警报，连接 alerts.id。 |
| `media_file_id` | 引用哪个文件的资料，连接 media_files.id。 |
| `evidence_type` | 证据类别：暴力片段、霸凌片段或武器截图。 |
| `source_start_seconds` | 相关内容从原视频第几秒开始；未知可为空。 |
| `source_end_seconds` | 相关内容在原视频第几秒结束；未知可为空。 |
| `captured_at` | 证据产生或采集的时间。 |
| `delete_after` | 证据达到保留期限、可以安排清理的时间。 |

**外键连接：**

- `evidence_records.(alert_id) → alerts.(id)`。
- `evidence_records.(media_file_id) → media_files.(id)`。

## 16. vlm_descriptions

场景描述生成状态、输出及重新生成历史。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `alert_id` | 属于哪一宗警报，连接 alerts.id。 |
| `model_version_id` | 实际使用哪个模型版本，连接 model_versions.id。 |
| `prompt_version_id` | 实际使用哪份提示词版本，连接 prompt_versions.id。 |
| `config_version_id` | 实际使用哪套参数版本，连接 config_versions.id。 |
| `status` | 描述等待生成、已经生成、失败或已禁用。 |
| `output_format` | 描述使用标准摘要、旧JSON还是纯文字等格式。 |
| `structured_json` | 模型生成的结构化描述内容。 |
| `display_text` | 供界面显示的场景文字描述，兼容旧文字结果。 |
| `error_code` | 这项记录的错误原因；没有错误或未记录时为空。 |
| `created_at` | 记录建立的时间。 |
| `completed_at` | 任务或计时阶段结束的时间。 |

**外键连接：**

- `vlm_descriptions.(alert_id) → alerts.(id)`。
- `vlm_descriptions.(model_version_id) → model_versions.(id)`。
- `vlm_descriptions.(prompt_version_id) → prompt_versions.(id)`。
- `vlm_descriptions.(config_version_id) → config_versions.(id)`。

## 17. alert_actions

用户对事件的人工处理历史及幂等请求。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `alert_id` | 属于哪一宗警报，连接 alerts.id。 |
| `user_id` | 谁做了这次警报处理，连接 users.id。 |
| `action` | 操作名称，例如确认、接手、解决或行政修改。 |
| `record_origin` | 记录来自新流程任务/动作还是旧历史事实；各表具体分类不同。 |
| `note` | 操作人填写的原因或备注。 |
| `broadcast_role_snapshot_json` | 当时选择的角色名单副本，用于历史展示；当前有效关系另外保存。 |
| `idempotency_key` | 请求的防重复编号；网络重试同一请求时避免重复执行。 |
| `created_at` | 记录建立的时间。 |

**外键连接：**

- `alert_actions.(alert_id) → alerts.(id)`。
- `alert_actions.(user_id) → users.(id)`。

## 18. alert_reads

每用户每警报的首次浏览状态。

**主键：** `user_id, alert_id`。

| Attribute | 易懂含义 |
|---|---|
| `user_id` | 涉及哪位用户，连接 users.id；具体身份取决于本表用途。 |
| `alert_id` | 属于哪一宗警报，连接 alerts.id。 |
| `read_at` | 该用户第一次阅读这条内容的时间。 |

**外键连接：**

- `alert_reads.(user_id) → users.(id)`。
- `alert_reads.(alert_id) → alerts.(id)`。

## 19. notifications

面向用户的逻辑通知意图、内容和稳定幂等身份。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `alert_id` | 属于哪一宗警报，连接 alerts.id。 |
| `message_kind` | 这条通知的用途，例如初次通知、主提醒、补发或状态更新。 |
| `event_key` | 通知对应业务事件的稳定编号，用来避免重复建立同一次业务通知。 |
| `visibility_policy` | 限定名单可见或广播可见；名单变空不会自动扩大为广播。 |
| `phase_id` | 属于哪一轮提醒计时，连接 notification_phases.id。 |
| `reminder_stage` | 属于某轮计时中的第1或第2次提醒；非提醒消息为空。 |
| `title` | 通知标题。 |
| `body` | 通知正文。 |
| `data_payload_json` | 发送给手机的附加资料，例如alert ID；不能替代当前权限检查。 |
| `created_at` | 记录建立的时间。 |

**外键连接：**

- `notifications.(alert_id) → alerts.(id)`。
- `notifications.(phase_id,alert_id) → notification_phases.(id,alert_id)`。

连接notification_phases的复合外键同时核对phase编号与alert编号，保证引用的是同一宗警报的提醒轮次。

## 20. notification_outbox

某条通知的持久发送任务、领取与重试状态。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `notification_id` | 属于哪条通知，连接 notifications.id。 |
| `channel_id` | 手机通知通道编号，例如区分不同提醒声音。 |
| `status` | 整条发送任务的状态，例如排队、领取、成功、部分成功或失败。 |
| `attempt_count` | 队列被领取多少次；不是逐设备实际发送次数。 |
| `next_attempt_at` | 下次可以尝试发送的时间。 |
| `claimed_at` | 后台工作程序领取这项任务的时间。 |
| `last_error_code` | 发送队列最近一次出错的原因。 |
| `created_at` | 记录建立的时间。 |
| `updated_at` | 记录最近更新的时间；旧投递记录未记载时可以为空。 |

**外键连接：**

- `notification_outbox.(notification_id) → notifications.(id)`。

## 21. notification_recipients

通知展示名单与实际排队用户快照。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `notification_id` | 属于哪条通知，连接 notifications.id。 |
| `user_id` | 指定用户收件对象，与role_id必须二选一。 |
| `role_id` | 指定角色可见对象，与user_id必须二选一。 |
| `feed_visible` | 此收件对象是否属于手机通知列表的可见名单。 |
| `queued_target` | 此用户是否属于实际推送队列的目标名单。 |

**外键连接：**

- `notification_recipients.(notification_id) → notifications.(id)`。
- `notification_recipients.(user_id) → users.(id)`。
- `notification_recipients.(role_id) → roles.(id)`。

## 22. notification_deliveries

每通知每设备的最新实际发送结果。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `notification_id` | 属于哪条通知，连接 notifications.id。 |
| `device_id` | 发送给哪台设备，连接 devices.id。 |
| `recipient_user_id` | 发送当时的用户，连接 users.id；设备以后换用户，这个历史值仍保留。 |
| `status` | 发给这台设备的结果，例如成功、失败或设备失效。 |
| `provider_message_id` | FCM等推送服务返回的消息编号。 |
| `attempt_count` | 实际尝试向这台设备发送多少次。 |
| `last_attempt_at` | 最近一次实际尝试向设备发送的时间。 |
| `error_code` | 这项记录的错误原因；没有错误或未记录时为空。 |
| `updated_at` | 记录最近更新的时间；旧投递记录未记载时可以为空。 |

**外键连接：**

- `notification_deliveries.(notification_id) → notifications.(id)`。
- `notification_deliveries.(device_id) → devices.(id)`。
- `notification_deliveries.(recipient_user_id) → users.(id)`。

## 23. notification_reads

每用户每消息的首次已读关系。

**主键：** `notification_id, user_id`。

| Attribute | 易懂含义 |
|---|---|
| `notification_id` | 属于哪条通知，连接 notifications.id。 |
| `user_id` | 涉及哪位用户，连接 users.id；具体身份取决于本表用途。 |
| `read_at` | 该用户第一次阅读这条内容的时间。 |

**外键连接：**

- `notification_reads.(notification_id) → notifications.(id)`。
- `notification_reads.(user_id) → users.(id)`。

## 24. alert_notification_audience

每警报每用户的初次通知资格及已送达事实。

**主键：** `alert_id, user_id`。

| Attribute | 易懂含义 |
|---|---|
| `alert_id` | 属于哪一宗警报，连接 alerts.id。 |
| `user_id` | 涉及哪位用户，连接 users.id；具体身份取决于本表用途。 |
| `qualification_reasons_json` | 该用户当时为什么符合初次通知接收条件。 |
| `first_notification_id` | 与初次通知尝试关联的消息；兼容现逻辑时可能更新为最近一次初次尝试，不保证永远固定第一条。 |
| `first_queued_at` | 第一次把该用户加入本警报初次通知队列的时间。 |
| `first_sent_at` | 第一次成功向该用户发送本警报通知的时间。 |

**外键连接：**

- `alert_notification_audience.(alert_id) → alerts.(id)`。
- `alert_notification_audience.(user_id) → users.(id)`。
- `alert_notification_audience.(first_notification_id) → notifications.(id)`。

## 25. notification_phases

事件Phase A/B的计时快照及旧计划归档。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `alert_id` | 属于哪一宗警报，连接 alerts.id。 |
| `phase_kind` | Phase A等待确认；Phase B已经确认、等待有人接手。 |
| `phase_status` | 本轮提醒在进行、已取消、已完成，或属于旧计划归档。 |
| `started_at` | 任务或计时阶段开始的时间。 |
| `first_after_seconds` | 从本轮开始后，等待多少秒再重复提醒。 |
| `all_after_seconds` | 从本轮开始后，等待多少秒再扩大提醒范围。 |
| `first_due_at` | 本轮首次重复提醒的到期时间。 |
| `all_due_at` | 本轮扩大到全部符合条件用户的到期时间。 |
| `broadcast_role_snapshot_json` | 当时选择的角色名单副本，用于历史展示；当前有效关系另外保存。 |
| `initiated_by_user_id` | 谁发起这轮提醒，连接 users.id；自动Phase A可以为空。 |
| `source_notification_id` | 触发这轮计时的通知，连接 notifications.id；没有消息也可以建立Phase B。 |
| `cancel_reason` | 为什么取消或替换这轮提醒。 |
| `completed_at` | 任务或计时阶段结束的时间。 |
| `is_legacy` | 是否来自旧提醒计划，以便保留历史。 |

**外键连接：**

- `notification_phases.(alert_id) → alerts.(id)`。
- `notification_phases.(initiated_by_user_id) → users.(id)`。
- `notification_phases.(source_notification_id) → notifications.(id)`。

## 26. notification_phase_roles

Phase B当时选择的live Broadcast Roles。

**主键：** `phase_id, role_id`。

| Attribute | 易懂含义 |
|---|---|
| `phase_id` | 属于哪一轮提醒计时，连接 notification_phases.id。 |
| `role_id` | 涉及哪个角色，连接 roles.id。 |

**外键连接：**

- `notification_phase_roles.(phase_id) → notification_phases.(id)`。
- `notification_phase_roles.(role_id) → roles.(id)`。

## 27. notification_phase_dispatches

一个phase-stage的领取、防重与完成结果。

**主键：** `phase_id, stage`。

| Attribute | 易懂含义 |
|---|---|
| `phase_id` | 属于哪一轮提醒计时，连接 notification_phases.id。 |
| `stage` | 同一轮计时中的第1次或第2次提醒。 |
| `status` | 本轮第1/2次提醒的执行结果，也能记录跳过或无收件人。 |
| `notification_id` | 属于哪条通知，连接 notifications.id。 |
| `recipient_count_unit` | 收件数量统计的是用户还是设备；旧数据无法确认时为空。 |
| `recipient_count` | 当时统计的收件数量，须配合统计单位理解。 |
| `sent_count` | 成功发送的设备数量。 |
| `failed_count` | 发送失败的设备数量。 |
| `error_code` | 这项记录的错误原因；没有错误或未记录时为空。 |
| `claimed_at` | 后台工作程序领取这项任务的时间。 |
| `completed_at` | 任务或计时阶段结束的时间。 |

**外键连接：**

- `notification_phase_dispatches.(phase_id) → notification_phases.(id)`。
- `notification_phase_dispatches.(notification_id) → notifications.(id)`。

## 28. notification_phase_audience

每阶段、每用户、每stage的发送防重与结果。

**主键：** `phase_id, user_id, stage`。

| Attribute | 易懂含义 |
|---|---|
| `phase_id` | 属于哪一轮提醒计时，连接 notification_phases.id。 |
| `user_id` | 涉及哪位用户，连接 users.id；具体身份取决于本表用途。 |
| `stage` | 同一轮计时中的第1次或第2次提醒。 |
| `status` | 这个用户在本轮本次提醒中的排队、成功、失败或跳过状态。 |
| `notification_id` | 属于哪条通知，连接 notifications.id。 |
| `updated_at` | 记录最近更新的时间；旧投递记录未记载时可以为空。 |

**外键连接：**

- `notification_phase_audience.(phase_id) → notification_phases.(id)`。
- `notification_phase_audience.(user_id) → users.(id)`。
- `notification_phase_audience.(notification_id) → notifications.(id)`。

## 29. mobile_settings

全局管理偏好、Borderline并发revision与模拟通话不变量。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `borderline_alert_enabled` | 保留的Mobile管理设置，不与Web的AI复核总开关混成同一个值。 |
| `borderline_revision` | Borderline接收设置的并发版本号。 |
| `emergency_call_simulation_only` | 当前通话功能只允许模拟。 |
| `updated_by_user_id` | 谁修改了全局设置，连接 users.id。 |
| `updated_at` | 记录最近更新的时间；旧投递记录未记载时可以为空。 |

**外键连接：**

- `mobile_settings.(updated_by_user_id) → users.(id)`。

## 30. audit_logs

行政、安全与配置操作的不可变审计事实。

**主键：** `id`。

| Attribute | 易懂含义 |
|---|---|
| `id` | 本表这条记录的唯一编号。 |
| `user_id` | 谁做了这个行政操作，连接 users.id；系统操作或已删除操作者可为空。 |
| `action` | 操作名称，例如确认、接手、解决或行政修改。 |
| `entity_type` | 行政审计当时针对的对象类别，例如role。 |
| `entity_id` | 当时被操作对象的编号；是历史参考，不是数据库外键。 |
| `detail_json` | 当时行政操作的具体内容及变化。 |
| `created_at` | 记录建立的时间。 |

**外键连接：**

- `audit_logs.(user_id) → users.(id)`。

## 31. schema_migrations

数据库结构升级的持久账表。

**主键：** `version`。

| Attribute | 易懂含义 |
|---|---|
| `version` | 数据库结构版本号，也是本表主键；与模型和运行参数版本不同。 |
| `name` | 这次数据库结构升级的名称。 |
| `applied_at` | 这次数据库结构升级真正完成的时间。 |

**外键连接：**

本表没有外键；其他表仍可以引用本表。

## 关系和活动的具体例子

- 用户U1确认并接手警报A1：alert_actions.user_id=U1、alert_id=A1保留历史；alerts.handler_user_id=U1及workflow_status记录当前状态。
- 管理员U1修改角色R1：audit_logs.user_id真正连接users；entity_type=role及entity_id=R1只是历史参考，不是对roles的FK，角色删除后也能保留当时目标。
- 用户U1拥有角色R1：user_roles.user_id连接users，role_id连接roles；通过关联表把逻辑多对多实现为两组一对多。

## 最容易混淆的记录

| 表 | 分别回答什么 |
|---|---|
| alerts / detection_events | 一宗合并后的事件 / 每一次被接纳的检测事实。 |
| model_versions / prompt_versions / config_versions | 用什么模型 / 给模型什么指令 / 使用什么参数。 |
| camera_sources / media_files | 摄像头使用哪个输入 / 文件本身在哪里、是什么格式、多大。 |
| borderline_reviews / vlm_descriptions | 是否需要警报的复核 / 画面内容的文字说明。 |
| notifications / notification_outbox / notification_deliveries | 要说什么 / 后台发送任务 / 某台设备的发送结果。 |
| notification_recipients / alert_notification_audience / notification_phase_audience | 某条消息的名单 / 某警报某用户的初次通知资格和结果 / 某轮某次提醒某用户的防重状态。 |
| notification_phases / notification_phase_dispatches | 整轮提醒计时 / 该轮第1或第2次提醒的执行。 |
| alert_actions / audit_logs | 人如何处理警报 / 管理员如何修改账号、角色及设置。 |
| alert_reads / notification_reads | 看过警报 / 看过通知，互不等同，也不会自动停止提醒。 |

## 关系补充

大部分关系是一对多。例如一台摄像头可以有多宗警报，alerts.camera_id连接cameras.id。并非所有FK都是一对多：outbox.notification_id也唯一，所以一条通知最多一项发送任务；review.detection_event_id唯一，所以一次检测最多一项带输入的复核任务。

phase是一整轮计时，stage是该轮第1/2次提醒。一宗警报可有多轮历史phase，但最多一轮active。Phase A等确认，Phase B已确认、等接手。

外键保证关联对象存在，不代替操作权限、状态机、发送服务或文件访问检查。历史JSON名单不能替代当前授权。schema_migrations是结构升级记录，不是业务模型参数的config版本。

合计：31表、305字段。

[完整约束设计](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/DESIGN.md) · [PK/FK速查](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/KEYS.md)
