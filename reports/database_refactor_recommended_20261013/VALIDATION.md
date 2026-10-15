# 推荐重构规划验证记录

执行日期：2026-10-13；Python SQLite `3.45.1`。

- 独立内存数据库成功创建 **31张STRICT表**，**305个属性**；31表都有合成数据。
- **94个针对性正/反约束场景通过**，并检查真实拒绝机制；deferred FK检查包含commit时点。
- 目标合成数据库：integrity_check=ok；foreign_key_check无错误。
- 当前系统DB仅mode=ro打开并在单一BEGIN快照中核对；未改变运行库、Web/Flutter源码或迁移版本。
- 在另一独立内存库导入真实prompt登记，按原task/version映射配置FK；所有当前历史配置逐字段与目标存储值比较，包括原ID/版本、JSON原字符串与数值精度。只验证prompt/config的表示与关联，不是完整44表迁移。

## 只读基线核对

| 项目 | 数量 |
|---|---:|
| source_tables | 44 |
| schema_version | 14 |
| config_versions | 30 |
| source_foreign_key_violations | 0 |
| delivery_mirrors | 115 |
| delivery_mirror_differences | 0 |
| feed_deliveries_without_outbox_mirror | 65 |
| payload_differences | 0 |
| queue_users_missing_feed_user | 0 |
| alerts_without_workflow | 0 |
| status_contradictions | 0 |
| review_tasks | 0 |
| review_task_event_missing | 0 |
| timestamps_checked | 4657 |
| timestamp_errors | 0 |

镜像比对包含status/provider/error/attempt/time；payload以原字串完全一致为依据。当前task数量为0时，event外键核对不构成真实任务迁移证明，task情形仅有合成约束验证。时间核对验证时区与无损规范化，未把用户信息、FCM注册值或payload正文写入报告。

## 证明范围

这些结果证明DDL可执行、列出的局部约束场景及当前基线的一些合并前提。没有执行生产迁移、repository改写、Firebase/FCM真实发送、worker并发/重启、文件搬迁或Web/Mobile端到端回归。DESIGN.md的验收矩阵是后续实施要求，不是本次已完成测试。

## 约束场景

| 场景 | 预期 | 结果 |
|---|---|---|
| Boolean不允许2 | 拒绝非法数据 | PASS |
| STRICT整数拒绝非数字 | 拒绝非法数据 | PASS |
| 必需配置不能用NULL绕过 | 拒绝非法数据 | PASS |
| Firebase UID最多128字符 | 拒绝非法数据 | PASS |
| Firebase UID唯一 | 拒绝非法数据 | PASS |
| Provider长显示缓存不截断 | 接受合法场景 | PASS |
| 用户允许多角色 | 接受合法场景 | PASS |
| 角色关系未知FK | 拒绝非法数据 | PASS |
| 角色关系不可重复 | 拒绝非法数据 | PASS |
| 摄像头名称80字符上限 | 拒绝非法数据 | PASS |
| FCM注册值20字符下限 | 拒绝非法数据 | PASS |
| 媒体路径拒绝父目录穿越 | 拒绝非法数据 | PASS |
| SHA固定64位小写hex | 拒绝非法数据 | PASS |
| 同摄像头仅一个active source | 拒绝非法数据 | PASS |
| 同文件可作另一摄像头输入 | 接受合法场景 | PASS |
| RTSP不能同时引用上传文件 | 拒绝非法数据 | PASS |
| 截图必须引用image文件 | 拒绝非法数据 | PASS |
| stride不大于segment | 拒绝非法数据 | PASS |
| 阈值排序 | 拒绝非法数据 | PASS |
| N不大于M | 拒绝非法数据 | PASS |
| scene帧为偶数 | 拒绝非法数据 | PASS |
| 升级晚于重复 | 拒绝非法数据 | PASS |
| 模型标识不能空 | 拒绝非法数据 | PASS |
| prompt用途FK校验 | 拒绝非法数据 | PASS |
| 概率不能大于1 | 拒绝非法数据 | PASS |
| detector不能使用scene模型 | 拒绝非法数据 | PASS |
| 分数逐值校验 {"gun":null} | 拒绝非法数据 | PASS |
| 分数逐值校验 {"gun":1.1} | 拒绝非法数据 | PASS |
| 分数逐值校验 {"gun":"0.3"} | 拒绝非法数据 | PASS |
| 新配置类别/精度 | 拒绝非法数据 | PASS |
| 历史阈值精度及long_gun保留 | 接受合法场景 | PASS |
| 武器跨敏感度排序未强加新规则 | 接受合法场景 | PASS |
| 身体霸凌可用共享violence模型 | 接受合法场景 | PASS |
| 身体霸凌可按现有save_alert登记任务 | 接受合法场景 | PASS |
| 配置版本不能原位修改 | 拒绝非法数据 | PASS |
| prompt版本不能硬删除 | 拒绝非法数据 | PASS |
| 冻结prompt需要内容和SHA | 拒绝非法数据 | PASS |
| 每detector仅一个task复核 | 拒绝非法数据 | PASS |
| 复核event必须同警报摄像头 | 拒绝非法数据 | PASS |
| task最多尝试3 | 拒绝非法数据 | PASS |
| positive需要raise_alert=1 | 拒绝非法数据 | PASS |
| positive不能NULL绕过 | 拒绝非法数据 | PASS |
| negative不能获得补充资格 | 拒绝非法数据 | PASS |
| VLM snapshot 缺键 | 拒绝非法数据 | PASS |
| VLM snapshot JSON null | 拒绝非法数据 | PASS |
| VLM snapshot 整数类型 | 拒绝非法数据 | PASS |
| VLM snapshot Boolean类型 | 拒绝非法数据 | PASS |
| positive与补充资格一起保存 | 接受合法场景 | PASS |
| 旧审计诚实表示无任务输入 | 接受合法场景 | PASS |
| task输入上下文不能空 | 拒绝非法数据 | PASS |
| SceneSummary 最多2证据 | 拒绝非法数据 | PASS |
| SceneSummary 必须有description | 拒绝非法数据 | PASS |
| SceneSummary 证据必须字符串 | 拒绝非法数据 | PASS |
| 保留raw JSON fallback | 接受合法场景 | PASS |
| 保留长text和未知旧config | 接受合法场景 | PASS |
| 同alert只有一个active phase | 拒绝非法数据 | PASS |
| 期限必须等于anchor加delay | 拒绝非法数据 | PASS |
| 期限不允许微秒偏移 | 拒绝非法数据 | PASS |
| 边界微秒跨天精确保留 | 接受合法场景 | PASS |
| active phase不能串alert | 拒绝非法数据 | PASS |
| phase source通知必须同alert | 拒绝非法数据 | PASS |
| 只有Phase B可选live角色 | 拒绝非法数据 | PASS |
| 停止phase清理active pointer | 接受合法场景 | PASS |
| 不同phase保留独立历史 | 接受合法场景 | PASS |
| 新phase不能假冒归档legacy | 拒绝非法数据 | PASS |
| 同phase-stage主消息唯一 | 拒绝非法数据 | PASS |
| 主stage后允许late repeat另一消息 | 接受合法场景 | PASS |
| recipient用户重复被partial UNIQUE拒绝 | 拒绝非法数据 | PASS |
| recipient必须用户或角色恰一项 | 拒绝非法数据 | PASS |
| role行不能冒充queued用户 | 拒绝非法数据 | PASS |
| feed名单与queue名单可以不同 | 接受合法场景 | PASS |
| 逐消息设备结果唯一 | 拒绝非法数据 | PASS |
| 设备token转移不改旧发送用户 | 接受合法场景 | PASS |
| lease恢复允许超过3次claim | 接受合法场景 | PASS |
| 消息内容发送后被冻结 | 拒绝非法数据 | PASS |
| 未发送消息可完成payload组装 | 接受合法场景 | PASS |
| stage跳过可没有消息 | 接受合法场景 | PASS |
| stage不能引用另一stage消息 | 拒绝非法数据 | PASS |
| 每phase-user-stage关系唯一 | 拒绝非法数据 | PASS |
| action幂等key唯一 | 拒绝非法数据 | PASS |
| confirm-only选择1到20角色 | 拒绝非法数据 | PASS |
| 旧action长note不截断 | 接受合法场景 | PASS |
| 只允许模拟call | 拒绝非法数据 | PASS |
| 行政审计不可改写内容 | 拒绝非法数据 | PASS |
| actor删除不删除审计 | 接受合法场景 | PASS |
| 警报read集合唯一 | 拒绝非法数据 | PASS |
| 通知read集合唯一 | 拒绝非法数据 | PASS |
| 拒绝非规范时间 2026-02-30T01:02:03.123456Z | 拒绝非法数据 | PASS |
| 拒绝非规范时间 2026-10-13T24:00:00.000000Z | 拒绝非法数据 | PASS |
| 拒绝非规范时间 2026-10-13T01:02:03.123Z | 拒绝非法数据 | PASS |
| 拒绝非规范时间 2026-10-13T01:02:03.123456+00:00 | 拒绝非法数据 | PASS |
| 拒绝非规范时间 0000-01-01T01:02:03.123456Z | 拒绝非法数据 | PASS |
| 有效闰日 | 接受合法场景 | PASS |
| restricted名单删除仍restricted | 接受合法场景 | PASS |
