# GuardAId 推荐重构规划：44 → 31 张表

**推荐31张实际表：30张业务/配置/运行表，1张schema_migrations。** 数量由实体职责及重复分析产生，不以ERD框数为目标。本次交付规划与独立DDL样稿，当前44表运行数据库和应用源码保持原实现。

| 模块 | 表 | 职责边界 |
|---|---|---|
| 身份与权限（4） | users、roles、user_roles、devices | 身份/角色/关联/设备各有独立目的；个人和角色一对一偏好归主实体 |
| 监控与媒体（4） | locations、cameras、media_files、camera_sources | 共享地点、摄像头身份、实际文件、可替换输入来源分开 |
| 模型与策略（3） | model_versions、prompt_versions、config_versions | 模型/提示词独立版本；五张配置明细按同一版本合为完整policy |
| 事件与响应（7） | alerts、detection_events、borderline_reviews、evidence_records、vlm_descriptions、alert_actions、alert_reads | 当前状态、检测事实、复核、证据、描述、人的响应、已读关系各自明确 |
| 通知与提醒（10） | notifications、notification_outbox、notification_recipients、notification_deliveries、notification_reads、alert_notification_audience、notification_phases、notification_phase_roles、notification_phase_dispatches、notification_phase_audience | 内容/任务/名单/设备结果/read/首发资格/时钟/角色选择/阶段claim/用户阶段防重分开 |
| 全局设置与治理（3） | mobile_settings、audit_logs、schema_migrations | 单例偏好+revision、行政审计、结构升级账表，各有长期职责 |

## 为什么是31张

44 − 4（五配置合一）− 2（用户/角色一对一偏好）− 1（单例revision）− 1（当前workflow归alert）− 1（复核镜像）− 1（人工动作模型）− 2（旧计划/阶段兼容双写）− 1（重复用户名单）− 1（重复设备结果）+ 1（真正的phase-role关联）= **31**。

保留outbox：消息内容和可领取/重试的发送任务不是同一生命周期。保留stage dispatch：claim、跳过、no-recipients可能没有消息，不能制造假notification。保留两个read表及audit：它们并非相同事件目的。没有把model/prompt/config、六种通知关系或所有推理混入多态大表。

## 约束按什么确定

- 数据字典逐属性说明类型、NULL、默认值、范围/长度/枚举、PK/FK及理由；另给全部联合CHECK、删除行为、索引和有限trigger。
- 长度来自现有接口或Firebase等协议。外部Firebase显示名缓存与本地编辑80字符不同，不盲目套同一限制。没有gender、无必要的job/debug/attempt实体或统一VARCHAR(255)。
- 状态用当前枚举，Boolean用0/1；概率0..1；stride≤segment、阈值排序、N≤M、scene偶数帧、升级更晚等来自当前业务。旧阈值类别/精度保留。
- UTC时间仍TEXT但无损统一六位小数Z格式，真实日历检查、精确期限计算；视频source time保持REAL秒。SHA保留64位hex既有格式。
- 关系用FK：尤其live Broadcast Roles新建phase-role表。JSON只用于参数/输出/历史snapshot。旧含NULL的recipient UNIQUE改两个partial UNIQUE。
- 重试、权限、最后管理员、工作流程CAS/幂等、真实SHA/路径及外部发送仍必须配套服务事务；不能用SQL CHECK宣称保证所有系统行为。

## 功能与迁移边界

完整Web/Mobile功能覆盖、旧44表的逐一去向、派生API字段、分阶段迁移/回退和验收矩阵都在DESIGN.md。当前数据核对的配置、消息/投递镜像及workflow一致性结果见VALIDATION.md。

旧status由workflow确定后仍由DTO返回；未记录的历史prompt内容、description实际config、delivery当时用户不伪造。历史镜像只有能证明同一事实才合并，原ID/时间/差异note要保存或归档。

本提案明确修正两项边界：recipient集合真正去重；restricted通知名单为空不会扩大为广播。后者在角色删除回归中必须验证，正常消息/角色选择功能继续保留。

## 文件

- DESIGN.md：完整逐属性设计、实体理由、31表PK/FK/CHECK/索引/trigger、44表映射、功能及迁移规划。
- schema_proposal.sql：新空库DDL样稿，不是运行迁移脚本。
- erd_full.mmd：从真实FK/NULL/唯一约束生成的完整ERD；partial uniqueness须看字典注释。
- erd_identity.mmd、erd_monitoring.mmd、erd_response.mmd：方便分区绘图的模块关系图。
- VALIDATION.md：独立DDL/约束及只读基线核对的真实执行范围。

本方案是推荐方向，不是已经实施或经过全系统验证的“全局最优”证明。

## 可点击的阅读入口

- [完整设计及305个属性的约束理由](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/DESIGN.md)
- [原44表、339个属性的迁移去向](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/ATTRIBUTE_MAPPING.md)
- [31表的PK/FK与删除规则速查](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/KEYS.md)
- [44个HTTP路由及30项Mobile契约](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/API_COVERAGE.md)
- [可执行的新空库DDL样稿](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/schema_proposal.sql)
- [本次实际执行的验证与范围](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/VALIDATION.md)
- [完整ERD源码](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/erd_full.mmd)
- [身份模块ERD](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/erd_identity.mmd)
- [监控模块ERD](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/erd_monitoring.mmd)
- [通知与响应模块ERD](C:/Users/YSTeh/Documents/INTI/2026_2027_Y3/Python/6000CEM/reports/database_refactor_recommended_20261013/erd_response.mmd)
