# GuardAId / Mobile App — 推荐的针对性数据库重构规划

日期：2026-10-13（Asia/Kuala_Lumpur）。基线为当前schema v14的44张SQLite表及当前Web/Mobile功能。

**推荐结果为31张实际实体表，其中30张业务/配置/运行表，1张schema_migrations基础设施账表。** 这个数量来自逐项职责判断，未预设必须少于15、25或30。目标是一个清晰、可约束、可迁移的系统模型。此方案替代“15实体硬上限”方案作为无ERD数量限制时的推荐方向。

本次只生成规划、完整逐属性字典、31表可执行DDL样稿、ERD及独立验证程序。运行数据库、FastAPI/worker和Flutter源码不作修改；新DDL不能直接替换现有SQLiteRepository运行文件。功能在设计层面逐项覆盖，实际迁移后的端到端兼容性尚须证明。

实体保留规则：独立身份/生命周期、真实1:N或M:N关系、独立状态及保留职责，至少满足一个且不能只是把DTO拆成表。合并规则：同一事实双写，或相同主键决定的一对一属性组，且不会丢独立历史。每个合并需要旧数据映射及事务行为说明。

不合并的关键边界：model/prompt/config各自版本；detector/review/description不同事实；alert_read/notification_read/人工动作/行政审计不同用途；通知内容/outbox任务/阶段执行不同生命周期。不会用一张宽大多态表统一六种通知关系。


## 实体职责与独立存在的依据

| # | Entity | 核心职责 | 保留/合并理由 |
|---:|---|---|---|
| 1 | `users` | 账号身份、状态、显示资料及所属个人Borderline偏好 | 一个用户的一份偏好由同一个user_id决定；随账号存在，无独立生命周期。合并原borderline_user_settings，保留NULL=未物化、来源及独立修改时间。Firebase缓存不套用本地编辑80字符限制。 |
| 2 | `roles` | 角色授权能力、通知敏感度及角色Borderline默认值 | 权限与角色默认偏好都由role_id决定。合并原borderline_role_settings；系统角色保护、授权上限由服务事务执行。compat选择标记替代单例设置中的活角色ID数组。 |
| 3 | `user_roles` | 明确的用户↔角色分配关系 | 用户与角色具有不同身份及生命周期，关系必须独立。当前UI单角色不成为数据库永久唯一限制；保留现有多角色关系能力。 |
| 4 | `devices` | 当前设备归属、FCM注册值、启用及在线状态 | 一用户可有多设备；token可以在重新登录时改变当前归属。与逐次通知结果分开，避免历史发送结果阻止token转移。 |
| 5 | `locations` | 摄像头共享的地点主数据 | 名称标准化去重、建筑/楼层/说明由地点决定，不重复塞进每台摄像头。normalized_name可能因Unicode casefold扩长，不强设与name相同长度。 |
| 6 | `cameras` | 稳定摄像头身份、地点及分析状态 | 摄像头身份与可以替换的视频来源分离；与可产生多个警报的关系独立。 |
| 7 | `media_files` | 实际媒体文件的唯一元数据 | 文件可被来源/证据引用。文件与使用关系、证据生命周期不同。视频保存在filesystem，数据库只记录元数据；relative_path唯一。 |
| 8 | `camera_sources` | 摄像头输入来源及替换历史 | 来源替换修改active关系；文件元数据不重复。source并非证据：所有者、输入类型和保留/清理策略不同，因此不合并为多态media_usages。 |
| 9 | `model_versions` | 按任务保存不可变模型版本身份与部署来源 | 多个检测/描述/复核共享版本；独立于策略及prompt生命周期。不与完整配置、全局偏好混成runtime_resources。历史未记录metadata不能伪造。 |
| 10 | `prompt_versions` | 按用途保存提示词版本、内容及摘要 | 二元复核prompt与场景prompt分别版本化；被引用的历史内容不能原位改变。capture_state是迁移事实标记，不是替未来功能创建的独立entity。 |
| 11 | `config_versions` | 同一版本的完整运行策略快照 | 当前五张配置表的30个版本一一对应，save_settings同一事务写五行，get_settings同查。所有属性依赖同一配置版本，合并没有引入跨实体事实；减少缺一张明细就无法读取的故障点。模型/提示词仍独立。 |
| 12 | `alerts` | 稳定事件及其当前唯一手机处理状态 | workflow是每个alert恰好一条的当前状态，不具有独立身份/历史生命周期；并入alert。去掉可以由workflow确定的旧status重复真值，DTO仍返回原status。检测统计继续从events聚合；不要加入description或review次数。 |
| 13 | `detection_events` | 一次被警报接纳的检测事实 | 检测事实与可运行/失败/重试的VLM任务不同；不合成inference_runs。多次检测的分数、来源窗口及版本不能被事件最高分覆盖。 |
| 14 | `borderline_reviews` | 一次Borderline复核的输入、排队执行、结论及补充通知资格 | 原task与review审计主要镜像同一复核结果，属于同一review生命周期。保留audit_only形式来表达旧记录真正缺失的数据，不把legacy结果恢复成可执行任务；detector/description仍独立。 |
| 15 | `evidence_records` | 警报证据关系、来源区间及保留期限 | 证据有自身captured/retention事实；与camera_sources的输入类型/active关系不同，因此独立。删除证据关系并不让DB自动删除物理文件，服务仍协调filesystem清理。 |
| 16 | `vlm_descriptions` | 场景描述生成状态、输出及重新生成历史 | 描述是每个事件可以重试/重新生成的一系列输出，并非detector事实或二元复核。读取最新ready结果；没有必要与其他推理记录共享大表和状态枚举。 |
| 17 | `alert_actions` | 用户对事件的人工处理历史及幂等请求 | 旧alert_actions和新workflow_events都是人的一次事件响应；统一历史事件模型。audit_logs保留另一条行政审计事实，不按相同时间乱删。已读关系仍单独保存。 |
| 18 | `alert_reads` | 每用户每警报的首次浏览状态 | 这是独立的用户↔警报状态关系，不是人工响应或行政审计；保留小而明确的联合主键。read不停止提醒。 |
| 19 | `notifications` | 面向用户的逻辑通知意图、内容和稳定幂等身份 | 通知作为可浏览内容有独立生命周期；发送任务失败/重试不应改变消息身份。保留notifications，不把跳过阶段造成为假的隐藏消息。payload镜像当前一致，采用一份canonical值；若切换时发现差异先阻断核对并归档原值。 |
| 20 | `notification_outbox` | 某条通知的持久发送任务、领取与重试状态 | 这是可领取、重试和恢复的任务，和用户看到的消息不同；保留独立表有明确运行职责。移除重复event_key及payload：幂等键与正文归notifications。只统一数据真值，不为了减少表数混合生命周期。 |
| 21 | `notification_recipients` | 通知展示名单与实际排队用户快照 | 旧队列与feed使用同一用户名单，却保存两套关系。合并为一行两个有意义标记，可保留两名单不同时的事实；live FK仍明确。原UNIQUE(notification_id,role_id,user_id)含NULL不能可靠去重，改为两个partial UNIQUE。 |
| 22 | `notification_deliveries` | 每通知每设备的最新实际发送结果 | 当前worker同一事务双写两份结果，115份镜像逐字段一致。统一一条消息/设备事实；不采用会因设备换用户而失效的永久(device_id,user_id)复合FK。新插入在服务事务校验当时owner，历史NULL诚实表达未知。 |
| 23 | `notification_reads` | 每用户每消息的首次已读关系 | 通知读取与警报浏览是不同用户行为，不合并到共享read字段，也不混进不可删的行政审计。mark-all-read仍逐用户执行。 |
| 24 | `alert_notification_audience` | 每警报每用户的初次通知资格及已送达事实 | 此关系跨越多条通知，负责初次推送去重与阶段1真实audience。不能与单条消息recipient混为同一事实；资格缓存不是浏览授权。 |
| 25 | `notification_phases` | 事件Phase A/B的计时快照及旧计划归档 | 计时从一个明确phase anchor开始，响应可取消；与消息/队列任务独立。同一事件可有多个历史phase，但最多一个active。old schedules也是计时快照，迁移后停止双写。精确微秒deadline计算由Python datetime执行，不用SQLite毫秒舍入函数代替。 |
| 26 | `notification_phase_roles` | Phase B当时选择的live Broadcast Roles | 这张新增表有真实职责：把当前用于即时收件资格的JSON角色ID列表改为可执行FK、可索引的M:N关系。选中时最多20角色由事务验证；以后角色删除不抹掉历史快照。 |
| 27 | `notification_phase_dispatches` | 一个phase-stage的领取、防重与完成结果 | 一条stage执行记录可能没有任何消息或设备，所以它不能被并入notifications而造假通知。claim事实是提醒worker的幂等保障；outbox任务处理的是已有消息的发送与重试，两者语义不同。 |
| 28 | `notification_phase_audience` | 每阶段、每用户、每stage的发送防重与结果 | late-repeat可在主stage完成后为新增资格账号创建另一条消息；逐用户stage防重仍独立。不是message recipient的重复：这里跨越该phase-stage的多个通知。删除冗余alert_id依赖，查旧API时JOIN phase。 |
| 29 | `mobile_settings` | 全局管理偏好、Borderline并发revision与模拟通话不变量 | 全局偏好与角色/用户配置的统一revision属于同一单例。合并borderline_settings_revision；旧角色集合转roles.compat_borderline_selected，历史集合留审计。系统运行策略版本仍在config_versions。 |
| 30 | `audit_logs` | 行政、安全与配置操作的不可变审计事实 | 审计与事件人工响应、首次已读、schema账表有不同查询/保留职责，保留独立。删除role不能让过去管理员操作消失；entity_id是历史snapshot，不能谎称它受FK保障。 |
| 31 | `schema_migrations` | 数据库结构升级的持久账表 | 基础设施元数据有明确长期职责。保留当前migration runner识别的账表，不为了少一张而混入用户活动。schema版本只以本表为真值；DDL样稿不插入假装已执行的升级行。 |

## 表数量由哪些决策产生

| 决策 | 表数变化 | 依据 |
|---|---:|---|
| 基线 | +44 | 原schema v14 |
| 五张配置表合并 | -4 | 相同版本和保存生命周期 |
| 用户/角色偏好并入主实体 | -2 | 两个一对一属性组 |
| revision并入全局设置 | -1 | 同一全局设置聚合 |
| 当前workflow并入alert | -1 | 每事件唯一当前状态 |
| 统一复核请求/结论镜像 | -1 | 同一review生命周期 |
| 统一旧/新人工响应历史 | -1 | 同一事件动作事实 |
| 旧计划/dispatch统一当前phase模型 | -2 | 保留全部旧事实，结束双写 |
| 统一用户目标名单 | -1 | 同一message-user关系的两个标记 |
| 统一设备结果镜像 | -1 | 同一message-device结果 |
| 新增live phase-role关联 | +1 | 替代可执行JSON关联，提高FK保障 |

结果为31张实际表（30张业务/配置/运行表加1张schema账表）。没有以预定表数倒推实体。

## 约束的设计原则与依据

1. 使用STRICT表和SQLite原生INTEGER/REAL/TEXT。TEXT变长，VARCHAR(n)不预留n字符也不执行长度限制；枚举CHECK已限定全部允许值，不再重复一个“最长枚举字符串”约束。没有gender等项目未需要的字段。
2. 公开ID保留TEXT seed/prefix_UUID形式，保证现有deep link/URL/API身份；不假定所有ID都是固定36字符UUID。无公开ID的notification_recipients关系使用INTEGER PRIMARY KEY，不额外生成UUID。所有TEXT PK明确NOT NULL，避免旧SQLite普通TEXT PRIMARY KEY可能容许NULL的行为。
3. 本地字段长度源自现有domain.py：摄像头名80、地点名120、角色显示名60、角色说明200、设备label100、installation512、FCM registration20..4096、native操作note1000、幂等键8..120。Firebase UID1..128源自供应商。user显示名与email是外部缓存，Firebase登录路径不受本地编辑80/创建邮箱254限制；API创建/编辑仍执行原上限。normalized_name可能Unicode casefold扩长，不套120上限。
4. 没有已有业务/协议上限的版本名、模型名、错误码、说明、provider_message_id、文件名、服务器引用不臆造255等上限。传输总大小、上传大小和FCM最终UTF8 payload预算继续由既有服务/协议边界验证；数据库字符长度不冒充协议字节限额。
5. Boolean为INTEGER CHECK IN(0,1)。多值状态保留可读TEXT枚举，不把未定义/未知状态编码为0。NULL用于真正未知/不适用，不能用0、空字符串、1970日期或默认版本冒充历史信息。
6. 时间仍采用TEXT，统一27字符UTC格式YYYY-MM-DDTHH:MM:SS.ffffffZ，保留既有微秒和ISO语义，便于SQL排查与词典序比较。日期部分必须是真实公历日期，时间HH/MM/SS范围正确；不依赖SQLite对完整小数时间的毫秒舍入作精确验证。原Z三位小数及+00:00六位小数只做无损UTC规范化。没有日期时不填now。
7. 视频source时间REAL秒，非负；起止已知时end>=start，不强迫旧未知区间全补齐。系统time不是视频source time。概率REAL0..1且必要场合NOT NULL，NaN转NULL不能绕过必填。帧数、token、N/M与间隔范围来自当前Pydantic配置。武器阈值新输入继续四小数；历史校准值与long_gun/五类别不重舍入。
8. 暴力模式顺序、stride<=segment、N<=M、scene帧数偶数、全体升级晚于首次重复，都有明确现有业务依据。武器跨模式排序目前不是API约束，本次不悄悄新增该限制；保持三个可配置校准profile，未来若定义单调性需要同时修改产品规则和validator，而非暗改数据库。
9. SHA-256维持当前TEXT小写hex64格式以降低迁移/工具兼容成本，CHECK真实摘要长度和字符集合。32字节BLOB更紧凑，但这不是本次重构必要条件；不声称“零浪费”或把固定声明长度当成磁盘预分配。
10. 明确NOT NULL、FK、UNIQUE、CHECK；跨表task/alert/phase约束才用trigger。JSON只用于已有输入/输出、数值映射和历史snapshot；live phase-role选择改为关系表。CHECK的NULL三值逻辑及JSON必填键用IS NOT NULL/COALESCE(...,0)处理。
11. 删除行为依生命周期：可撤销用户状态关系CASCADE；历史model/config/prompt和人工动作RESTRICT/保留；first_notification等历史指针SET NULL而不抹除首发时间；物理文件只由服务协调删除。users/devices历史以停用/撤销为主，不为了清表物理删除审计主体。audit entity_id是历史snapshot，不是伪动态FK。
12. 默认值必须明确：pending、enabled=1、布尔权限默认0、attempt_count=0、空资格对象等。涉及版本、prompt、deadline、actor或已读时间不随便设默认。小表没有查询需求的字段不盲目加索引；高频筛选/父键依赖索引按这里的实际路径设计，再用负载验证。


## 逐属性数据字典

列约束为可执行DDL；下面另列完整表级PK/FK/CHECK及索引。默认值只用于有明确语义的初始化，不能用默认值掩盖未知历史信息。

### 1. `users`

账号身份、状态、显示资料及所属个人Borderline偏好
一个用户的一份偏好由同一个user_id决定；随账号存在，无独立生命周期。合并原borderline_user_settings，保留NULL=未物化、来源及独立修改时间。Firebase缓存不套用本地编辑80字符限制。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `firebase_uid` | `TEXT` | `NOT NULL` | Firebase 官方规定 UID 为 1..128 字符；大小写敏感、唯一，不保存密码 |
| `email_cache` | `TEXT` | `NULL allowed` | Firebase 返回的显示缓存不等同于新建用户输入；不额外封死已有外部身份。管理员创建时仍沿用254字符 API 上限 |
| `display_name` | `TEXT` | `NULL allowed` | Firebase 登录会同步 provider displayName，其协议未规定80字符上限；本地创建/编辑仍在 API 限制80，DB不截断合法外部缓存 |
| `status` | `TEXT` | `NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','disabled'))` | 保留审批、启用、停用三个状态 |
| `borderline_enabled` | `INTEGER` | `CHECK(borderline_enabled IN (0,1))` | NULL 表示尚无个人偏好，0/1 表示已经物化的有效开关 |
| `borderline_source` | `TEXT` | `CHECK(borderline_source IN ('migration','role_default','role_bulk','individual'))` | 保留个人覆盖与整组覆盖来源 |
| `borderline_updated_by_user_id` | `TEXT` | `NULL allowed` | 修改偏好的用户 ID；后台初始化时可空 |
| `borderline_updated_at` | `TEXT` | `CHECK(borderline_updated_at IS NULL OR (length(borderline_updated_at)=27 AND borderline_updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(borderline_updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(borderline_updated_at,1,10),'+0 days')=substr(borderline_updated_at,1,10),0) AND CAST(substr(borderline_updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(borderline_updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(borderline_updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `updated_at` | `TEXT` | `NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
UNIQUE(firebase_uid)
CHECK(updated_at >= created_at)
CHECK((borderline_enabled IS NULL AND borderline_source IS NULL AND borderline_updated_at IS NULL) OR (borderline_enabled IS NOT NULL AND borderline_source IS NOT NULL AND borderline_updated_at IS NOT NULL))
FOREIGN KEY (borderline_updated_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
CHECK(length(firebase_uid) BETWEEN 1 AND 128)
```

索引：

```sql
CREATE INDEX ix_users_email_lookup ON users(lower(trim(email_cache))) WHERE email_cache IS NOT NULL;
```

### 2. `roles`

角色授权能力、通知敏感度及角色Borderline默认值
权限与角色默认偏好都由role_id决定。合并原borderline_role_settings；系统角色保护、授权上限由服务事务执行。compat选择标记替代单例设置中的活角色ID数组。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `name` | `TEXT` | `NOT NULL CHECK(length(trim(name)) > 0)` | 机器角色名唯一；历史 seed 名称和新的 slug 都保留 |
| `display_name` | `TEXT` | `CHECK(length(display_name) <= 60)` | 新建/编辑接口要求 2..60 字符；历史行可空 |
| `description` | `TEXT` | `CHECK(length(description) <= 200)` | 角色描述的当前接口上限为 200 字符 |
| `sensitivity_mode` | `TEXT` | `NOT NULL DEFAULT 'moderate' CHECK(sensitivity_mode IN ('sensitive','moderate','certain'))` | 只决定普通推送资格，不作为警报可见性的授权条件 |
| `is_system` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(is_system IN (0,1))` | 系统角色标记；保护删除逻辑仍在事务权限检查中 |
| `can_manage_roles` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(can_manage_roles IN (0,1))` | 对应目前已有的角色管理权限 |
| `can_manage_users` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(can_manage_users IN (0,1))` | 对应目前已有的用户管理权限 |
| `can_manage_borderline_settings` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(can_manage_borderline_settings IN (0,1))` | 对应目前已有的 Borderline 管理权限 |
| `borderline_default_enabled` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(borderline_default_enabled IN (0,1))` | 新成员默认值及整组覆盖操作的角色默认开关 |
| `borderline_updated_by_user_id` | `TEXT` | `NULL allowed` | 默认值修改者，使用真实用户外键 |
| `borderline_updated_at` | `TEXT` | `CHECK(borderline_updated_at IS NULL OR (length(borderline_updated_at)=27 AND borderline_updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(borderline_updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(borderline_updated_at,1,10),'+0 days')=substr(borderline_updated_at,1,10),0) AND CAST(substr(borderline_updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(borderline_updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(borderline_updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `compat_borderline_selected` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(compat_borderline_selected IN (0,1))` | 旧版 mobile_settings 的角色选择集合，逐角色保存，避免把活关系藏入 JSON |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `updated_at` | `TEXT` | `NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
UNIQUE(name)
CHECK(updated_at >= created_at)
FOREIGN KEY (borderline_updated_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
```

索引：

```sql
-- 只有PK/UNIQUE自动索引，无额外查询索引
```

### 3. `user_roles`

明确的用户↔角色分配关系
用户与角色具有不同身份及生命周期，关系必须独立。当前UI单角色不成为数据库永久唯一限制；保留现有多角色关系能力。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `user_id` | `TEXT` | `NOT NULL` | 联合主键成员，所属用户 |
| `role_id` | `TEXT` | `NOT NULL` | 联合主键成员，分配的角色 |
| `assigned_by_user_id` | `TEXT` | `NULL allowed` | 系统首次初始化可空，否则记录分配者 |
| `assigned_at` | `TEXT` | `NOT NULL CHECK(assigned_at IS NULL OR (length(assigned_at)=27 AND assigned_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(assigned_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(assigned_at,1,10),'+0 days')=substr(assigned_at,1,10),0) AND CAST(substr(assigned_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(assigned_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(assigned_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
PRIMARY KEY(user_id,role_id)
FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE RESTRICT
FOREIGN KEY (assigned_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
```

索引：

```sql
CREATE INDEX ix_user_roles_role ON user_roles(role_id,user_id);
```

### 4. `devices`

当前设备归属、FCM注册值、启用及在线状态
一用户可有多设备；token可以在重新登录时改变当前归属。与逐次通知结果分开，避免历史发送结果阻止token转移。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `user_id` | `TEXT` | `NOT NULL` | 当前设备归属；同一 token 重新登录可转移到另一用户 |
| `installation_id` | `TEXT` | `CHECK(length(installation_id) <= 512)` | 沿用设备注册接口最大 512 字符 |
| `fcm_registration_value` | `TEXT` | `NOT NULL CHECK(length(fcm_registration_value) BETWEEN 20 AND 4096)` | 沿用 DeviceRegistration 20..4096 范围；token 长度由供应商决定，不臆测固定长度 |
| `platform` | `TEXT` | `NOT NULL CHECK(platform IN ('android','ios','web'))` | 现有受支持的平台集合 |
| `device_label` | `TEXT` | `CHECK(length(device_label) <= 100)` | 沿用接口最大 100 字符 |
| `enabled` | `INTEGER` | `NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))` | 失效 token 或账号停用后置 0 |
| `last_seen_at` | `TEXT` | `NOT NULL CHECK(last_seen_at IS NULL OR (length(last_seen_at)=27 AND last_seen_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(last_seen_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(last_seen_at,1,10),'+0 days')=substr(last_seen_at,1,10),0) AND CAST(substr(last_seen_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(last_seen_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(last_seen_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `updated_at` | `TEXT` | `NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
UNIQUE(fcm_registration_value)
CHECK(updated_at >= created_at)
FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
```

索引：

```sql
CREATE INDEX ix_devices_user_enabled ON devices(user_id,enabled);
```

### 5. `locations`

摄像头共享的地点主数据
名称标准化去重、建筑/楼层/说明由地点决定，不重复塞进每台摄像头。normalized_name可能因Unicode casefold扩长，不强设与name相同长度。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 保留地点历史 ID；与摄像头位置关联 |
| `name` | `TEXT` | `NOT NULL CHECK(length(trim(name)) > 0 AND length(name) <= 120)` | 当前摄像头位置接口上限 120；空位置应转为 Unspecified |
| `normalized_name` | `TEXT` | `NOT NULL CHECK(length(trim(normalized_name)) > 0)` | 唯一检索键，由服务器按现有空白/大小写规则生成；不能由客户端单独提交 |
| `building` | `TEXT` | `NULL allowed` | 现有 schema 无已定义业务长度；不臆造上限 |
| `floor` | `TEXT` | `NULL allowed` | 文本允许 G、B1、Mezzanine 等，并非只允许整数 |
| `description` | `TEXT` | `NULL allowed` | 保留地点说明，不臆造字符上限 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
UNIQUE(normalized_name)
```

索引：

```sql
-- 只有PK/UNIQUE自动索引，无额外查询索引
```

### 6. `cameras`

稳定摄像头身份、地点及分析状态
摄像头身份与可以替换的视频来源分离；与可产生多个警报的关系独立。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `location_id` | `TEXT` | `NOT NULL` | 位置为独立共享实体 |
| `name` | `TEXT` | `NOT NULL CHECK(length(trim(name)) > 0 AND length(name) <= 80)` | 沿用 Camera.name 的 1..80 字符范围 |
| `enabled` | `INTEGER` | `NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))` | 启用状态 |
| `status` | `TEXT` | `NOT NULL CHECK(status IN ('ready','analysing','paused','error'))` | 保持现有状态集合 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `updated_at` | `TEXT` | `NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE RESTRICT
CHECK(updated_at >= created_at)
```

索引：

```sql
CREATE INDEX ix_cameras_location ON cameras(location_id);
```

### 7. `media_files`

实际媒体文件的唯一元数据
文件可被来源/证据引用。文件与使用关系、证据生命周期不同。视频保存在filesystem，数据库只记录元数据；relative_path唯一。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `relative_path` | `TEXT` | `NOT NULL CHECK(length(trim(relative_path)) > 0)` | 服务器媒体根目录下的 POSIX 相对路径；SQL 防越界形态，服务仍须 realpath containment 检查 |
| `original_filename` | `TEXT` | `NULL allowed` | 上传文件名只作元数据；不作为服务器文件路径 |
| `media_kind` | `TEXT` | `NOT NULL CHECK(media_kind IN ('camera_source','evidence_clip','evidence_image'))` | 与当前文件分类保持一致 |
| `content_type` | `TEXT` | `NOT NULL CHECK(length(trim(content_type)) > 0)` | MIME 类型由媒体处理器验证，不臆造固定长度 |
| `size_bytes` | `INTEGER` | `NOT NULL CHECK(size_bytes >= 0)` | 字节大小非负；不存 BLOB 视频内容 |
| `sha256` | `TEXT` | `CHECK(sha256 IS NULL OR (length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'))` | 文件内容摘要，可空表示未计算；SHA-256为64位小写hex，保持现有存储/API格式，不做无必要编码迁移 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
UNIQUE(relative_path)
CHECK(substr(relative_path,1,1)<>'/' AND instr(relative_path,':')=0 AND instr(relative_path,char(92))=0 AND instr(relative_path,char(0))=0 AND instr('/'||relative_path||'/','/../')=0 AND instr('/'||relative_path||'/','/./')=0 AND instr(relative_path,'//')=0)
```

索引：

```sql
-- 只有PK/UNIQUE自动索引，无额外查询索引
```

### 8. `camera_sources`

摄像头输入来源及替换历史
来源替换修改active关系；文件元数据不重复。source并非证据：所有者、输入类型和保留/清理策略不同，因此不合并为多态media_usages。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `camera_id` | `TEXT` | `NOT NULL` | 来源所属摄像头 |
| `media_file_id` | `TEXT` | `NULL allowed` | 上传视频引用文件；RTSP不引用媒体文件 |
| `source_type` | `TEXT` | `NOT NULL CHECK(source_type IN ('uploaded_video','rtsp'))` | 两个现有来源类型 |
| `rtsp_endpoint_ref` | `TEXT` | `NULL allowed` | 仅保存服务器端点或secret引用，不保存URL密码 |
| `duration_seconds` | `REAL` | `NOT NULL DEFAULT 0 CHECK(duration_seconds >= 0)` | 完整输入视频时长；RTSP可为0 |
| `is_active` | `INTEGER` | `NOT NULL DEFAULT 1 CHECK(is_active IN (0,1))` | 每台摄像头最多一个当前来源 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE CASCADE
FOREIGN KEY (media_file_id) REFERENCES media_files(id) ON DELETE RESTRICT
CHECK((source_type='uploaded_video' AND media_file_id IS NOT NULL AND rtsp_endpoint_ref IS NULL) OR (source_type='rtsp' AND media_file_id IS NULL AND rtsp_endpoint_ref IS NOT NULL))
```

索引：

```sql
CREATE UNIQUE INDEX ux_active_camera_source ON camera_sources(camera_id) WHERE is_active=1;
CREATE INDEX ix_camera_source_file ON camera_sources(media_file_id) WHERE media_file_id IS NOT NULL;
```

### 9. `model_versions`

按任务保存不可变模型版本身份与部署来源
多个检测/描述/复核共享版本；独立于策略及prompt生命周期。不与完整配置、全局偏好混成runtime_resources。历史未记录metadata不能伪造。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `task` | `TEXT` | `NOT NULL CHECK(task IN ('violence','physical_contact_bullying','weapon','borderline_binary','scene_description'))` | 现有模型用途及save_alert按AlertType登记的身体霸凌；身体霸凌也可共享violence模型 |
| `model_name` | `TEXT` | `NOT NULL CHECK(length(trim(model_name)) > 0)` | 模型或供应商名称是可变长不透明字符串；不臆造80/255上限 |
| `version` | `TEXT` | `NOT NULL CHECK(length(trim(version)) > 0)` | 模型版本身份，保持历史标签 |
| `framework` | `TEXT` | `NULL allowed` | 历史模型登记可能未记录framework，允许空 |
| `quantization` | `TEXT` | `NULL allowed` | 量化描述没有当前固定枚举，允许空 |
| `checkpoint_ref` | `TEXT` | `NULL allowed` | 服务器控制的checkpoint逻辑引用；不能接收客户端电脑任意绝对路径 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
UNIQUE(task,model_name,version)
```

索引：

```sql
-- 只有PK/UNIQUE自动索引，无额外查询索引
```

### 10. `prompt_versions`

按用途保存提示词版本、内容及摘要
二元复核prompt与场景prompt分别版本化；被引用的历史内容不能原位改变。capture_state是迁移事实标记，不是替未来功能创建的独立entity。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `task` | `TEXT` | `NOT NULL CHECK(task IN ('borderline_binary','scene_description'))` | 现有两个VLM prompt任务 |
| `version` | `TEXT` | `NOT NULL CHECK(length(trim(version)) > 0)` | 保留原全局唯一prompt标签 |
| `capture_state` | `TEXT` | `NOT NULL DEFAULT 'metadata_only' CHECK(capture_state IN ('metadata_only','frozen'))` | 明确区分历史缺失内容与已冻结内容 |
| `content` | `TEXT` | `NULL allowed` | 真实prompt内容不按任意字符上限截断 |
| `content_sha256` | `TEXT` | `CHECK(content_sha256 IS NULL OR (length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'))` | 完整prompt的内容摘要，服务计算；SHA-256为64位小写hex，保持现有存储/API格式，不做无必要编码迁移 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
UNIQUE(version)
CHECK(capture_state='metadata_only' OR (content IS NOT NULL AND content_sha256 IS NOT NULL))
```

索引：

```sql
-- 只有PK/UNIQUE自动索引，无额外查询索引
```

### 11. `config_versions`

同一版本的完整运行策略快照
当前五张配置表的30个版本一一对应，save_settings同一事务写五行，get_settings同查。所有属性依赖同一配置版本，合并没有引入跨实体事实；减少缺一张明细就无法读取的故障点。模型/提示词仍独立。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用cfg_000001等既有配置ID |
| `version_number` | `INTEGER` | `NOT NULL CHECK(version_number >= 1)` | 唯一递增配置版本；不是用户输入的任意标识 |
| `created_by` | `TEXT` | `NOT NULL CHECK(length(trim(created_by)) > 0)` | 保留local-demo、migration标签等来源；不能假定每个字符串都是user_id |
| `created_by_user_id` | `TEXT` | `NULL allowed` | 能够确认是已认证用户时额外登记真实FK；后台/本地配置允许空 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `validation_profile` | `TEXT` | `NOT NULL DEFAULT 'runtime' CHECK(validation_profile IN ('runtime','historical'))` | 新配置沿用当前API类别/精度；历史阈值和类别不重舍入 |
| `detection_debug_mode_enabled` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(detection_debug_mode_enabled IN (0,1))` | 保持当前配置开关及默认关闭 |
| `default_sensitivity` | `TEXT` | `NOT NULL CHECK(default_sensitivity IN ('sensitive','moderate','certain'))` | 保留默认敏感度 |
| `violence_segment_seconds` | `REAL` | `NOT NULL CHECK(violence_segment_seconds >= 1 AND violence_segment_seconds <= 60)` | 沿用 ViolenceSettings 范围 |
| `violence_stride_seconds` | `REAL` | `NOT NULL CHECK(violence_stride_seconds >= 0 AND violence_stride_seconds <= 60)` | 必须大于 0 且不大于 segment；表级约束另外执行 |
| `violence_detector_input_frames` | `INTEGER` | `NOT NULL CHECK(violence_detector_input_frames >= 4 AND violence_detector_input_frames <= 128)` | 沿用输入帧预算 |
| `violence_sensitive_threshold` | `REAL` | `NOT NULL CHECK(violence_sensitive_threshold >= 0 AND violence_sensitive_threshold <= 1)` | 置信度阈值；保留历史精度并保证三个模式排序 |
| `violence_moderate_threshold` | `REAL` | `NOT NULL CHECK(violence_moderate_threshold >= 0 AND violence_moderate_threshold <= 1)` | 置信度阈值；保留历史精度并保证三个模式排序 |
| `violence_certain_threshold` | `REAL` | `NOT NULL CHECK(violence_certain_threshold >= 0 AND violence_certain_threshold <= 1)` | 置信度阈值；保留历史精度并保证三个模式排序 |
| `violence_cooldown_seconds` | `INTEGER` | `NOT NULL CHECK(violence_cooldown_seconds >= 0 AND violence_cooldown_seconds <= 3600)` | 保留当前兼容字段；不得恢复成丢弃新警报的旧策略 |
| `violence_merge_gap_seconds` | `REAL` | `NOT NULL CHECK(violence_merge_gap_seconds >= 0 AND violence_merge_gap_seconds <= 120)` | 沿用当前事件合并间隔可配置范围 |
| `weapon_sample_fps` | `REAL` | `NOT NULL CHECK(weapon_sample_fps >= 0.25 AND weapon_sample_fps <= 30)` | 沿用 WeaponSettings 采样范围 |
| `weapon_confidence_threshold` | `REAL` | `NOT NULL CHECK(weapon_confidence_threshold >= 0 AND weapon_confidence_threshold <= 1)` | 保留旧 global threshold；实际通知仍用类别/模式阈值 |
| `weapon_nms_candidate_floor` | `REAL` | `NOT NULL CHECK(weapon_nms_candidate_floor >= 0 AND weapon_nms_candidate_floor <= 0.1)` | 沿用 NMS 候选下限范围 |
| `weapon_nms_iou_threshold` | `REAL` | `NOT NULL CHECK(weapon_nms_iou_threshold >= 0.1 AND weapon_nms_iou_threshold <= 0.95)` | 沿用 NMS IoU 范围 |
| `weapon_n_required` | `INTEGER` | `NOT NULL CHECK(weapon_n_required >= 1 AND weapon_n_required <= 120)` | N-of-M 参数；另约束 N<=M |
| `weapon_m_window` | `INTEGER` | `NOT NULL CHECK(weapon_m_window >= 1 AND weapon_m_window <= 120)` | 滑动窗口 M |
| `weapon_cooldown_seconds` | `INTEGER` | `NOT NULL CHECK(weapon_cooldown_seconds >= 0 AND weapon_cooldown_seconds <= 3600)` | 保留兼容参数 |
| `weapon_merge_gap_seconds` | `REAL` | `NOT NULL CHECK(weapon_merge_gap_seconds >= 0 AND weapon_merge_gap_seconds <= 120)` | 武器事件合并间隔 |
| `weapon_class_thresholds_json` | `TEXT` | `NOT NULL CHECK(weapon_class_thresholds_json IS NULL OR (CASE WHEN json_valid(weapon_class_thresholds_json) THEN json_type(weapon_class_thresholds_json)='object' ELSE 0 END))` | 三种 sensitivity 的类别→数值阈值映射；不是外键关系。历史五类与 long_gun 名称保留，由触发器校验每个分数 |
| `vlm_model_id` | `TEXT` | `NOT NULL  CHECK(length(trim(vlm_model_id))>0)` | 外部模型供应商标识，沿用 VlmSettings.model_id；不是内部资源 ID |
| `vlm_quantization` | `TEXT` | `NOT NULL  CHECK(length(trim(vlm_quantization))>0)` | VLM 量化设置 |
| `vlm_input_fps` | `REAL` | `NOT NULL CHECK(vlm_input_fps >= 0.25 AND vlm_input_fps <= 12)` | 沿用 VLM 输入采样范围 |
| `vlm_video_token_budget` | `INTEGER` | `NOT NULL CHECK(vlm_video_token_budget >= 256 AND vlm_video_token_budget <= 16384)` | 沿用 token 预算 |
| `vlm_max_output_tokens` | `INTEGER` | `NOT NULL CHECK(vlm_max_output_tokens >= 32 AND vlm_max_output_tokens <= 2048)` | 沿用输出 token 预算 |
| `vlm_scene_description_frames` | `INTEGER` | `NOT NULL CHECK(vlm_scene_description_frames >= 4 AND vlm_scene_description_frames <= 128)` | 还必须为偶数；与复核 input_fps 独立 |
| `vlm_borderline_review_enabled` | `INTEGER` | `NOT NULL CHECK(vlm_borderline_review_enabled IN (0,1))` | Web master switch；不同于账号接收偏好 |
| `vlm_scene_description_enabled` | `INTEGER` | `NOT NULL CHECK(vlm_scene_description_enabled IN (0,1))` | 场景描述开关 |
| `binary_prompt_version_id` | `TEXT` | `NOT NULL ` | 真实外键，指向 prompt/borderline_binary 资源 |
| `scene_prompt_version_id` | `TEXT` | `NOT NULL ` | 真实外键，指向 prompt/scene_description 资源 |
| `same_audience_repeat_after_seconds` | `INTEGER` | `NOT NULL CHECK(same_audience_repeat_after_seconds >= 15 AND same_audience_repeat_after_seconds <= 3600)` | 当前 Config API 的第一次重复范围 |
| `all_users_escalation_after_seconds` | `INTEGER` | `NOT NULL CHECK(all_users_escalation_after_seconds >= 30 AND all_users_escalation_after_seconds <= 7200)` | 当前全体升级范围；必须晚于首次重复 |

表级PK/FK/CHECK：

```sql
UNIQUE(version_number)
FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
FOREIGN KEY (binary_prompt_version_id) REFERENCES prompt_versions(id) ON DELETE RESTRICT
FOREIGN KEY (scene_prompt_version_id) REFERENCES prompt_versions(id) ON DELETE RESTRICT
CHECK(violence_stride_seconds>0 AND violence_stride_seconds<=violence_segment_seconds)
CHECK(violence_sensitive_threshold<=violence_moderate_threshold AND violence_moderate_threshold<=violence_certain_threshold)
CHECK(weapon_n_required<=weapon_m_window)
CHECK(vlm_scene_description_frames%2=0)
CHECK(all_users_escalation_after_seconds>same_audience_repeat_after_seconds)
```

索引：

```sql
CREATE INDEX ix_config_creator ON config_versions(created_by_user_id) WHERE created_by_user_id IS NOT NULL;
CREATE INDEX ix_config_binary_prompt ON config_versions(binary_prompt_version_id);
CREATE INDEX ix_config_scene_prompt ON config_versions(scene_prompt_version_id);
```

### 12. `alerts`

稳定事件及其当前唯一手机处理状态
workflow是每个alert恰好一条的当前状态，不具有独立身份/历史生命周期；并入alert。去掉可以由workflow确定的旧status重复真值，DTO仍返回原status。检测统计继续从events聚合；不要加入description或review次数。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `camera_id` | `TEXT` | `NOT NULL` | 事件所属摄像头 |
| `incident_type` | `TEXT` | `NOT NULL CHECK(incident_type IN ('violence','physical_contact_bullying','weapon'))` | 现有三种事件 |
| `workflow_status` | `TEXT` | `NOT NULL CHECK(workflow_status IN ('awaiting_confirmation','confirmed_awaiting_handler','in_progress','resolved','dismissed','legacy_in_review','legacy_escalated'))` | 唯一当前状态来源；旧API status由固定映射计算 |
| `sensitivity_mode` | `TEXT` | `NOT NULL CHECK(sensitivity_mode IN ('sensitive','moderate','certain'))` | 保留历史标签；不用于限制Active有角色用户浏览 |
| `is_borderline_promoted` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(is_borderline_promoted IN (0,1))` | 保留提升标记 |
| `is_provisional` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(is_provisional IN (0,1))` | 旧记录兼容；不恢复已停用的提前告警行为 |
| `borderline_review_status` | `TEXT` | `NOT NULL CHECK(borderline_review_status IN ('not_applicable','disabled','queued','running','positive','negative','failed','cancelled','legacy_unknown'))` | 用于异步复核聚合展示；不同于人工处理状态 |
| `evidence_status` | `TEXT` | `NOT NULL CHECK(evidence_status IN ('preparing','available','failed','legacy_unknown'))` | 证据准备状态，与事件解决状态独立 |
| `opening_config_version_id` | `TEXT` | `NOT NULL` | 开场策略版本 |
| `opening_model_version_id` | `TEXT` | `NOT NULL` | 开场detector模型版本 |
| `binary_prompt_version_id` | `TEXT` | `NULL allowed` | 可选开场复核prompt版本 |
| `handler_user_id` | `TEXT` | `NULL allowed` | 当前处理者；停用账号不删除其历史关联 |
| `active_phase_id` | `TEXT` | `NULL allowed` | 指向同事件且仍active的phase |
| `broadcast_role_snapshot_json` | `TEXT` | `NOT NULL DEFAULT '[]' CHECK(broadcast_role_snapshot_json IS NULL OR (CASE WHEN json_valid(broadcast_role_snapshot_json) THEN json_type(broadcast_role_snapshot_json)='array' ELSE 0 END))` | 当时选择的角色ID展示快照；运行收件资格使用notification_phase_roles的真实FK |
| `revision` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(revision >= 0)` | 工作流程乐观并发token |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `workflow_updated_at` | `TEXT` | `NOT NULL CHECK(workflow_updated_at IS NULL OR (length(workflow_updated_at)=27 AND workflow_updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(workflow_updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(workflow_updated_at,1,10),'+0 days')=substr(workflow_updated_at,1,10),0) AND CAST(substr(workflow_updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(workflow_updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(workflow_updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `resolved_at` | `TEXT` | `CHECK(resolved_at IS NULL OR (length(resolved_at)=27 AND resolved_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(resolved_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(resolved_at,1,10),'+0 days')=substr(resolved_at,1,10),0) AND CAST(substr(resolved_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(resolved_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(resolved_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
UNIQUE(id,camera_id)
FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE RESTRICT
FOREIGN KEY (opening_config_version_id) REFERENCES config_versions(id) ON DELETE RESTRICT
FOREIGN KEY (opening_model_version_id) REFERENCES model_versions(id) ON DELETE RESTRICT
FOREIGN KEY (binary_prompt_version_id) REFERENCES prompt_versions(id) ON DELETE RESTRICT
FOREIGN KEY (handler_user_id) REFERENCES users(id) ON DELETE RESTRICT
FOREIGN KEY (active_phase_id,id) REFERENCES notification_phases(id,alert_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
CHECK(workflow_updated_at>=created_at)
CHECK(resolved_at IS NULL OR resolved_at>=created_at)
CHECK(workflow_status<>'in_progress' OR handler_user_id IS NOT NULL)
```

索引：

```sql
CREATE INDEX ix_alert_camera_workflow ON alerts(camera_id,workflow_status);
CREATE INDEX ix_alert_active_phase ON alerts(active_phase_id) WHERE active_phase_id IS NOT NULL;
CREATE INDEX ix_alert_opening_config ON alerts(opening_config_version_id);
CREATE INDEX ix_alert_opening_model ON alerts(opening_model_version_id);
CREATE INDEX ix_alert_handler ON alerts(handler_user_id) WHERE handler_user_id IS NOT NULL;
CREATE INDEX ix_alert_prompt ON alerts(binary_prompt_version_id) WHERE binary_prompt_version_id IS NOT NULL;
```

### 13. `detection_events`

一次被警报接纳的检测事实
检测事实与可运行/失败/重试的VLM任务不同；不合成inference_runs。多次检测的分数、来源窗口及版本不能被事件最高分覆盖。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `alert_id` | `TEXT` | `NOT NULL` | 可聚合进同一事件，但每次检测独立保存 |
| `model_version_id` | `TEXT` | `NOT NULL` | 该次模型版本 |
| `config_version_id` | `TEXT` | `NOT NULL` | 该次策略版本，不冒充opening版本 |
| `confidence` | `REAL` | `NOT NULL CHECK(confidence >= 0 AND confidence <= 1)` | 模型概率分数 |
| `decision_source` | `TEXT` | `NOT NULL CHECK(decision_source IN ('threshold','vlm_promoted','weapon_n_of_m'))` | 现有判定来源 |
| `source_start_seconds` | `REAL` | `CHECK(source_start_seconds >= 0)` | 原视频窗口起点，可空表示旧记录未知 |
| `source_end_seconds` | `REAL` | `CHECK(source_end_seconds >= 0)` | 原视频窗口终点 |
| `detected_at` | `TEXT` | `NOT NULL CHECK(detected_at IS NULL OR (length(detected_at)=27 AND detected_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(detected_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(detected_at,1,10),'+0 days')=substr(detected_at,1,10),0) AND CAST(substr(detected_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(detected_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(detected_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `qualifications_json` | `TEXT` | `NOT NULL DEFAULT '{}' CHECK(qualifications_json IS NULL OR (CASE WHEN json_valid(qualifications_json) THEN json_type(qualifications_json)='object' ELSE 0 END))` | 当时资格计算结果，不能用来替代当前权限 |
| `weapon_class_scores_json` | `TEXT` | `NOT NULL DEFAULT '{}' CHECK(weapon_class_scores_json IS NULL OR (CASE WHEN json_valid(weapon_class_scores_json) THEN json_type(weapon_class_scores_json)='object' ELSE 0 END))` | 类别→概率映射；触发器逐值限制数值0..1 |

表级PK/FK/CHECK：

```sql
FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
FOREIGN KEY (model_version_id) REFERENCES model_versions(id) ON DELETE RESTRICT
FOREIGN KEY (config_version_id) REFERENCES config_versions(id) ON DELETE RESTRICT
CHECK(source_start_seconds IS NULL OR source_end_seconds IS NULL OR source_end_seconds>=source_start_seconds)
```

索引：

```sql
CREATE INDEX ix_detection_alert_time ON detection_events(alert_id,detected_at);
CREATE INDEX ix_detection_model ON detection_events(model_version_id);
CREATE INDEX ix_detection_config ON detection_events(config_version_id);
```

### 14. `borderline_reviews`

一次Borderline复核的输入、排队执行、结论及补充通知资格
原task与review审计主要镜像同一复核结果，属于同一review生命周期。保留audit_only形式来表达旧记录真正缺失的数据，不把legacy结果恢复成可执行任务；detector/description仍独立。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 任务沿用reviewtask ID，旧独立审计沿用review ID |
| `record_origin` | `TEXT` | `NOT NULL CHECK(record_origin IN ('task','audit_only'))` | 历史旧审计没有任务输入/执行时间；必须明确未知，不能捏造任务 |
| `legacy_review_id` | `TEXT` | `NULL allowed` | 能证明同一事实的旧审计镜像合入task时保留原ID；不靠最近时间猜测合并 |
| `camera_id` | `TEXT` | `NOT NULL` | 复核来源摄像头 |
| `alert_id` | `TEXT` | `NULL allowed` | task必填；旧negative审计允许没有警报 |
| `detection_event_id` | `TEXT` | `NULL allowed` | task必填且唯一；旧审计无已记录窗口key时可空 |
| `model_version_id` | `TEXT` | `NOT NULL` | 实际复核模型版本，用FK取代任务中的松散标签 |
| `prompt_version_id` | `TEXT` | `NOT NULL` | 实际二元复核prompt版本 |
| `config_version_id` | `TEXT` | `NOT NULL` | 策略版本FK |
| `detector_confidence` | `REAL` | `NOT NULL CHECK(detector_confidence >= 0 AND detector_confidence <= 1)` | 保留输入detector分数 |
| `source_start_seconds` | `REAL` | `CHECK(source_start_seconds >= 0)` | task必填；旧审计可以未知 |
| `source_end_seconds` | `REAL` | `CHECK(source_end_seconds >= 0)` | 结束不得早于开始 |
| `context_path` | `TEXT` | `NULL allowed` | 服务器控制输入引用；迁移老绝对路径先验证归属，不允许客户端任意路径 |
| `status` | `TEXT` | `NOT NULL CHECK(status IN ('queued','running','positive','negative','failed','cancelled'))` | 复核生命周期；audit_only只允许positive/negative |
| `attempt_count` | `INTEGER` | `CHECK(attempt_count >= 0 AND attempt_count <= 3)` | task尝试最多3；旧审计未记录则NULL |
| `raise_alert` | `INTEGER` | `CHECK(raise_alert IN (0,1))` | 尚未得出结论为空；正/负分别1/0 |
| `uncertainty` | `TEXT` | `CHECK(uncertainty IN ('low','medium','high'))` | 当前复核输出枚举 |
| `brief_reason` | `TEXT` | `NULL allowed` | 保留原因，不臆造没有接口依据的长度 |
| `error_code` | `TEXT` | `NULL allowed` | 错误分类允许扩展，不只保存一个failed布尔值 |
| `vlm_settings_json` | `TEXT` | `CHECK(vlm_settings_json IS NULL OR (CASE WHEN json_valid(vlm_settings_json) THEN json_type(vlm_settings_json)='object' ELSE 0 END))` | 提交给worker的真正VLM参数快照，可能含对全局配置的覆盖；逐字段校验 |
| `supplemental_notification_status` | `TEXT` | `NOT NULL DEFAULT 'not_applicable' CHECK(supplemental_notification_status IN ('not_applicable','eligible','handled','suppressed_disabled'))` | 持久标记防止重启/历史导入重放补充推送 |
| `created_at` | `TEXT` | `CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `started_at` | `TEXT` | `CHECK(started_at IS NULL OR (length(started_at)=27 AND started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(started_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(started_at,1,10),'+0 days')=substr(started_at,1,10),0) AND CAST(substr(started_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(started_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(started_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `completed_at` | `TEXT` | `CHECK(completed_at IS NULL OR (length(completed_at)=27 AND completed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(completed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(completed_at,1,10),'+0 days')=substr(completed_at,1,10),0) AND CAST(substr(completed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(completed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(completed_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `reviewed_at` | `TEXT` | `CHECK(reviewed_at IS NULL OR (length(reviewed_at)=27 AND reviewed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(reviewed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(reviewed_at,1,10),'+0 days')=substr(reviewed_at,1,10),0) AND CAST(substr(reviewed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(reviewed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(reviewed_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 旧审计的原reviewed_at；task完成时间与镜像入账时间不同，分别保留 |

表级PK/FK/CHECK：

```sql
UNIQUE(detection_event_id)
UNIQUE(legacy_review_id)
FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE RESTRICT
FOREIGN KEY (alert_id,camera_id) REFERENCES alerts(id,camera_id) ON DELETE RESTRICT
FOREIGN KEY (detection_event_id) REFERENCES detection_events(id) ON DELETE RESTRICT
FOREIGN KEY (model_version_id) REFERENCES model_versions(id) ON DELETE RESTRICT
FOREIGN KEY (prompt_version_id) REFERENCES prompt_versions(id) ON DELETE RESTRICT
FOREIGN KEY (config_version_id) REFERENCES config_versions(id) ON DELETE RESTRICT
CHECK(source_start_seconds IS NULL OR source_end_seconds IS NULL OR source_end_seconds>=source_start_seconds)
CHECK((record_origin='task' AND alert_id IS NOT NULL AND detection_event_id IS NOT NULL AND context_path IS NOT NULL AND length(trim(context_path))>0 AND vlm_settings_json IS NOT NULL AND attempt_count IS NOT NULL AND source_start_seconds IS NOT NULL AND source_end_seconds IS NOT NULL AND created_at IS NOT NULL) OR (record_origin='audit_only' AND detection_event_id IS NULL AND context_path IS NULL AND vlm_settings_json IS NULL AND attempt_count IS NULL AND created_at IS NULL AND started_at IS NULL AND completed_at IS NULL AND reviewed_at IS NOT NULL AND status IN ('positive','negative') AND supplemental_notification_status='not_applicable'))
CHECK(status NOT IN ('positive','negative') OR raise_alert IS NOT NULL)
CHECK(status<>'positive' OR raise_alert=1)
CHECK(status<>'negative' OR raise_alert=0)
CHECK(supplemental_notification_status='not_applicable' OR (record_origin='task' AND status='positive'))
CHECK(started_at IS NULL OR (created_at IS NOT NULL AND started_at>=created_at))
CHECK(completed_at IS NULL OR (created_at IS NOT NULL AND completed_at>=created_at AND (started_at IS NULL OR completed_at>=started_at)))
```

索引：

```sql
CREATE INDEX ix_review_queue ON borderline_reviews(status,created_at) WHERE record_origin='task' AND status IN ('queued','running');
CREATE INDEX ix_review_alert_time ON borderline_reviews(alert_id,created_at);
CREATE INDEX ix_review_camera ON borderline_reviews(camera_id);
CREATE INDEX ix_review_model ON borderline_reviews(model_version_id);
CREATE INDEX ix_review_prompt ON borderline_reviews(prompt_version_id);
CREATE INDEX ix_review_config ON borderline_reviews(config_version_id);
```

### 15. `evidence_records`

警报证据关系、来源区间及保留期限
证据有自身captured/retention事实；与camera_sources的输入类型/active关系不同，因此独立。删除证据关系并不让DB自动删除物理文件，服务仍协调filesystem清理。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `alert_id` | `TEXT` | `NOT NULL` | 关联事件 |
| `media_file_id` | `TEXT` | `NOT NULL` | 一份文件对应最多一条证据关系，保留原UNIQUE |
| `evidence_type` | `TEXT` | `NOT NULL CHECK(evidence_type IN ('violence_clip','bullying_clip','weapon_screenshot'))` | 当前证据类型 |
| `source_start_seconds` | `REAL` | `CHECK(source_start_seconds >= 0)` | 来源视频起点；旧记录未知允许空 |
| `source_end_seconds` | `REAL` | `CHECK(source_end_seconds >= 0)` | 来源视频终点 |
| `captured_at` | `TEXT` | `NOT NULL CHECK(captured_at IS NULL OR (length(captured_at)=27 AND captured_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(captured_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(captured_at,1,10),'+0 days')=substr(captured_at,1,10),0) AND CAST(substr(captured_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(captured_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(captured_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `delete_after` | `TEXT` | `CHECK(delete_after IS NULL OR (length(delete_after)=27 AND delete_after GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(delete_after,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(delete_after,1,10),'+0 days')=substr(delete_after,1,10),0) AND CAST(substr(delete_after,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(delete_after,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(delete_after,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
UNIQUE(media_file_id)
FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
FOREIGN KEY (media_file_id) REFERENCES media_files(id) ON DELETE RESTRICT
CHECK(source_start_seconds IS NULL OR source_end_seconds IS NULL OR source_end_seconds>=source_start_seconds)
CHECK(delete_after IS NULL OR delete_after>=captured_at)
```

索引：

```sql
CREATE INDEX ix_evidence_alert ON evidence_records(alert_id);
CREATE INDEX ix_evidence_retention ON evidence_records(delete_after) WHERE delete_after IS NOT NULL;
```

### 16. `vlm_descriptions`

场景描述生成状态、输出及重新生成历史
描述是每个事件可以重试/重新生成的一系列输出，并非detector事实或二元复核。读取最新ready结果；没有必要与其他推理记录共享大表和状态枚举。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `alert_id` | `TEXT` | `NOT NULL` | 所属事件 |
| `model_version_id` | `TEXT` | `NOT NULL` | 场景模型版本 |
| `prompt_version_id` | `TEXT` | `NULL allowed` | 旧记录未知prompt可空 |
| `config_version_id` | `TEXT` | `NULL allowed` | 新增时记录实际policy；旧表未记录时NULL，不能编造opening版本 |
| `status` | `TEXT` | `NOT NULL CHECK(status IN ('pending','ready','failed','disabled'))` | 保持现有DescriptionStatus |
| `output_format` | `TEXT` | `NOT NULL DEFAULT 'none' CHECK(output_format IN ('none','summary','raw_json','text'))` | 格式决定适用约束，保留当前fallback读取 |
| `structured_json` | `TEXT` | `CHECK(structured_json IS NULL OR (CASE WHEN json_valid(structured_json) THEN 1 ELSE 0 END))` | 已验证summary或可兼容读取的原始JSON，不一律强迫旧结构为summary |
| `display_text` | `TEXT` | `NULL allowed` | 原显示文本；仅summary.description受120上限，原text回退不能被截断 |
| `error_code` | `TEXT` | `NULL allowed` | 失败原因分类 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `completed_at` | `TEXT` | `CHECK(completed_at IS NULL OR (length(completed_at)=27 AND completed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(completed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(completed_at,1,10),'+0 days')=substr(completed_at,1,10),0) AND CAST(substr(completed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(completed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(completed_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
FOREIGN KEY (model_version_id) REFERENCES model_versions(id) ON DELETE RESTRICT
FOREIGN KEY (prompt_version_id) REFERENCES prompt_versions(id) ON DELETE RESTRICT
FOREIGN KEY (config_version_id) REFERENCES config_versions(id) ON DELETE RESTRICT
CHECK(completed_at IS NULL OR completed_at>=created_at)
CHECK((output_format='none' AND structured_json IS NULL AND display_text IS NULL) OR (output_format IN ('summary','raw_json') AND structured_json IS NOT NULL) OR (output_format='text' AND structured_json IS NULL AND display_text IS NOT NULL))
CHECK(status<>'ready' OR output_format<>'none')
```

索引：

```sql
CREATE INDEX ix_description_alert_time ON vlm_descriptions(alert_id,created_at DESC);
CREATE INDEX ix_description_model ON vlm_descriptions(model_version_id);
CREATE INDEX ix_description_prompt ON vlm_descriptions(prompt_version_id) WHERE prompt_version_id IS NOT NULL;
CREATE INDEX ix_description_config ON vlm_descriptions(config_version_id) WHERE config_version_id IS NOT NULL;
```

### 17. `alert_actions`

用户对事件的人工处理历史及幂等请求
旧alert_actions和新workflow_events都是人的一次事件响应；统一历史事件模型。audit_logs保留另一条行政审计事实，不按相同时间乱删。已读关系仍单独保存。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `alert_id` | `TEXT` | `NOT NULL` | 被处理事件 |
| `user_id` | `TEXT` | `NOT NULL` | 操作人必须真实存在；历史身份不硬删除 |
| `action` | `TEXT` | `NOT NULL CHECK(action IN ('acknowledge','review','dismiss','resolve','escalate','dismiss_false_alarm','confirm_only','confirm_and_handle','confirm_resolved','take_ownership','simulated_emergency_call'))` | 完整旧/新操作集合 |
| `record_origin` | `TEXT` | `NOT NULL CHECK(record_origin IN ('legacy','workflow'))` | 保持Mobile isLegacy语义；不是不同类型业务实体 |
| `note` | `TEXT` | `NULL allowed` | 新workflow沿用1000字符上限；旧无限制note保留原值 |
| `broadcast_role_snapshot_json` | `TEXT` | `CHECK(broadcast_role_snapshot_json IS NULL OR (CASE WHEN json_valid(broadcast_role_snapshot_json) THEN json_type(broadcast_role_snapshot_json)='array' ELSE 0 END))` | 用户当时选择的角色ID历史快照；实际Phase B关系另有FK |
| `idempotency_key` | `TEXT` | `NULL allowed` | 新workflow请求8..120；旧操作无key允许空 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE RESTRICT
FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT
CHECK(record_origin='legacy' OR (idempotency_key IS NOT NULL AND length(idempotency_key) BETWEEN 8 AND 120 AND broadcast_role_snapshot_json IS NOT NULL AND (note IS NULL OR length(note)<=1000)))
```

索引：

```sql
CREATE UNIQUE INDEX ux_alert_action_idempotency ON alert_actions(alert_id,idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX ix_alert_actions_alert_time ON alert_actions(alert_id,created_at DESC);
CREATE INDEX ix_alert_actions_user ON alert_actions(user_id);
```

### 18. `alert_reads`

每用户每警报的首次浏览状态
这是独立的用户↔警报状态关系，不是人工响应或行政审计；保留小而明确的联合主键。read不停止提醒。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `user_id` | `TEXT` | `NOT NULL` | 每人独立，不能放在alerts共享行 |
| `alert_id` | `TEXT` | `NOT NULL` | 浏览目标 |
| `read_at` | `TEXT` | `NOT NULL CHECK(read_at IS NULL OR (length(read_at)=27 AND read_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(read_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(read_at,1,10),'+0 days')=substr(read_at,1,10),0) AND CAST(substr(read_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(read_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(read_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
PRIMARY KEY(user_id,alert_id)
FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
```

索引：

```sql
CREATE INDEX ix_alert_reads_alert ON alert_reads(alert_id);
```

### 19. `notifications`

面向用户的逻辑通知意图、内容和稳定幂等身份
通知作为可浏览内容有独立生命周期；发送任务失败/重试不应改变消息身份。保留notifications，不把跳过阶段造成为假的隐藏消息。payload镜像当前一致，采用一份canonical值；若切换时发现差异先阻断核对并归档原值。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `alert_id` | `TEXT` | `NOT NULL` | 所属警报 |
| `message_kind` | `TEXT` | `NOT NULL CHECK(message_kind IN ('initial','broadcast','decision_update','phase_reminder','late_repeat','manual','legacy'))` | 消息用途；同一领域的有限分类，不混入其他实体 |
| `event_key` | `TEXT` | `NULL allowed` | 由旧outbox移入的稳定业务幂等键；旧直接发送通知可空；队列清理也不能失去去重身份 |
| `visibility_policy` | `TEXT` | `NOT NULL DEFAULT 'restricted' CHECK(visibility_policy IN ('restricted','broadcast'))` | 显式通知可见范围；原无recipient的旧通知导入broadcast，不把新名单为空解释为全员授权 |
| `phase_id` | `TEXT` | `NULL allowed` | phase_reminder/late_repeat必须引用真实phase |
| `reminder_stage` | `INTEGER` | `CHECK(reminder_stage >= 1 AND reminder_stage <= 2)` | 对应阶段1/2，仅提醒消息适用 |
| `title` | `TEXT` | `NOT NULL` | 保持现有标题；协议最终字节大小由sender验证，不臆造字符上限 |
| `body` | `TEXT` | `NOT NULL` | 通知正文，保存真实内容，不为压表数截断 |
| `data_payload_json` | `TEXT` | `NOT NULL CHECK(data_payload_json IS NULL OR (CASE WHEN json_valid(data_payload_json) THEN json_type(data_payload_json)='object' ELSE 0 END))` | 逻辑通知与实际发送使用同一canonical payload；冻结的alertId等不是授权来源 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
UNIQUE(event_key)
UNIQUE(id,alert_id)
FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
FOREIGN KEY (phase_id,alert_id) REFERENCES notification_phases(id,alert_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
CHECK((message_kind IN ('phase_reminder','late_repeat') AND phase_id IS NOT NULL AND reminder_stage IS NOT NULL) OR (message_kind NOT IN ('phase_reminder','late_repeat') AND phase_id IS NULL AND reminder_stage IS NULL))
```

索引：

```sql
CREATE INDEX ix_notifications_alert_time ON notifications(alert_id,created_at DESC);
CREATE INDEX ix_notifications_phase ON notifications(phase_id,reminder_stage) WHERE phase_id IS NOT NULL;
CREATE UNIQUE INDEX ux_main_stage_message ON notifications(phase_id,reminder_stage) WHERE message_kind='phase_reminder';
```

### 20. `notification_outbox`

某条通知的持久发送任务、领取与重试状态
这是可领取、重试和恢复的任务，和用户看到的消息不同；保留独立表有明确运行职责。移除重复event_key及payload：幂等键与正文归notifications。只统一数据真值，不为了减少表数混合生命周期。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 保留现有dispatch/outbox ID，FCM dispatchId与内部队列引用不改变 |
| `notification_id` | `TEXT` | `NOT NULL` | 一条消息最多一个当前发送任务；内容/默认payload通过FK读取，不再重复 |
| `channel_id` | `TEXT` | `NOT NULL CHECK(length(trim(channel_id)) > 0)` | Android beep channel的逻辑ID，与正文不同用途，不设无依据长度 |
| `status` | `TEXT` | `NOT NULL CHECK(status IN ('pending','dispatching','sent','partial','failed','no_recipients'))` | 现有outbox生命周期 |
| `attempt_count` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(attempt_count >= 0)` | 领取次数；正常失败重试阈值3，crash/lease恢复可增加领取数，不把它误作设备实际发送次数或硬截断 |
| `next_attempt_at` | `TEXT` | `NOT NULL CHECK(next_attempt_at IS NULL OR (length(next_attempt_at)=27 AND next_attempt_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(next_attempt_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(next_attempt_at,1,10),'+0 days')=substr(next_attempt_at,1,10),0) AND CAST(substr(next_attempt_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(next_attempt_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(next_attempt_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `claimed_at` | `TEXT` | `CHECK(claimed_at IS NULL OR (length(claimed_at)=27 AND claimed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(claimed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(claimed_at,1,10),'+0 days')=substr(claimed_at,1,10),0) AND CAST(substr(claimed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(claimed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(claimed_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `last_error_code` | `TEXT` | `NULL allowed` | 队列级错误区别于逐设备错误与阶段错误 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `updated_at` | `TEXT` | `NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
UNIQUE(notification_id)
FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE
CHECK(updated_at>=created_at)
```

索引：

```sql
CREATE INDEX ix_outbox_due ON notification_outbox(status,next_attempt_at);
```

### 21. `notification_recipients`

通知展示名单与实际排队用户快照
旧队列与feed使用同一用户名单，却保存两套关系。合并为一行两个有意义标记，可保留两名单不同时的事实；live FK仍明确。原UNIQUE(notification_id,role_id,user_id)含NULL不能可靠去重，改为两个partial UNIQUE。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `INTEGER` | `PRIMARY KEY` | 关系行没有公开ID需求；用SQLite内部整数键，修复旧表无PK问题 |
| `notification_id` | `TEXT` | `NOT NULL` | 所属逻辑消息 |
| `user_id` | `TEXT` | `NULL allowed` | 用户收件人；和role_id互斥 |
| `role_id` | `TEXT` | `NULL allowed` | 旧role可见名单；和user_id互斥 |
| `feed_visible` | `INTEGER` | `NOT NULL DEFAULT 1 CHECK(feed_visible IN (0,1))` | 该成员是否属于Mobile通知展示名单 |
| `queued_target` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(queued_target IN (0,1))` | 用户是否属于实际outbox目标快照；role行不能冒充已物化用户快照 |

表级PK/FK/CHECK：

```sql
FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE
FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
CHECK((user_id IS NOT NULL AND role_id IS NULL) OR (user_id IS NULL AND role_id IS NOT NULL))
CHECK(feed_visible=1 OR queued_target=1)
CHECK(role_id IS NULL OR queued_target=0)
```

索引：

```sql
CREATE UNIQUE INDEX ux_notification_recipient_user ON notification_recipients(notification_id,user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX ux_notification_recipient_role ON notification_recipients(notification_id,role_id) WHERE role_id IS NOT NULL;
CREATE INDEX ix_notification_recipient_user ON notification_recipients(user_id,feed_visible);
CREATE INDEX ix_notification_recipient_role ON notification_recipients(role_id,feed_visible);
```

### 22. `notification_deliveries`

每通知每设备的最新实际发送结果
当前worker同一事务双写两份结果，115份镜像逐字段一致。统一一条消息/设备事实；不采用会因设备换用户而失效的永久(device_id,user_id)复合FK。新插入在服务事务校验当时owner，历史NULL诚实表达未知。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 原notification_deliveries.id继续保留；仅旧outbox有行时创建明确映射的新ID |
| `notification_id` | `TEXT` | `NOT NULL` | 所属消息；原outbox_id可通过outbox.notification_id唯一关系回查 |
| `device_id` | `TEXT` | `NOT NULL` | 目标设备身份，历史设备只停用而不硬删除 |
| `recipient_user_id` | `TEXT` | `NULL allowed` | 新发送捕获当时用户；旧表未保存时NULL，不能猜成设备当前owner |
| `status` | `TEXT` | `NOT NULL CHECK(status IN ('queued','sent','failed','invalid_device'))` | 完整原delivery状态，和队列聚合状态分开 |
| `provider_message_id` | `TEXT` | `NULL allowed` | FCM返回的不透明ID |
| `attempt_count` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(attempt_count >= 0)` | 设备实际尝试计数，不与队列领取次数混用、不把两镜像次数相加 |
| `last_attempt_at` | `TEXT` | `CHECK(last_attempt_at IS NULL OR (length(last_attempt_at)=27 AND last_attempt_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(last_attempt_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(last_attempt_at,1,10),'+0 days')=substr(last_attempt_at,1,10),0) AND CAST(substr(last_attempt_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(last_attempt_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(last_attempt_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `error_code` | `TEXT` | `NULL allowed` | 该设备的错误，而非共享消息错误 |
| `updated_at` | `TEXT` | `CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 新结果及原outbox镜像有实际更新时间；仅旧feed有的65条未保存此字段，保持NULL，不能冒充last_attempt_at；新写入由服务必填 |

表级PK/FK/CHECK：

```sql
UNIQUE(notification_id,device_id)
FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE
FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE RESTRICT
FOREIGN KEY (recipient_user_id) REFERENCES users(id) ON DELETE RESTRICT
CHECK(status NOT IN ('sent','failed','invalid_device') OR last_attempt_at IS NOT NULL)
```

索引：

```sql
CREATE INDEX ix_delivery_device ON notification_deliveries(device_id);
CREATE INDEX ix_delivery_recipient ON notification_deliveries(recipient_user_id) WHERE recipient_user_id IS NOT NULL;
```

### 23. `notification_reads`

每用户每消息的首次已读关系
通知读取与警报浏览是不同用户行为，不合并到共享read字段，也不混进不可删的行政审计。mark-all-read仍逐用户执行。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `notification_id` | `TEXT` | `NOT NULL` | 读取的消息 |
| `user_id` | `TEXT` | `NOT NULL` | 读取者 |
| `read_at` | `TEXT` | `NOT NULL CHECK(read_at IS NULL OR (length(read_at)=27 AND read_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(read_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(read_at,1,10),'+0 days')=substr(read_at,1,10),0) AND CAST(substr(read_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(read_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(read_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
PRIMARY KEY(notification_id,user_id)
FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE
FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
```

索引：

```sql
CREATE INDEX ix_notification_reads_user ON notification_reads(user_id,read_at);
```

### 24. `alert_notification_audience`

每警报每用户的初次通知资格及已送达事实
此关系跨越多条通知，负责初次推送去重与阶段1真实audience。不能与单条消息recipient混为同一事实；资格缓存不是浏览授权。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `alert_id` | `TEXT` | `NOT NULL` | 警报身份 |
| `user_id` | `TEXT` | `NOT NULL` | 该警报累计具备初次通知资格的用户 |
| `qualification_reasons_json` | `TEXT` | `NOT NULL DEFAULT '[]' CHECK(qualification_reasons_json IS NULL OR (CASE WHEN json_valid(qualification_reasons_json) THEN json_type(qualification_reasons_json)='array' ELSE 0 END))` | 当时ordinary_threshold/borderline_review/legacy等资格来源；逐元素验证字符串 |
| `first_notification_id` | `TEXT` | `NULL allowed` | 最近一次初次尝试的消息；未有消息或旧消息删除时可空 |
| `first_queued_at` | `TEXT` | `NOT NULL CHECK(first_queued_at IS NULL OR (length(first_queued_at)=27 AND first_queued_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(first_queued_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(first_queued_at,1,10),'+0 days')=substr(first_queued_at,1,10),0) AND CAST(substr(first_queued_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(first_queued_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(first_queued_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `first_sent_at` | `TEXT` | `CHECK(first_sent_at IS NULL OR (length(first_sent_at)=27 AND first_sent_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(first_sent_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(first_sent_at,1,10),'+0 days')=substr(first_sent_at,1,10),0) AND CAST(substr(first_sent_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(first_sent_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(first_sent_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
PRIMARY KEY(alert_id,user_id)
FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
FOREIGN KEY (first_notification_id) REFERENCES notifications(id) ON DELETE SET NULL
CHECK(first_sent_at IS NULL OR first_sent_at>=first_queued_at)
```

索引：

```sql
CREATE INDEX ix_alert_audience_user ON alert_notification_audience(user_id,alert_id);
CREATE INDEX ix_alert_audience_notification ON alert_notification_audience(first_notification_id) WHERE first_notification_id IS NOT NULL;
```

### 25. `notification_phases`

事件Phase A/B的计时快照及旧计划归档
计时从一个明确phase anchor开始，响应可取消；与消息/队列任务独立。同一事件可有多个历史phase，但最多一个active。old schedules也是计时快照，迁移后停止双写。精确微秒deadline计算由Python datetime执行，不用SQLite毫秒舍入函数代替。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `alert_id` | `TEXT` | `NOT NULL` | 所属事件 |
| `phase_kind` | `TEXT` | `NOT NULL CHECK(phase_kind IN ('awaiting_decision','awaiting_handler'))` | 当前两种计时阶段 |
| `phase_status` | `TEXT` | `NOT NULL CHECK(phase_status IN ('active','cancelled','completed','archived'))` | 前三种为当前流程，archived只用于不再运行的旧计划事实 |
| `started_at` | `TEXT` | `NOT NULL CHECK(started_at IS NULL OR (length(started_at)=27 AND started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(started_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(started_at,1,10),'+0 days')=substr(started_at,1,10),0) AND CAST(substr(started_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(started_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(started_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `first_after_seconds` | `INTEGER` | `NOT NULL CHECK(first_after_seconds >= 15 AND first_after_seconds <= 3600)` | 阶段建立时冻结的首次重复延迟 |
| `all_after_seconds` | `INTEGER` | `NOT NULL CHECK(all_after_seconds >= 30 AND all_after_seconds <= 7200)` | 全体升级延迟必须更晚 |
| `first_due_at` | `TEXT` | `NOT NULL CHECK(first_due_at IS NULL OR (length(first_due_at)=27 AND first_due_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(first_due_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(first_due_at,1,10),'+0 days')=substr(first_due_at,1,10),0) AND CAST(substr(first_due_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(first_due_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(first_due_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `all_due_at` | `TEXT` | `NOT NULL CHECK(all_due_at IS NULL OR (length(all_due_at)=27 AND all_due_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(all_due_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(all_due_at,1,10),'+0 days')=substr(all_due_at,1,10),0) AND CAST(substr(all_due_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(all_due_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(all_due_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `broadcast_role_snapshot_json` | `TEXT` | `NOT NULL DEFAULT '[]' CHECK(broadcast_role_snapshot_json IS NULL OR (CASE WHEN json_valid(broadcast_role_snapshot_json) THEN json_type(broadcast_role_snapshot_json)='array' ELSE 0 END))` | 建立阶段时的历史选择；live关系在notification_phase_roles |
| `initiated_by_user_id` | `TEXT` | `NULL allowed` | Phase B发起人；自动Phase A可空 |
| `source_notification_id` | `TEXT` | `NULL allowed` | 启动计时的消息；即使没有设备也可建立Phase B |
| `cancel_reason` | `TEXT` | `NULL allowed` | 取消/替代原因，可扩展 |
| `completed_at` | `TEXT` | `CHECK(completed_at IS NULL OR (length(completed_at)=27 AND completed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(completed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(completed_at,1,10),'+0 days')=substr(completed_at,1,10),0) AND CAST(substr(completed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(completed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(completed_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `is_legacy` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(is_legacy IN (0,1))` | 允许诚实归档旧v10计划，不伪造人工取消 |

表级PK/FK/CHECK：

```sql
UNIQUE(id,alert_id)
FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
FOREIGN KEY (initiated_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
FOREIGN KEY (source_notification_id) REFERENCES notifications(id) ON DELETE RESTRICT
CHECK(all_after_seconds>first_after_seconds)
CHECK(first_due_at<all_due_at AND first_due_at>started_at)
CHECK(completed_at IS NULL OR completed_at>=started_at)
CHECK(phase_status<>'active' OR completed_at IS NULL)
CHECK(phase_status<>'archived' OR is_legacy=1)
CHECK(COALESCE(CAST(strftime('%s',substr(first_due_at,1,19)||'Z') AS INTEGER)=CAST(strftime('%s',substr(started_at,1,19)||'Z') AS INTEGER)+first_after_seconds,0) AND substr(first_due_at,21,6)=substr(started_at,21,6))
CHECK(COALESCE(CAST(strftime('%s',substr(all_due_at,1,19)||'Z') AS INTEGER)=CAST(strftime('%s',substr(started_at,1,19)||'Z') AS INTEGER)+all_after_seconds,0) AND substr(all_due_at,21,6)=substr(started_at,21,6))
```

索引：

```sql
CREATE UNIQUE INDEX ux_one_active_phase ON notification_phases(alert_id) WHERE phase_status='active';
CREATE INDEX ix_phase_due ON notification_phases(phase_status,first_due_at,all_due_at);
CREATE INDEX ix_phase_initiator ON notification_phases(initiated_by_user_id) WHERE initiated_by_user_id IS NOT NULL;
CREATE INDEX ix_phase_source_notice ON notification_phases(source_notification_id) WHERE source_notification_id IS NOT NULL;
```

### 26. `notification_phase_roles`

Phase B当时选择的live Broadcast Roles
这张新增表有真实职责：把当前用于即时收件资格的JSON角色ID列表改为可执行FK、可索引的M:N关系。选中时最多20角色由事务验证；以后角色删除不抹掉历史快照。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `phase_id` | `TEXT` | `NOT NULL` | 具体Phase B，不能只按alert覆盖所有历史阶段 |
| `role_id` | `TEXT` | `NOT NULL` | 当前存在的角色FK；删除role后live选择消失，历史snapshot仍在 |

表级PK/FK/CHECK：

```sql
PRIMARY KEY(phase_id,role_id)
FOREIGN KEY (phase_id) REFERENCES notification_phases(id) ON DELETE CASCADE
FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
```

索引：

```sql
CREATE INDEX ix_phase_roles_role ON notification_phase_roles(role_id,phase_id);
```

### 27. `notification_phase_dispatches`

一个phase-stage的领取、防重与完成结果
一条stage执行记录可能没有任何消息或设备，所以它不能被并入notifications而造假通知。claim事实是提醒worker的幂等保障；outbox任务处理的是已有消息的发送与重试，两者语义不同。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `phase_id` | `TEXT` | `NOT NULL` | 阶段 |
| `stage` | `INTEGER` | `NOT NULL CHECK(stage >= 1 AND stage <= 2)` | 联合主键成员，阶段1/2 |
| `status` | `TEXT` | `NOT NULL CHECK(status IN ('dispatching','queued','sent','partial','failed','no_recipients','skipped_stopped','skipped_superseded','skipped_responded'))` | 完整当前/旧阶段执行状态 |
| `notification_id` | `TEXT` | `NULL allowed` | 真正产生消息时才引用；claim/skipped/no-recipients允许无消息 |
| `recipient_count_unit` | `TEXT` | `CHECK(recipient_count_unit IN ('user','device'))` | 明确旧代码排队统计用户、完成统计设备的不同单位；历史无法证明时NULL |
| `recipient_count` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(recipient_count >= 0)` | 阶段收件数快照，须结合单位 |
| `sent_count` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(sent_count >= 0)` | 设备成功数快照 |
| `failed_count` | `INTEGER` | `NOT NULL DEFAULT 0 CHECK(failed_count >= 0)` | 设备失败数快照 |
| `error_code` | `TEXT` | `NULL allowed` | 阶段错误与outbox错误各自保存 |
| `claimed_at` | `TEXT` | `NOT NULL CHECK(claimed_at IS NULL OR (length(claimed_at)=27 AND claimed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(claimed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(claimed_at,1,10),'+0 days')=substr(claimed_at,1,10),0) AND CAST(substr(claimed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(claimed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(claimed_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |
| `completed_at` | `TEXT` | `CHECK(completed_at IS NULL OR (length(completed_at)=27 AND completed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(completed_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(completed_at,1,10),'+0 days')=substr(completed_at,1,10),0) AND CAST(substr(completed_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(completed_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(completed_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
PRIMARY KEY(phase_id,stage)
UNIQUE(notification_id)
FOREIGN KEY (phase_id) REFERENCES notification_phases(id) ON DELETE CASCADE
FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE SET NULL
CHECK(completed_at IS NULL OR completed_at>=claimed_at)
```

索引：

```sql
CREATE INDEX ix_phase_dispatch_status ON notification_phase_dispatches(status,claimed_at);
```

### 28. `notification_phase_audience`

每阶段、每用户、每stage的发送防重与结果
late-repeat可在主stage完成后为新增资格账号创建另一条消息；逐用户stage防重仍独立。不是message recipient的重复：这里跨越该phase-stage的多个通知。删除冗余alert_id依赖，查旧API时JOIN phase。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `phase_id` | `TEXT` | `NOT NULL` | phase已决定alert，不重复存alert_id |
| `user_id` | `TEXT` | `NOT NULL` | 用户身份 |
| `stage` | `INTEGER` | `NOT NULL CHECK(stage >= 1 AND stage <= 2)` | 阶段1/2 |
| `status` | `TEXT` | `NOT NULL CHECK(status IN ('queued','sent','failed','skipped'))` | 当前用户阶段状态 |
| `notification_id` | `TEXT` | `NULL allowed` | 主提醒或late-repeat消息；旧outbox唯一映射到消息，保留稳定关联 |
| `updated_at` | `TEXT` | `NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
PRIMARY KEY(phase_id,user_id,stage)
FOREIGN KEY (phase_id) REFERENCES notification_phases(id) ON DELETE CASCADE
FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE SET NULL
```

索引：

```sql
CREATE INDEX ix_phase_audience_user ON notification_phase_audience(user_id,phase_id);
CREATE INDEX ix_phase_audience_notice ON notification_phase_audience(notification_id) WHERE notification_id IS NOT NULL;
```

### 29. `mobile_settings`

全局管理偏好、Borderline并发revision与模拟通话不变量
全局偏好与角色/用户配置的统一revision属于同一单例。合并borderline_settings_revision；旧角色集合转roles.compat_borderline_selected，历史集合留审计。系统运行策略版本仍在config_versions。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(id='global')` | 真实单例设置，不伪装成模型版本资源 |
| `borderline_alert_enabled` | `INTEGER` | `NOT NULL DEFAULT 1 CHECK(borderline_alert_enabled IN (0,1))` | 保留旧Mobile全局设置；与config.vlm_borderline_review_enabled Web master不同 |
| `borderline_revision` | `INTEGER` | `NOT NULL DEFAULT 1 CHECK(borderline_revision >= 1)` | 角色/个人接收设置共用的乐观并发版本 |
| `emergency_call_simulation_only` | `INTEGER` | `NOT NULL DEFAULT 1 CHECK(emergency_call_simulation_only=1)` | 产品当前只模拟通话；不能通过数据库值0启用真实拨号 |
| `updated_by_user_id` | `TEXT` | `NULL allowed` | 后台初始化可空，否则记录修改者 |
| `updated_at` | `TEXT` | `NOT NULL CHECK(updated_at IS NULL OR (length(updated_at)=27 AND updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(updated_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(updated_at,1,10),'+0 days')=substr(updated_at,1,10),0) AND CAST(substr(updated_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(updated_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(updated_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
```

索引：

```sql
-- 只有PK/UNIQUE自动索引，无额外查询索引
```

### 30. `audit_logs`

行政、安全与配置操作的不可变审计事实
审计与事件人工响应、首次已读、schema账表有不同查询/保留职责，保留独立。删除role不能让过去管理员操作消失；entity_id是历史snapshot，不能谎称它受FK保障。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `id` | `TEXT` | `PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0 AND instr(id,char(0))=0)` | 沿用既有公开 ID，避免改动手机深链接、证据 URL 和幂等引用 |
| `user_id` | `TEXT` | `NULL allowed` | 系统操作或已删除actor允许NULL；敏感内容不得写入detail |
| `action` | `TEXT` | `NOT NULL CHECK(length(trim(action)) > 0)` | 行政操作名可扩展，不把未来所有操作封死成少量枚举 |
| `entity_type` | `TEXT` | `NOT NULL CHECK(length(trim(entity_type)) > 0)` | 操作目标的历史类别，非动态授权或可执行外键 |
| `entity_id` | `TEXT` | `NULL allowed` | 历史目标标识，可指已删除角色；故意不是live FK |
| `detail_json` | `TEXT` | `CHECK(detail_json IS NULL OR (CASE WHEN json_valid(detail_json) THEN 1 ELSE 0 END))` | 当时操作及迁移例外的冻结JSON；原始合法JSON类型保留，运行审计由服务生成对象 |
| `created_at` | `TEXT` | `NOT NULL CHECK(created_at IS NULL OR (length(created_at)=27 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(created_at,1,10),'+0 days')=substr(created_at,1,10),0) AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
```

索引：

```sql
CREATE INDEX ix_audit_actor_time ON audit_logs(user_id,created_at DESC);
CREATE INDEX ix_audit_subject_time ON audit_logs(entity_type,entity_id,created_at DESC);
```

### 31. `schema_migrations`

数据库结构升级的持久账表
基础设施元数据有明确长期职责。保留当前migration runner识别的账表，不为了少一张而混入用户活动。schema版本只以本表为真值；DDL样稿不插入假装已执行的升级行。

| Attribute | Type | 列级constraint | 理由与NULL含义 |
|---|---|---|---|
| `version` | `INTEGER` | `NOT NULL CHECK(version >= 1)` | 结构版本主键，与业务配置版本完全不同 |
| `name` | `TEXT` | `NOT NULL CHECK(length(trim(name)) > 0)` | 升级名称，仅version用作身份，不要求名称永远不重复 |
| `applied_at` | `TEXT` | `NOT NULL CHECK(applied_at IS NULL OR (length(applied_at)=27 AND applied_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND CAST(substr(applied_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND COALESCE(date(substr(applied_at,1,10),'+0 days')=substr(applied_at,1,10),0) AND CAST(substr(applied_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(applied_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(applied_at,18,2) AS INTEGER) BETWEEN 0 AND 59))` | 统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序 |

表级PK/FK/CHECK：

```sql
PRIMARY KEY(version)
```

索引：

```sql
-- 只有PK/UNIQUE自动索引，无额外查询索引
```

## 跨表与内容约束

只有跨行/跨表规则使用trigger；普通枚举、范围、NULL及唯一性优先原生约束。权限/状态转换/并发/外部系统约束仍由下面的事务契约负责。

```sql
CREATE TRIGGER config_prompt_tasks_insert BEFORE INSERT ON config_versions
BEGIN
    SELECT CASE WHEN NEW.binary_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.binary_prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'binary_prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.scene_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.scene_prompt_version_id AND r.task='scene_description') THEN RAISE(ABORT,'scene_prompt_version_id: wrong reference task/context') END;
END;
CREATE TRIGGER config_prompt_tasks_update BEFORE UPDATE ON config_versions
BEGIN
    SELECT CASE WHEN NEW.binary_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.binary_prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'binary_prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.scene_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.scene_prompt_version_id AND r.task='scene_description') THEN RAISE(ABORT,'scene_prompt_version_id: wrong reference task/context') END;
END;
CREATE TRIGGER alert_model_prompt_phase_insert BEFORE INSERT ON alerts
BEGIN
    SELECT CASE WHEN NEW.opening_model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.opening_model_version_id AND (r.task=NEW.incident_type OR (NEW.incident_type='physical_contact_bullying' AND r.task='violence'))) THEN RAISE(ABORT,'opening_model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.binary_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.binary_prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'binary_prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.active_phase_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notification_phases r WHERE r.id=NEW.active_phase_id AND r.alert_id=NEW.id AND r.phase_status='active' AND ((r.phase_kind='awaiting_decision' AND NEW.workflow_status='awaiting_confirmation') OR (r.phase_kind='awaiting_handler' AND NEW.workflow_status='confirmed_awaiting_handler'))) THEN RAISE(ABORT,'active_phase_id: wrong reference task/context') END;
END;
CREATE TRIGGER alert_model_prompt_phase_update BEFORE UPDATE ON alerts
BEGIN
    SELECT CASE WHEN NEW.opening_model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.opening_model_version_id AND (r.task=NEW.incident_type OR (NEW.incident_type='physical_contact_bullying' AND r.task='violence'))) THEN RAISE(ABORT,'opening_model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.binary_prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.binary_prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'binary_prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.active_phase_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notification_phases r WHERE r.id=NEW.active_phase_id AND r.alert_id=NEW.id AND r.phase_status='active' AND ((r.phase_kind='awaiting_decision' AND NEW.workflow_status='awaiting_confirmation') OR (r.phase_kind='awaiting_handler' AND NEW.workflow_status='confirmed_awaiting_handler'))) THEN RAISE(ABORT,'active_phase_id: wrong reference task/context') END;
END;
CREATE TRIGGER detection_model_task_insert BEFORE INSERT ON detection_events
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND EXISTS(SELECT 1 FROM alerts a WHERE a.id=NEW.alert_id AND (r.task=a.incident_type OR (a.incident_type='physical_contact_bullying' AND r.task='violence')))) THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
END;
CREATE TRIGGER detection_model_task_update BEFORE UPDATE ON detection_events
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND EXISTS(SELECT 1 FROM alerts a WHERE a.id=NEW.alert_id AND (r.task=a.incident_type OR (a.incident_type='physical_contact_bullying' AND r.task='violence')))) THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
END;
CREATE TRIGGER review_references_insert BEFORE INSERT ON borderline_reviews
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.detection_event_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM detection_events e JOIN alerts a ON a.id=e.alert_id WHERE e.id=NEW.detection_event_id AND e.alert_id=NEW.alert_id AND a.camera_id=NEW.camera_id) THEN RAISE(ABORT,'review event must belong to its alert/camera') END;
END;
CREATE TRIGGER review_references_update BEFORE UPDATE ON borderline_reviews
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.prompt_version_id AND r.task='borderline_binary') THEN RAISE(ABORT,'prompt_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.detection_event_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM detection_events e JOIN alerts a ON a.id=e.alert_id WHERE e.id=NEW.detection_event_id AND e.alert_id=NEW.alert_id AND a.camera_id=NEW.camera_id) THEN RAISE(ABORT,'review event must belong to its alert/camera') END;
END;
CREATE TRIGGER description_version_tasks_insert BEFORE INSERT ON vlm_descriptions
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND r.task='scene_description') THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.prompt_version_id AND r.task='scene_description') THEN RAISE(ABORT,'prompt_version_id: wrong reference task/context') END;
END;
CREATE TRIGGER description_version_tasks_update BEFORE UPDATE ON vlm_descriptions
BEGIN
    SELECT CASE WHEN NEW.model_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_versions r WHERE r.id=NEW.model_version_id AND r.task='scene_description') THEN RAISE(ABORT,'model_version_id: wrong reference task/context') END;
    SELECT CASE WHEN NEW.prompt_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM prompt_versions r WHERE r.id=NEW.prompt_version_id AND r.task='scene_description') THEN RAISE(ABORT,'prompt_version_id: wrong reference task/context') END;
END;
CREATE TRIGGER source_file_kind_insert BEFORE INSERT ON camera_sources
BEGIN
    SELECT CASE WHEN NEW.media_file_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM media_files r WHERE r.id=NEW.media_file_id AND r.media_kind='camera_source') THEN RAISE(ABORT,'media_file_id: wrong reference task/context') END;
END;
CREATE TRIGGER source_file_kind_update BEFORE UPDATE ON camera_sources
BEGIN
    SELECT CASE WHEN NEW.media_file_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM media_files r WHERE r.id=NEW.media_file_id AND r.media_kind='camera_source') THEN RAISE(ABORT,'media_file_id: wrong reference task/context') END;
END;
CREATE TRIGGER evidence_file_kind_insert BEFORE INSERT ON evidence_records
BEGIN
    SELECT CASE WHEN NEW.media_file_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM media_files r WHERE r.id=NEW.media_file_id AND r.media_kind=CASE WHEN NEW.evidence_type='weapon_screenshot' THEN 'evidence_image' ELSE 'evidence_clip' END) THEN RAISE(ABORT,'media_file_id: wrong reference task/context') END;
END;
CREATE TRIGGER evidence_file_kind_update BEFORE UPDATE ON evidence_records
BEGIN
    SELECT CASE WHEN NEW.media_file_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM media_files r WHERE r.id=NEW.media_file_id AND r.media_kind=CASE WHEN NEW.evidence_type='weapon_screenshot' THEN 'evidence_image' ELSE 'evidence_clip' END) THEN RAISE(ABORT,'media_file_id: wrong reference task/context') END;
END;
CREATE TRIGGER phase_source_alert_insert BEFORE INSERT ON notification_phases
BEGIN
    SELECT CASE WHEN NEW.source_notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.source_notification_id AND r.alert_id=NEW.alert_id) THEN RAISE(ABORT,'source_notification_id: wrong reference task/context') END;
END;
CREATE TRIGGER phase_source_alert_update BEFORE UPDATE ON notification_phases
BEGIN
    SELECT CASE WHEN NEW.source_notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.source_notification_id AND r.alert_id=NEW.alert_id) THEN RAISE(ABORT,'source_notification_id: wrong reference task/context') END;
END;
CREATE TRIGGER phase_role_kind_insert BEFORE INSERT ON notification_phase_roles
BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM notification_phases p WHERE p.id=NEW.phase_id AND p.phase_kind='awaiting_handler') THEN RAISE(ABORT,'live broadcast role belongs to Phase B') END;
END;
CREATE TRIGGER phase_role_kind_update BEFORE UPDATE ON notification_phase_roles
BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM notification_phases p WHERE p.id=NEW.phase_id AND p.phase_kind='awaiting_handler') THEN RAISE(ABORT,'live broadcast role belongs to Phase B') END;
END;
CREATE TRIGGER phase_dispatch_notice_insert BEFORE INSERT ON notification_phase_dispatches
BEGIN
    SELECT CASE WHEN NEW.notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.notification_id AND r.message_kind='phase_reminder' AND r.phase_id=NEW.phase_id AND r.reminder_stage=NEW.stage) THEN RAISE(ABORT,'notification_id: wrong reference task/context') END;
END;
CREATE TRIGGER phase_dispatch_notice_update BEFORE UPDATE ON notification_phase_dispatches
BEGIN
    SELECT CASE WHEN NEW.notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.notification_id AND r.message_kind='phase_reminder' AND r.phase_id=NEW.phase_id AND r.reminder_stage=NEW.stage) THEN RAISE(ABORT,'notification_id: wrong reference task/context') END;
END;
CREATE TRIGGER phase_user_notice_insert BEFORE INSERT ON notification_phase_audience
BEGIN
    SELECT CASE WHEN NEW.notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.notification_id AND r.message_kind IN ('phase_reminder','late_repeat') AND r.phase_id=NEW.phase_id AND r.reminder_stage=NEW.stage) THEN RAISE(ABORT,'notification_id: wrong reference task/context') END;
END;
CREATE TRIGGER phase_user_notice_update BEFORE UPDATE ON notification_phase_audience
BEGIN
    SELECT CASE WHEN NEW.notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.notification_id AND r.message_kind IN ('phase_reminder','late_repeat') AND r.phase_id=NEW.phase_id AND r.reminder_stage=NEW.stage) THEN RAISE(ABORT,'notification_id: wrong reference task/context') END;
END;
CREATE TRIGGER alert_audience_notice_insert BEFORE INSERT ON alert_notification_audience
BEGIN
    SELECT CASE WHEN NEW.first_notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.first_notification_id AND r.alert_id=NEW.alert_id) THEN RAISE(ABORT,'first_notification_id: wrong reference task/context') END;
END;
CREATE TRIGGER alert_audience_notice_update BEFORE UPDATE ON alert_notification_audience
BEGIN
    SELECT CASE WHEN NEW.first_notification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notifications r WHERE r.id=NEW.first_notification_id AND r.alert_id=NEW.alert_id) THEN RAISE(ABORT,'first_notification_id: wrong reference task/context') END;
END;
CREATE TRIGGER outbox_has_stable_event_insert BEFORE INSERT ON notification_outbox
BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM notifications n WHERE n.id=NEW.notification_id AND n.event_key IS NOT NULL) THEN RAISE(ABORT,'queued notification requires stable event key') END;
END;
CREATE TRIGGER outbox_has_stable_event_update BEFORE UPDATE ON notification_outbox
BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM notifications n WHERE n.id=NEW.notification_id AND n.event_key IS NOT NULL) THEN RAISE(ABORT,'queued notification requires stable event key') END;
END;
CREATE TRIGGER config_weapon_profiles_insert BEFORE INSERT ON config_versions
BEGIN
    SELECT CASE WHEN NOT json_valid(NEW.weapon_class_thresholds_json) THEN RAISE(ABORT,'malformed threshold JSON') END;
    SELECT CASE WHEN (SELECT count(*) FROM json_each(NEW.weapon_class_thresholds_json))<>3 THEN RAISE(ABORT,'three sensitivity profiles required') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p WHERE p.key NOT IN ('sensitive','moderate','certain') OR p.type<>'object') THEN RAISE(ABORT,'invalid threshold profile') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p WHERE NOT EXISTS(SELECT 1 FROM json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key='knife') OR NOT EXISTS(SELECT 1 FROM json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key IN ('gun','long_gun'))) THEN RAISE(ABORT,'knife and gun/legacy long_gun thresholds required') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p JOIN json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.type NOT IN ('integer','real') OR c.value<0 OR c.value>1) THEN RAISE(ABORT,'threshold values must be probabilities') END;
    SELECT CASE WHEN NEW.validation_profile='runtime' AND EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p JOIN json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key NOT IN ('knife','gun') OR abs(c.value*10000-round(c.value*10000))>0.00000001) THEN RAISE(ABORT,'runtime classes/precision must follow current API') END;
END;
CREATE TRIGGER config_weapon_profiles_update BEFORE UPDATE ON config_versions
BEGIN
    SELECT CASE WHEN NOT json_valid(NEW.weapon_class_thresholds_json) THEN RAISE(ABORT,'malformed threshold JSON') END;
    SELECT CASE WHEN (SELECT count(*) FROM json_each(NEW.weapon_class_thresholds_json))<>3 THEN RAISE(ABORT,'three sensitivity profiles required') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p WHERE p.key NOT IN ('sensitive','moderate','certain') OR p.type<>'object') THEN RAISE(ABORT,'invalid threshold profile') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p WHERE NOT EXISTS(SELECT 1 FROM json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key='knife') OR NOT EXISTS(SELECT 1 FROM json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key IN ('gun','long_gun'))) THEN RAISE(ABORT,'knife and gun/legacy long_gun thresholds required') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p JOIN json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.type NOT IN ('integer','real') OR c.value<0 OR c.value>1) THEN RAISE(ABORT,'threshold values must be probabilities') END;
    SELECT CASE WHEN NEW.validation_profile='runtime' AND EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p JOIN json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key NOT IN ('knife','gun') OR abs(c.value*10000-round(c.value*10000))>0.00000001) THEN RAISE(ABORT,'runtime classes/precision must follow current API') END;
END;
CREATE TRIGGER detection_class_scores_insert BEFORE INSERT ON detection_events
BEGIN
    SELECT CASE WHEN NOT json_valid(NEW.weapon_class_scores_json) THEN RAISE(ABORT,'malformed score JSON') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_scores_json) WHERE type NOT IN ('integer','real') OR value<0 OR value>1) THEN RAISE(ABORT,'scores must be numeric probabilities') END;
END;
CREATE TRIGGER detection_class_scores_update BEFORE UPDATE ON detection_events
BEGIN
    SELECT CASE WHEN NOT json_valid(NEW.weapon_class_scores_json) THEN RAISE(ABORT,'malformed score JSON') END;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_scores_json) WHERE type NOT IN ('integer','real') OR value<0 OR value>1) THEN RAISE(ABORT,'scores must be numeric probabilities') END;
END;
CREATE TRIGGER strings_alerts_insert BEFORE INSERT ON alerts
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;
CREATE TRIGGER strings_alerts_update BEFORE UPDATE ON alerts
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;
CREATE TRIGGER strings_alert_actions_insert BEFORE INSERT ON alert_actions
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;
CREATE TRIGGER strings_alert_actions_update BEFORE UPDATE ON alert_actions
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;
CREATE TRIGGER strings_notification_phases_insert BEFORE INSERT ON notification_phases
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;
CREATE TRIGGER strings_notification_phases_update BEFORE UPDATE ON notification_phases
BEGIN
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND NOT json_valid(NEW.broadcast_role_snapshot_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.broadcast_role_snapshot_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.broadcast_role_snapshot_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;
CREATE TRIGGER strings_alert_notification_audience_insert BEFORE INSERT ON alert_notification_audience
BEGIN
    SELECT CASE WHEN NEW.qualification_reasons_json IS NOT NULL AND NOT json_valid(NEW.qualification_reasons_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.qualification_reasons_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.qualification_reasons_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;
CREATE TRIGGER strings_alert_notification_audience_update BEFORE UPDATE ON alert_notification_audience
BEGIN
    SELECT CASE WHEN NEW.qualification_reasons_json IS NOT NULL AND NOT json_valid(NEW.qualification_reasons_json) THEN RAISE(ABORT,'malformed snapshot array') END;
    SELECT CASE WHEN NEW.qualification_reasons_json IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.qualification_reasons_json) WHERE type<>'text') THEN RAISE(ABORT,'snapshot entries must be strings') END;
END;
CREATE TRIGGER native_confirm_roles BEFORE INSERT ON alert_actions
WHEN NEW.record_origin='workflow' AND NEW.action='confirm_only'
BEGIN SELECT CASE WHEN json_array_length(NEW.broadcast_role_snapshot_json) NOT BETWEEN 1 AND 20
THEN RAISE(ABORT,'confirm-only selects 1..20 roles') END; END;
CREATE TRIGGER review_vlm_snapshot_insert BEFORE INSERT ON borderline_reviews
BEGIN
    SELECT CASE WHEN NEW.vlm_settings_json IS NOT NULL AND NOT json_valid(NEW.vlm_settings_json) THEN RAISE(ABORT,'malformed VLM snapshot') END;
    SELECT CASE WHEN NEW.record_origin='task' AND NOT (COALESCE(json_type(NEW.vlm_settings_json,'$.model_id')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.model_id')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.quantization')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.quantization')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.binary_prompt_version')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.binary_prompt_version')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_prompt_version')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.scene_prompt_version')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.input_fps') IN ('integer','real') AND json_extract(NEW.vlm_settings_json,'$.input_fps') BETWEEN 0.25 AND 12,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.video_token_budget') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.video_token_budget') BETWEEN 256 AND 16384,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.max_output_tokens') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.max_output_tokens') BETWEEN 32 AND 2048,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_description_frames') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.scene_description_frames') BETWEEN 4 AND 128,0) AND COALESCE(json_extract(NEW.vlm_settings_json,'$.scene_description_frames')%2=0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.borderline_review_enabled') IN ('true','false'),0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_description_enabled') IN ('true','false'),0)) THEN RAISE(ABORT,'VLM snapshot field schema failed') END;
END;
CREATE TRIGGER review_vlm_snapshot_update BEFORE UPDATE ON borderline_reviews
BEGIN
    SELECT CASE WHEN NEW.vlm_settings_json IS NOT NULL AND NOT json_valid(NEW.vlm_settings_json) THEN RAISE(ABORT,'malformed VLM snapshot') END;
    SELECT CASE WHEN NEW.record_origin='task' AND NOT (COALESCE(json_type(NEW.vlm_settings_json,'$.model_id')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.model_id')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.quantization')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.quantization')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.binary_prompt_version')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.binary_prompt_version')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_prompt_version')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.scene_prompt_version')))>0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.input_fps') IN ('integer','real') AND json_extract(NEW.vlm_settings_json,'$.input_fps') BETWEEN 0.25 AND 12,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.video_token_budget') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.video_token_budget') BETWEEN 256 AND 16384,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.max_output_tokens') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.max_output_tokens') BETWEEN 32 AND 2048,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_description_frames') IN ('integer') AND json_extract(NEW.vlm_settings_json,'$.scene_description_frames') BETWEEN 4 AND 128,0) AND COALESCE(json_extract(NEW.vlm_settings_json,'$.scene_description_frames')%2=0,0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.borderline_review_enabled') IN ('true','false'),0) AND COALESCE(json_type(NEW.vlm_settings_json,'$.scene_description_enabled') IN ('true','false'),0)) THEN RAISE(ABORT,'VLM snapshot field schema failed') END;
END;
CREATE TRIGGER description_summary_schema_insert BEFORE INSERT ON vlm_descriptions
BEGIN
    SELECT CASE WHEN NEW.structured_json IS NOT NULL AND NOT json_valid(NEW.structured_json) THEN RAISE(ABORT,'malformed scene JSON') END;
    SELECT CASE WHEN NEW.output_format='summary' AND NOT (COALESCE(json_type(NEW.structured_json)='object',0) AND COALESCE(json_type(NEW.structured_json,'$.description')='text' AND length(json_extract(NEW.structured_json,'$.description')) BETWEEN 1 AND 120,0) AND COALESCE(json_type(NEW.structured_json,'$.visible_evidence')='array' AND json_array_length(NEW.structured_json,'$.visible_evidence')<=2,0) AND COALESCE(json_extract(NEW.structured_json,'$.uncertainty') IN ('low','medium','high'),0) AND NOT EXISTS(SELECT 1 FROM json_each(NEW.structured_json,'$.visible_evidence') WHERE type<>'text')) THEN RAISE(ABORT,'SceneSummary schema failed') END;
END;
CREATE TRIGGER description_summary_schema_update BEFORE UPDATE ON vlm_descriptions
BEGIN
    SELECT CASE WHEN NEW.structured_json IS NOT NULL AND NOT json_valid(NEW.structured_json) THEN RAISE(ABORT,'malformed scene JSON') END;
    SELECT CASE WHEN NEW.output_format='summary' AND NOT (COALESCE(json_type(NEW.structured_json)='object',0) AND COALESCE(json_type(NEW.structured_json,'$.description')='text' AND length(json_extract(NEW.structured_json,'$.description')) BETWEEN 1 AND 120,0) AND COALESCE(json_type(NEW.structured_json,'$.visible_evidence')='array' AND json_array_length(NEW.structured_json,'$.visible_evidence')<=2,0) AND COALESCE(json_extract(NEW.structured_json,'$.uncertainty') IN ('low','medium','high'),0) AND NOT EXISTS(SELECT 1 FROM json_each(NEW.structured_json,'$.visible_evidence') WHERE type<>'text')) THEN RAISE(ABORT,'SceneSummary schema failed') END;
END;
CREATE TRIGGER immutable_config_versions BEFORE UPDATE ON config_versions BEGIN SELECT RAISE(ABORT,'config_versions: append-only fact/version'); END;
CREATE TRIGGER immutable_model_versions BEFORE UPDATE ON model_versions BEGIN SELECT RAISE(ABORT,'model_versions: append-only fact/version'); END;
CREATE TRIGGER immutable_prompt_versions BEFORE UPDATE ON prompt_versions BEGIN SELECT RAISE(ABORT,'prompt_versions: append-only fact/version'); END;
CREATE TRIGGER immutable_detection_events BEFORE UPDATE ON detection_events BEGIN SELECT RAISE(ABORT,'detection_events: append-only fact/version'); END;
CREATE TRIGGER immutable_alert_actions BEFORE UPDATE ON alert_actions BEGIN SELECT RAISE(ABORT,'alert_actions: append-only fact/version'); END;
CREATE TRIGGER immutable_schema_migrations BEFORE UPDATE ON schema_migrations BEGIN SELECT RAISE(ABORT,'schema_migrations: append-only fact/version'); END;
CREATE TRIGGER retain_config_versions BEFORE DELETE ON config_versions BEGIN SELECT RAISE(ABORT,'config_versions: retain version/history'); END;
CREATE TRIGGER retain_model_versions BEFORE DELETE ON model_versions BEGIN SELECT RAISE(ABORT,'model_versions: retain version/history'); END;
CREATE TRIGGER retain_prompt_versions BEFORE DELETE ON prompt_versions BEGIN SELECT RAISE(ABORT,'prompt_versions: retain version/history'); END;
CREATE TRIGGER retain_alert_actions BEFORE DELETE ON alert_actions BEGIN SELECT RAISE(ABORT,'alert_actions: retain version/history'); END;
CREATE TRIGGER retain_schema_migrations BEFORE DELETE ON schema_migrations BEGIN SELECT RAISE(ABORT,'schema_migrations: retain version/history'); END;
CREATE TRIGGER immutable_audit BEFORE UPDATE ON audit_logs WHEN NEW.id IS NOT OLD.id OR NEW.action IS NOT OLD.action OR NEW.entity_type IS NOT OLD.entity_type OR NEW.entity_id IS NOT OLD.entity_id OR NEW.detail_json IS NOT OLD.detail_json OR NEW.created_at IS NOT OLD.created_at OR (NEW.user_id IS NOT OLD.user_id AND NEW.user_id IS NOT NULL) BEGIN SELECT RAISE(ABORT,'audit is append-only except actor FK deletion cleanup'); END;
CREATE TRIGGER retain_audit BEFORE DELETE ON audit_logs BEGIN SELECT RAISE(ABORT,'retain administrative audit'); END;
CREATE TRIGGER immutable_users_id BEFORE UPDATE OF id ON users WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_roles_id BEFORE UPDATE OF id ON roles WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_devices_id BEFORE UPDATE OF id ON devices WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_locations_id BEFORE UPDATE OF id ON locations WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_cameras_id BEFORE UPDATE OF id ON cameras WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_media_files_id BEFORE UPDATE OF id ON media_files WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_camera_sources_id BEFORE UPDATE OF id ON camera_sources WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_alerts_id BEFORE UPDATE OF id ON alerts WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_borderline_reviews_id BEFORE UPDATE OF id ON borderline_reviews WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_evidence_records_id BEFORE UPDATE OF id ON evidence_records WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_vlm_descriptions_id BEFORE UPDATE OF id ON vlm_descriptions WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_notifications_id BEFORE UPDATE OF id ON notifications WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_notification_outbox_id BEFORE UPDATE OF id ON notification_outbox WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_notification_recipients_id BEFORE UPDATE OF id ON notification_recipients WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_notification_deliveries_id BEFORE UPDATE OF id ON notification_deliveries WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_notification_phases_id BEFORE UPDATE OF id ON notification_phases WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER immutable_mobile_settings_id BEFORE UPDATE OF id ON mobile_settings WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;
CREATE TRIGGER clear_stopped_phase AFTER UPDATE OF phase_status ON notification_phases
WHEN NEW.phase_status<>'active'
BEGIN UPDATE alerts SET active_phase_id=NULL WHERE active_phase_id=NEW.id; END;
CREATE TRIGGER keep_notification_identity BEFORE UPDATE ON notifications
WHEN NEW.alert_id IS NOT OLD.alert_id OR NEW.event_key IS NOT OLD.event_key OR NEW.phase_id IS NOT OLD.phase_id OR NEW.reminder_stage IS NOT OLD.reminder_stage OR NEW.message_kind IS NOT OLD.message_kind
BEGIN SELECT RAISE(ABORT,'notification identity/context is immutable'); END;
CREATE TRIGGER freeze_dispatched_content BEFORE UPDATE OF title,body,data_payload_json ON notifications
WHEN (NEW.title IS NOT OLD.title OR NEW.body IS NOT OLD.body OR NEW.data_payload_json IS NOT OLD.data_payload_json)
AND (EXISTS(SELECT 1 FROM notification_outbox o WHERE o.notification_id=OLD.id AND o.attempt_count>0)
     OR EXISTS(SELECT 1 FROM notification_deliveries d WHERE d.notification_id=OLD.id AND d.attempt_count>0))
BEGIN SELECT RAISE(ABORT,'content is frozen after dispatch begins'); END;
CREATE TRIGGER keep_notification_outbox_context BEFORE UPDATE ON notification_outbox WHEN NEW.notification_id IS NOT OLD.notification_id OR NEW.created_at IS NOT OLD.created_at BEGIN SELECT RAISE(ABORT,'context snapshot is immutable'); END;
CREATE TRIGGER keep_notification_deliveries_context BEFORE UPDATE ON notification_deliveries WHEN NEW.notification_id IS NOT OLD.notification_id OR NEW.device_id IS NOT OLD.device_id OR NEW.recipient_user_id IS NOT OLD.recipient_user_id BEGIN SELECT RAISE(ABORT,'context snapshot is immutable'); END;
CREATE TRIGGER keep_borderline_reviews_context BEFORE UPDATE ON borderline_reviews WHEN NEW.record_origin IS NOT OLD.record_origin OR NEW.camera_id IS NOT OLD.camera_id OR NEW.alert_id IS NOT OLD.alert_id OR NEW.detection_event_id IS NOT OLD.detection_event_id OR NEW.model_version_id IS NOT OLD.model_version_id OR NEW.prompt_version_id IS NOT OLD.prompt_version_id OR NEW.config_version_id IS NOT OLD.config_version_id OR NEW.detector_confidence IS NOT OLD.detector_confidence OR NEW.source_start_seconds IS NOT OLD.source_start_seconds OR NEW.source_end_seconds IS NOT OLD.source_end_seconds OR NEW.context_path IS NOT OLD.context_path OR NEW.vlm_settings_json IS NOT OLD.vlm_settings_json BEGIN SELECT RAISE(ABORT,'context snapshot is immutable'); END;
CREATE TRIGGER keep_notification_phases_context BEFORE UPDATE ON notification_phases WHEN NEW.alert_id IS NOT OLD.alert_id OR NEW.phase_kind IS NOT OLD.phase_kind OR NEW.started_at IS NOT OLD.started_at OR NEW.first_after_seconds IS NOT OLD.first_after_seconds OR NEW.all_after_seconds IS NOT OLD.all_after_seconds OR NEW.first_due_at IS NOT OLD.first_due_at OR NEW.all_due_at IS NOT OLD.all_due_at BEGIN SELECT RAISE(ABORT,'context snapshot is immutable'); END;
```

## 44张旧表逐一去向

| 原表 | 目标 |
|---|---|
| `alert_actions` | alert_actions [legacy] |
| `alert_notification_audience` | alert_notification_audience |
| `alert_notification_escalation_configs` | config_versions两阶段延迟 |
| `alert_notification_escalation_dispatches` | notification_phase_dispatches的历史对应phase-stage |
| `alert_notification_escalation_schedules` | notification_phases的legacy归档或可证明相同phase |
| `alert_reads` | alert_reads |
| `alerts` | alerts；status由workflow_status固定映射派生 |
| `audit_logs` | audit_logs |
| `borderline_review_tasks` | borderline_reviews [task] |
| `borderline_reviews` | borderline_reviews [audit_only]；能证明相同的镜像合并并保留legacy_review_id |
| `borderline_role_settings` | roles.borderline_* |
| `borderline_settings_revision` | mobile_settings.borderline_revision |
| `borderline_user_settings` | users.borderline_* |
| `camera_sources` | camera_sources |
| `cameras` | cameras |
| `config_versions` | config_versions |
| `detection_events` | detection_events |
| `devices` | devices |
| `evidence_records` | evidence_records |
| `locations` | locations |
| `media_files` | media_files |
| `mobile_alert_reminder_audience` | notification_phase_audience；alert_id通过phase推导，outbox_id唯一映射notification_id |
| `mobile_alert_reminder_dispatches` | notification_phase_dispatches |
| `mobile_alert_reminder_phases` | notification_phases；live JSON角色选择展开到notification_phase_roles |
| `mobile_alert_workflow_events` | alert_actions [workflow]；审计事实留audit_logs |
| `mobile_alert_workflows` | alerts的当前流程/处理人/phase/revision字段 |
| `mobile_notification_outbox` | notification_outbox；event_key移notifications，payload统一读取notifications |
| `mobile_notification_outbox_deliveries` | notification_deliveries；以(notification_id,device_id)单一事实，不加总次数 |
| `mobile_notification_outbox_recipients` | notification_recipients.queued_target |
| `mobile_settings` | mobile_settings；角色集合改为roles.compat_borderline_selected |
| `model_versions` | model_versions |
| `notification_deliveries` | notification_deliveries |
| `notification_reads` | notification_reads |
| `notification_recipients` | notification_recipients.feed_visible |
| `notifications` | notifications |
| `prompt_versions` | prompt_versions |
| `roles` | roles |
| `schema_migrations` | schema_migrations |
| `user_roles` | user_roles |
| `users` | users |
| `violence_configs` | config_versions.violence_* |
| `vlm_configs` | config_versions.vlm_*及prompt FK |
| `vlm_descriptions` | vlm_descriptions |
| `weapon_configs` | config_versions.weapon_* |

## 功能覆盖与行为保持

| 当前功能 | 新实体/服务 | 不能改变的行为 |
|---|---|---|
| Firebase登录、session refresh、reset/setup邮件、管理员创建或关联用户 | users/roles/user_roles + 当前Firebase服务 | 密码/ID token/refresh token不入库，身份只信服务验证 |
| 角色CRUD、权限、用户状态、资料编辑、角色分配 | users/roles/user_roles/audit_logs | 保护最后管理员与系统角色；不能授予超过自身的权限；UI仍按当前primary role |
| 角色/个人Borderline、新成员默认、角色整组覆盖、master switch | roles/users/mobile_settings/config_versions | 个人覆盖与后续整组再次覆盖语义保留；Web开关与账号接收开关分开 |
| 摄像头CRUD、上传替换、media/frame预览、RTSP引用 | locations/cameras/camera_sources/media_files | 每camera最多一个active source；原文件/来源清理顺序及访问检查继续 |
| 配置保存/历史回溯 | config_versions/model_versions/prompt_versions | 保存新增version，不覆盖历史；每次检测用自身版本 |
| 暴力/身体霸凌/武器检测与事件合并 | alerts/detection_events + 当前policy/orchestrator | source-time合并间隔、阈值、N-of-M保留；不重新用cooldown丢弃新警报/新账号 |
| Borderline排队、重启恢复、正/负结果、最多三次尝试 | borderline_reviews | task窗唯一；negative不删除警报或改变人工状态；历史positive不重放 |
| 场景描述/重试/旧输出读取 | vlm_descriptions | 最新ready，summary/raw_json/text回退都保留 |
| 证据准备、失败、可用、截图/片段、删除到期媒体 | evidence_records/media_files/alerts | 数据与filesystem协调；全Active有角色用户可浏览全部证据 |
| 警报与通知已读、mark-all-read | alert_reads/notification_reads | 两种状态独立；read不停止提醒 |
| 确认误报、仅确认并广播、确认并处理、接手、解决、旧操作、模拟通话 | alerts/alert_actions + 当前状态机 | optimistic revision409、请求幂等、仅handler解决；通话仍只模拟 |
| Phase A/B、60/120默认、可配置阶段延迟、全体升级 | notification_phases/roles/dispatches | Phase B新anchor；过期stage2替代未发stage1；没有设备也能Confirm-only并建phase |
| 初次通知、新加入资格用户、late-repeat、防重复阶段 | alert_notification_audience/notification_phase_audience | 每alert-user初次状态、每phase-user-stage防重；late-repeat不被主stage唯一关系挡住 |
| 多设备FCM、失效token、队列恢复、逐设备重试 | outbox/deliveries/recipients/devices | 已sent设备不重发；关闭账号/role及Borderline后发送前重验；beep channel IDs不改变 |
| 实际受众与浏览授权 | 当前API授权 + audiences | 警报浏览不要求收到通知或拥有device；通知列表仍按其可见名单 |
| Runtime health、schema版本、模型readiness | schema_migrations + 当前runtime服务 | 元数据版本查询适配而非造假PRAGMA；不把模型状态伪装SQL静态记录 |
| simulation plan、analysis进度/ETA/取消/debug最近64条 | 保留当前orchestrator内存服务 | 它们目前不在SQLite持久化，方案不新增无必要job/debug entity，也不删除功能 |
| Flutter demo/offline demo | 当前DemoGuardAidRepository | 没有新增手机独立SQLite数据库，真实API DTO与demo流程继续 |

### 派生字段的明确契约

旧alerts.status不再独立保存，映射：awaiting_confirmation→open；confirmed_awaiting_handler/in_progress/legacy_in_review→in_review；resolved→resolved；dismissed→dismissed；legacy_escalated→escalated。当前只读核对无矛盾、无缺workflow。迁移时再次核对；有矛盾先报告，不自选一方覆盖。

cameraName/location、MIN/MAX detected time、max confidence、detection count、最新ready描述、phase所属alert、旧phase_audience.outboxId通过明确JOIN/聚合/唯一关系计算。不存在“为了DTO每个字段建一表”或同一真值重复存储。

### 两项明确的约束强化

- 旧notification_recipients无PK，且UNIQUE含NULL无法可靠防重复。新INTEGER PK和两个partial UNIQUE修复关系集合约束；重复旧行做集合去重并记录原重复数量，不造成重复feed项。
- 通知可见范围显式记录。旧本来无recipient的历史消息迁移为broadcast；restricted消息在角色删除/名单为空后仍restricted，避免把“空名单”误当全体授权。这是明确的授权边界修正，需要在role删除契约验收中确认；没有取消任何正常角色选择或消息浏览功能。

另有必要输入完善：必需VLM模型标识、量化标签和task输入context不能是空字符串。现配置DTO对某些str只做类型检查，实施时同步加入非空验证并返回清晰错误；不通过默认值替换为空的历史记录。当前快照均符合，正常选择模型/参数和复核功能保留。


## 数据库与应用层的责任边界

| 规则 | DB负责 | 服务/事务负责 |
|---|---|---|
| 类型/空值/值域/唯一/外键/同alert-phase | STRICT/NOT NULL/CHECK/UNIQUE/FK/有限trigger | 入参规范化及友好错误 |
| 权限/最后管理员/角色授予上限 | 保存关联与权限值 | 验证Firebase、当前账号/role，BEGIN IMMEDIATE内验证写入，避免并发绕过 |
| workflow并发 | revision非负、动作key唯一 | 先查idempotency，再UPDATE ... WHERE revision=expected；成功revision+1；失败409 |
| 计时期限 | 日期格式、顺序、精确秒差及相同微秒、一个active phase | Python UTC datetime冻结期限；先取消phase，再更新不再等待的workflow；同事务通知/动作/role关系 |
| queue重试 | 状态/索引/消息唯一任务/设备唯一结果 | 条件claim/lease恢复、正常失败结果的重试阈值3、当前退避；crash恢复领取次数与真正设备发送次数不同，不能用SQL硬限领取3来阻止恢复；外部FCM与DB不是同一原子事务，不能承诺网络绝对exactly-once |
| 当前可发送对象 | 真实名单FK、逐设备记录 | 每次发送重验active/role/device/threshold/Borderline/workflow及60秒初发有效窗，保留current channels |
| user/devices历史owner | 普通FK，不用会随token归属变化失效的复合FK | 新发送前记录真实owner；旧未知留NULL；token换用户不重写旧结果 |
| SHA与路径/MIME/RTSP密码 | 摘要hex形状、相对路径形态、文件kind一致性 | 真正计算SHA、最终路径containment、MIME/codec、secret引用、上传/请求/FCM总字节预算 |
| config版本与全局revision | 两个独立版本域；配置append-only | 读取MAX并分配版本、设置CAS更新、偏好批量覆盖及审计同事务 |
| JSON内容 | 数值概率、profile、VLM snapshot、SceneSummary、字符串数组 | versioned DTO，真实模型/提示词内容，native/historical标记不可由客户端设置 |

当前动作状态机继续使用原代码的transition定义：read/模拟call不停止phase；dismiss/处理/接手/解决停止phase；confirm-only→in_review兼容状态并启动Phase B。不得把触发器误当作全部业务状态机。provider错误码扩展不必增加一个临时entity。

历史snapshot不能作为live授权。phase选中角色存真实关联，快照用于展示被删角色的当时选择；notification payload保留实际内容，FK列提供授权/查询路径。数据库没有SQLite用户角色权限隔离，所有写入仍经受控repository；离线导入标记不开放给HTTP客户端。


## 分阶段重构实施规划

本文件不包含运行迁移指令。实际改写必须依次完成下面步骤，不能直接把样稿SQL运行在当前DB。

### 阶段0：固定基线与契约

以sqlite consistent backup和一个只读快照采集44表结构、FK、业务统计、模型/prompt/config版本、已读集合、全部消息/queue/device/stage状态；文件清单与hash分开核验。WAL运行中不能只复制主.db。列出main.py全部Web/Mobile端点及Dart repository方法的输入/输出、错误码与副作用。基线含当前legacy读取，不先删表。

### 阶段1：新空库与约束验证

用schema_proposal.sql创建独立目标库。保留ID与FK定义；seed配置完整写入一行，历史prompt内容未知仍metadata_only。DDL测试仅证明schema和局部规则，不能代替API/worker回归。timezone写入边界统一Z六位小数，不经过毫秒函数丢精度。

### 阶段2：用户/角色设置及配置迁移

将一对一设置按原ID/值/来源/更新者/时间并入主表；NULL偏好与显式false不同。全局revision放mobile_settings并保留旧兼容开关，角色选择展开标记。五个配置行合为一份版本：保留每个值，不把旧阈值四舍五入，不把missing字段替换默认值。旧prompt label映射真实prompt资源；必要的metadata-only登记要有原标签，不伪造内容。这里只合并存储，不改变角色继承/个人覆盖/整组再覆盖逻辑。

### 阶段3：事件、review与动作

把workflow状态并入alert；先核验status映射，冲突报告阻断。request input snapshot、started/completed与旧reviewed_at分别保存。只有有唯一业务key且所有相关事实一致才合并review镜像，否则保留audit_only行；这些旧记录不会进入worker队列。旧positive补充标记维持terminal，不变eligible。旧/新动作都映射alert_actions并保留origin，不能用相近时间证明同一动作。模拟call确有双写时保留原ID/差异note的审计，查询保持现有历史去重展示；新写入结束双写。

### 阶段4：通知及阶段

消息/outbox保留各自ID；event_key移动到消息。只有两份payload逐字段等价才采用一份canonical（当前快照全部一致）；若有差异先保留完整原值审计并阻断自动切换，不能猜哪份实际发送。feed/queued名单分别用两个标记，不能用并集扩大推送范围。逐设备镜像相同导入一次，attempt_count不相加；不同则报告与归档双方事实，不能无根据选sent。旧缺失recipient_user不推测。

v10 plan/dispatch映射对应phase；无法证明与v11同一事实的旧计划另存archived/is_legacy，不伪造人工cancel；当前活跃阶段仍保持active且唯一。stage执行即使没有notification也保存原NULL，不造一条隐藏通知。旧统计单位不能从status='queued'简单猜：重试回调也会queued但统计设备数；能证明单位才填，否则NULL并保留原值。role选择展开phase_roles，历史删role仍只在snapshot中保留。phase_audience的alert由phase确定，outbox由消息唯一关系回查；归档原映射ID到migration审计。

### 阶段5：改写服务与影子对比

改写SQLiteRepository的配置、查询、动作、review与通知SQL，返回原DTO；改写orchestrator复核完成后单写review，不再mirror旧表。worker改为通过message读取canonical payload，收件人按queued_target查询；通知feed按feed_visible/visibility_policy。stage worker仍单独claim phase_stage，late-repeat另有消息。metadata runner/health继续schema_migrations。只读影子查询比较结果，不建立永久双写第二真值。

### 阶段6：验收、切换与回退

先在副本完成下面验收。切换窗口停止相关writer/worker，获取最终一致备份/补迁移，不在发送途中切换一半。原running review/dispatch lease按现有恢复语义处理；已sent设备/已完成stage保持防重。只有通过完整数据核对和契约回归才启用新库；schema_migrations在成功事务记录真正新版本。旧库只读保留至回退窗口结束。回退不是把新写入丢弃：必须有cutover前备份、停写、回迁新事实/媒体和记录ledger的方案。

### 必须执行的验收矩阵

| 类别 | 至少需要的场景 |
|---|---|
| 数据无损 | 每旧attribute有去向；PK/FK完整；配置值及精度、SHA/路径、MIN/MAX/confidence/count、两个read集合、audience、所有queue/stage状态一致 |
| 权限 | Active有角色全警报浏览；pending/disabled/无role拒绝；通知role删除不扩大feed；权限授予及最后管理员保护 |
| 动作 | 全部旧/新action；无设备Confirm-only；两client同时操作409；同key重试只执行一次；handler限制 |
| 复核/描述 | pending/running重启恢复、尝试上限、正/负/错误、不重播历史positive、关闭master抑制补充；summary和raw/text兼容 |
| 通知 | 多设备一成功一失败、重试不重发sent设备、失效token、账号/role变化、token换用户、60秒初发有效窗、保持各beep channel |
| 提醒 | Phase A/B新anchor、stage1/2、过期stage2 supersede、late user补发、每用户阶段唯一、read/comment/call不停止计时 |
| 媒体 | 上传替换/清理、帧/流预览、证据准备/失败/可用、codec修复、到期删除及失败补偿、路径访问权限 |
| API/UI | 对照main.py全部端点与Flutter repository方法、原public IDs/deep links、错误码、排序/分页与demo分支 |
| 并发/可靠性 | 多worker claim、crash后lease恢复、配置version并发、Borderline revision CAS、切换与回退演练 |

这些是实施阶段必须完成的检查，并非本次已执行结果；本次验证报告明确范围。不得以“DDL执行成功”声明全系统兼容。


## 选择与未做的优化

- 不合并model/prompt/policy/admin为一个资源池，不合并detector/review/description为任务大表；实体目的更直接，正常FK和状态约束能表达多数规则。
- config变宽但所有字段属于同一版本，不出现与该行不适用的一堆子类型NULL；这是明确的一对一水平合并。如果以后组件独立版本保存，重新拆分需要新业务依据。
- notification_recipients仅用户/角色同一名单关系，两个标记区分feed/queue用途；audience、阶段用户状态、设备结果仍独立，不用六scope表。新phase_role虽然增加一表，但换得真实FK与独立关系语义。
- alert workflow当前状态合并、兼容status派生；动作仍历史表，避免把历史改成当前状态字段。跨系统时间/错误码没有凭印象固定长度。没有新建分析job/debug实体，因为现功能不要求DB持久化。
- 索引与执行效率需要真实数据、EXPLAIN QUERY PLAN及负载测量；31表不是性能证明。新方案相对原44更少重复，相对15少多态约束复杂度，但也没有数学证明全局最优。
- 未建立新的notification attempts表：目前系统只保存每设备最新结果与计数，不拥有逐次尝试历史功能。若未来确需完整attempt audit，再有理由新增该实体。不会预先为了“专业看起来齐全”新增临时用途表。
- 无固定工期/性能数字猜测。迁移策略与约束样稿可以检查；代码改写、端到端验收和上线授权另属实施工作。


## 依据与附件

本地事实依据：guardaid_monitor/migrations/001_initial.sql..014_borderline_notification_eligibility.sql；DATABASE_DESIGN.md；app/domain.py、sqlite_repository.py、main.py、inference/orchestrator.py、notification_escalation.py、notification_outbox.py；mobileapp/lib/src/repository.dart与models.dart。基线数据库只读核对，未把邮箱/token写入文档。

原生SQLite规则见[类型](https://www.sqlite.org/datatype3.html)、[STRICT](https://www.sqlite.org/stricttables.html)、[CREATE TABLE](https://www.sqlite.org/lang_createtable.html)、[外键](https://www.sqlite.org/foreignkeys.html)、[局部索引](https://www.sqlite.org/partialindex.html)、[JSON](https://www.sqlite.org/json1.html)、[日期函数](https://www.sqlite.org/lang_datefunc.html)。Firebase UID范围见[Firebase Manage Users](https://firebase.google.com/docs/auth/admin/manage-users)。外部来源用于确认平台规则，实体职责以当前项目证据为主。

OVERVIEW.md为阅读入口；KEYS.md为31表PK/FK速查；ATTRIBUTE_MAPPING.md列原44表全部339属性去向；API_COVERAGE.md静态列44个HTTP路由及30项Mobile契约；schema_proposal.sql为新空库样稿；erd_full.mmd是完整FK图，erd_identity/monitoring/response.mmd是模块图；VALIDATION.md记录本次真正执行的验证。设计是规划，不能把附件当成运行库已改造。

