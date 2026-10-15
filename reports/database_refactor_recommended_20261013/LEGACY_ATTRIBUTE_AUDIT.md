# 旧版兼容属性与迁移标记审查

日期：2026-10-15。对象：现有31表设计样稿。此文为审查结论，未修改设计DDL、运行数据库、Web/Mobile源码。31表/305属性仍是修改前的样稿计数。

## 结论与判断标准

原方案优先保证现有历史、API契约和演示行为可继续读取，因此包含旧字段，也新增了一些描述迁移事实的标记。并非每个兼容字段都必须长期存在；也不能把保存历史业务事实的字段当成废弃功能删除。

本次建议：移除没有当前业务真值的is_provisional；capture_state可改为基于真实内容完整性的派生值；legacy_review_id可移到持久迁移映射审计。其他项目按下面条件决定，不能整批删除。

“可删除”指可以在新设计中调整，仍需同步改写SQL、约束、DTO及调用端。“移到迁移审计”要求完整持久保存原表/原ID/新ID和差异，不能仅留临时日志或默默丢弃。若原API或页面需要某个历史事实，仍须有可读回的等价实现。NULL及历史原值不得伪造。

## 一、旧版本字段或旧语义

| 新设计中的字段 | 为什么仍存在 | 是否可移除及具体条件 |
|---|---|---|
| alerts.is_provisional | 旧版提前建立暂定警报的标记。新暴力/武器流程都写false。 | **建议移除数据库列。** 本次只读快照127条全部为0；API可继续派生false，Flutter内存Demo可独立保留旧示例。不能用review queued/running等价推导它。 |
| config_versions.violence_cooldown_seconds | 原暴力通知冷却参数。当前SQLite outbox主路径不以该值挡住新警报，但旧直发回退路径仍检查cooldown。 | **有条件可删。** 先明确取消/适配直发回退路径及配置读写契约，并保留旧配置值的历史去向；不能仅因新路径绕过就认定代码完全没用。 |
| config_versions.weapon_cooldown_seconds | 原武器通知冷却参数，用途与上项相同。 | **有条件可删。** 与暴力cooldown一起处理；不改变当前提醒间隔或事件合并间隔，后两者是不同参数。 |
| config_versions.weapon_confidence_threshold | 原武器总体分数阈值；新主流程按类别和敏感度阈值判断，兼容单hit调用仍使用旧值。 | **有条件可删。** 统一调用为类别分数流程，改写或退役旧单hit分支；旧配置值归档，原配置接口的读回语义明确处理。不能删掉weapon_class_thresholds_json。 |
| mobile_settings.borderline_alert_enabled | 原Mobile全局enabled值；当前GET接口仍返回它，旧批量设置入口、角色删除等仍会维护它。实际接收另有Web master与用户/角色偏好。 | **完整保留当前契约时暂留。** 若正式收敛到新角色/个人设置契约，可删除该独立存储并设计兼容输出。它不能不经验证就由“任意角色开启”计算，因为新角色设置不会同步旧集合的全部状态。 |
| alerts.sensitivity_mode | 原警报整体敏感度标签；当前新检测资格按每次检测及角色设置判断，但旧标签仍经DTO输出。 | **不建议直接删。** 本快照三个值均有数据；只有明确结束该标签的展示/契约，或证明有稳定等价推导并保留历史后才删。它与roles.sensitivity_mode职责不同。 |
| notification_recipients.role_id | 旧通知按角色动态确定可见对象的关系。当前仍有角色收件行，通知列表按当前成员关系判断。 | **不建议直接删。** 将当时角色成员展开成固定用户快照会改变以后加入/离开角色的可见性。要先决定改为固定名单的产品语义，或保留等价动态关系。 |
| vlm_descriptions.display_text | 旧描述可能只有文字；新结果也保留界面可读文本。 | **建议保留。** 不是单纯废弃标记。可以设计统一输出字段替代，但必须完整保留纯文本结果和现有读取语义，不能只删文字列。 |

## 二、提案新增、用来表达旧数据的字段

这些字段中有的并非原数据库已有，而是31表提案为迁移增加。

| 字段 | 表达什么 | 是否可移除及具体条件 |
|---|---|---|
| roles.compat_borderline_selected | 原mobile_settings.borderline_recipient_roles_json中的独立选择集合，改成逐角色布尔值。 | **有条件可删。** 与旧enabled/recipientRoleIds契约一并收敛。不能假设它始终等于roles.borderline_default_enabled：新角色/个人设置和旧集合可以分别变化。原集合需要持久映射审计。 |
| prompt_versions.capture_state | 区分只知道版本、与保存了完整内容及摘要的提示词。 | **可以改为派生，建议考虑移除存储列。** 当前用途可以通过content及content_sha256的完整性表达；版本不可变约束和完整内容检查仍保留，历史内容缺失继续NULL，不补造。若未来另定义独立“发布/审批”状态，则需独立业务依据。 |
| config_versions.validation_profile | 对新配置执行当前类别/精度规则，对历史配置保留原类别和精度。 | **现方案建议保留。** 可由正式的配置格式版本/明确迁移边界替代，但不能删标记后把所有历史强改为现行knife/gun和四位精度。也不能悄悄取消对新输入的验证。 |
| borderline_reviews.legacy_review_id | task与旧结果可以证明同一事实并合并时，保留旧结果ID别名。 | **可以移出主表。** 将旧ID→新ID完整保存到持久迁移审计；没有合并的audit_only行继续使用原ID。本快照13条旧结果、0个task，不能据此声称将来切换时永远不会有镜像。 |
| borderline_reviews.record_origin | 区分完整可执行task与只有结果的audit_only历史记录。 | **建议保留。** 统一两类记录后的类型标记；不只用于显示旧版本。技术上可重写为根据输入字段成组完整性判别，但必须保留互斥约束及worker排除规则，不能单凭某个偶然NULL猜测任务类型。 |
| borderline_reviews.reviewed_at | 保存旧结果的原登记时间；task有独立created/started/completed。 | **不建议直接删。** 可统一重命名为明确的“结果登记时间”，但不能无依据视作task完成时间。合并镜像时两个原时间可能不同，完整保留需另有去向。 |
| alert_actions.record_origin | 区分旧操作与workflow操作，用于isLegacy展示及各自幂等/备注规则。 | **现方案建议保留。** 若另建立严格、可证明的等价规则，可派生isLegacy并改写CHECK。不能靠操作名称、时间或没有写入约束保障的key是否为空来猜来源。 |
| notification_phases.is_legacy | 区分来自旧v10计划的记录，允许其archived状态。 | **有条件可删。** 若迁移规则固定为“未合并旧计划一律archived，当前phase不打旧标记”，可由该状态表达归档，并将来源映射记入审计。若旧来源可为active/cancelled/completed，或archived也用于新phase，就仍是独立信息。现DDL不保证两者等价，不能只删列。 |

## 三、旧的是枚举值或JSON内容，不是整个字段

| 字段/位置 | 旧内容 | 可否移除 |
|---|---|---|
| alerts.workflow_status | legacy_in_review、legacy_escalated | 当前有2条legacy_escalated。不能随意改成等待确认/已解决等不同事实。可保留状态，或定义完整归档/兼容映射后收敛枚举；字段本身必须保留。 |
| alerts.borderline_review_status | legacy_unknown | 只在原复核信息确实未知时使用。当前样本无此值并不证明所有导入都能确定。可在迁移证明不需要后移除此允许值，不删除整个复核状态。 |
| alerts.evidence_status | legacy_unknown | 同上；证据当前是否可用需要真实核验，不能把未知填成available。 |
| alert_actions.action | acknowledge、review、dismiss、resolve、escalate等旧操作编码 | 本快照旧动作含escalate、resolve及模拟call。可结束新写入旧操作，历史原义保留；不能为了只留新枚举而伪造新动作。 |
| notifications.message_kind | legacy | 无法可靠分类的旧直接通知可用此值。当前80条通知没有outbox，不代表80条都无法分类。先按确凿关联分类，剩余未知才用legacy；可证明全分类后才移除该枚举值。 |
| vlm_descriptions.output_format | raw_json、text等兼容输出形式 | 合法旧输出仍需可读；可进行无损格式转换后收敛格式，不能删除有效文字内容。标准摘要必须有真实字段，不能为历史文字补造visible_evidence。 |
| config_versions.weapon_class_thresholds_json | long_gun、历史五类别与更高精度值 | 本快照22个版本含旧类别，8个为knife/gun。可禁止新配置写旧类别，但历史值要保留或完整归档；不能把long_gun当作gun无损等价，也不能重舍入。 |
| users.borderline_source | migration来源值 | 用户当前偏好仍需保存；若不保留该来源标签，需有审计来源去向。字段的individual/role_bulk等值仍有当前语义。 |
| alert_notification_audience.qualification_reasons_json | legacy资格来源 | 当前提醒兼容逻辑会识别此来源；去掉可能改变旧受众的重复提醒资格，必须有等价迁移规则。不能删除整个资格来源字段。 |

## 四、不要误删的当前业务属性

- alerts.is_borderline_promoted：正面复核仍会更新，通知资格逻辑仍读取；即使本快照全为0，也不能像is_provisional那样认定已退役。
- roles.sensitivity_mode：当前普通推送资格和角色管理使用。
- users/roles的Borderline偏好、来源、更新人/时间：当前个人覆盖、角色整组覆盖和审计使用。
- notifications.visibility_policy：明确可见范围，避免删角色或名单清空后扩大授权。
- notification_phase_dispatches.recipient_count_unit：当前用户数与设备数有不同含义，不是纯旧版标记。
- notification_deliveries.recipient_user_id：保存发送当时用户，避免设备换账号后历史归属被覆盖。
- vlm_descriptions.config_version_id：记录描述实际使用的配置，新记录有长期追溯用途。
- 模型、提示词、配置版本引用和历史snapshot：保存过去业务事实；“历史”不等于“废弃”。
- locations.building/floor/description：现有可选地点资料，当前无独立录入界面；不属于本次旧流程标记。是否保留需按地点管理需求单独决定。

## 五、本次只读核对事实

同一只读快照中：127条警报的is_provisional与is_borderline_promoted均为0；workflow含2条legacy_escalated；13条旧复核、0个task；39条旧计划、42个当前phase；3份prompt登记均未保存正文和SHA；80条通知没有outbox；notification_recipients有2条角色行。

警报敏感度标签：certain90、moderate4、sensitive33。武器配置：19个版本含历史五类别，3个为knife/long_gun，8个为knife/gun。

这些事实只证明当前快照。字段是否退役还结合代码读写检查；数据库里全为0或全为空不是充分删除依据。本次未跑应用测试、未启动模型或训练、未执行迁移。

## 代码依据

- services.py：record_violence_candidate/record_weapon_confirmation写is_provisional=False；武器单hit兼容分支仍读confidence_threshold；NotificationService.dispatch的outbox路径及冷却回退路径不同。
- inference/orchestrator.py：_finalise_new_alert仍传入cooldown参数，说明“传参存在”与“主路径实际使用”需要区分。
- sqlite_repository.py：旧Mobile设置、角色/个人偏好分别维护；正面复核标记参与资格；角色收件关系仍由列表查询使用。
- main.py：Borderline GET仍返回旧enabled/recipientRoleIds；旧批量PUT在个人例外存在后返回409；escalate入口已返回410但历史状态仍读取。
- models.dart/firebase_api_repository.dart：isLegacy和旧状态仍有DTO/展示兼容语义。

下一步若用户选择这些删减，应更新设计规格、DDL生成器、迁移映射、约束验证和说明文档，不能只在数据字典删掉字段名称。
