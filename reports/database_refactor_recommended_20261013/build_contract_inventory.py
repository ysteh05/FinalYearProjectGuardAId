"""Static contract inventory only; does not import or execute the application."""
from pathlib import Path
import ast
import re

ROOT=Path(__file__).resolve().parent
WORKSPACE=ROOT.parents[1]
MAIN=WORKSPACE/'guardaid_monitor/app/main.py'
DART=WORKSPACE/'mobileapp/lib/src/repository.dart'


def source_link(path,line,label):
    return f'[{label}]({path.as_posix()}:{line})'


def group(name):
    if name in {'health','runtime_status'}:
        return 'schema_migrations/model_versions/config_versions + runtime服务', '保留readiness/worker状态和schema真实版本；不能伪造已执行升级。'
    if name=='index':return '现有静态Web资源','保留首页及静态资源挂载。'
    if name in {'plan_simulation','start_analysis','list_analysis_jobs','get_analysis_job','cancel_analysis_job'}:
        return '现有SimulationPlanner/InferenceOrchestrator；持久结果写alerts/detection_events/reviews/evidence/descriptions', '进度、ETA、取消、debug由当前内存服务负责；API及运行行为保留。'
    if 'camera' in name:
        return 'locations/cameras/camera_sources/media_files', '身份、当前来源、替换/删除、视频/帧预览、文件访问及错误码保留。'
    if 'evidence' in name:
        return 'evidence_records/media_files/alerts + 媒体服务', 'Mobile沿用active+role授权；视频/截图响应、路径containment及到期清理保留。'
    if name in {'get_settings','save_settings'}:
        return 'config_versions/model_versions/prompt_versions', '完整配置按版本单行保存；DTO字段不改，master switch仍独立。'
    if 'borderline' in name:
        return 'users/roles/mobile_settings/config_versions/audit_logs', '角色整组覆盖/个人覆盖、全局revision CAS/409与权限检查保留。'
    if name=='mobile_session':return 'users/roles/user_roles + Firebase验证','Firebase UID唯一；provider缓存完整；pending/disabled状态及权限返回保留。'
    if name=='register_mobile_device':return 'devices','token唯一、可转移当前owner、启用/lastSeen及发送前校验保留。'
    if 'role' in name:
        return 'roles/user_roles/users/audit_logs；删除时phase_roles/recipients关联清理', '系统角色/最后管理员保护、授予权限上限、sensitivity用途保留；restricted空名单不变broadcast。'
    if 'user' in name or name=='update_mobile_profile':
        return 'users/user_roles/roles/devices/audit_logs + Firebase Admin', '创建/关联、默认个人偏好、停用撤销设备、primary role、资料更新、邮件流程与权限保留。'
    if 'notification' in name:
        return 'notifications/notification_recipients/notification_reads', 'feed按feed_visible及显式visibility查询；mark-all-read只更新本用户，read不停止phase。'
    if name=='mark_mobile_alert_read':return 'alert_reads/alerts','首次已读唯一、重复read幂等；read不停止提醒。'
    if 'broadcast' in name:return 'alerts/roles/user_roles + 当前授权','沿用可选角色、有效用户计数及权限预览，提交时重验，preview不写phase。'
    if name in {'submit_mobile_alert_action','simulate_mobile_emergency_call'}:
        return 'alerts/alert_actions/phases/phase_roles/notifications/outbox/recipients/audit_logs', '全部旧/新动作、revision409、key幂等、同事务Phase A/B转换及模拟通话保留。'
    if 'alert' in name:
        return 'alerts/detection_events/reviews/evidence/descriptions/alert_actions/alert_reads/phases + identity JOIN', '旧status派生、每次版本/分数/窗口、最新描述和完整历史保持；Active有角色可看全部警报。'
    raise AssertionError('unclassified endpoint '+name)


MOBILE = {
    'isDemo':('现有repository模式','demo与Firebase实现继续分别提供。'),
    'openedAlertIds':('现有FCM打开消息流','公开alert ID及深链接保持。'),
    'takeInitialAlertId':('现有FCM初始消息','首次打开alert ID保持；不增加DB表。'),
    'signIn':('Firebase认证 + /api/mobile/session','账号/权限/status返回保持；认证密码/token不存SQLite。'),
    'refreshSession':('/api/mobile/session','保留当前session刷新/停用处理。'),
    'sendPasswordReset':('Firebase Auth','外部认证邮件流程保留。'),
    'signOut':('Firebase Auth + devices','保留退出及当前设备状态处理；旧delivery归属不改。'),
    'loadAlerts':('/api/mobile/alerts','alert DTO、派生status与广泛警报浏览授权保持。'),
    'loadAlertDetails':('/api/mobile/alerts/{alert_id}','全部证据、检测统计、描述、动作、phase及revision保持。'),
    'markAlertRead':('/api/mobile/alerts/{alert_id}/read','alert_reads独立，不停止phase。'),
    'loadNotifications':('/api/mobile/notifications','通知feed与已读保持，显式visibility处理角色删除。'),
    'loadRoles':('/api/mobile/roles','可见角色及当前权限边界保持。'),
    'loadUsers':('/api/mobile/admin/users','用户管理权限、排序/DTO保持。'),
    'submitAlertAction':('/api/mobile/alerts/{alert_id}/actions','legacy入口、note、响应历史和状态保持。'),
    'loadBroadcastRoleOptions':('/api/mobile/alerts/{alert_id}/broadcast-preview','候选角色及有效人数保持，提交重新验证。'),
    'submitWorkflowAction':('/api/mobile/alerts/{alert_id}/actions','完整状态机、key、expectedRevision及409保持。'),
    'recordSimulatedEmergencyCall':('/api/mobile/alerts/{alert_id}/simulate-emergency-call','只模拟；历史显示/去重契约保持。'),
    'markAllNotificationsRead':('/api/mobile/notifications/mark-all-read','notification_reads独立且用户隔离。'),
    'updateRoleSensitivity':('/api/mobile/admin/roles/{role_id}','仅普通推送资格，不收窄警报浏览。'),
    'createRole':('/api/mobile/admin/roles','角色权限/命名/default偏好、审计保持。'),
    'deleteRole':('/api/mobile/admin/roles/{role_id}','最后管理员/系统角色保护；历史snapshot保留。'),
    'updateUserAssignment':('/api/mobile/admin/users/{user_id}','primary role UI及DB M:N语义、状态和默认偏好保持。'),
    'createUser':('/api/mobile/admin/users','Firebase创建/关联、本地映射及错误码保持。'),
    'sendUserSetupEmail':('Firebase Auth邮件流程','外部邮件和失败显示保持，不需要新增临时DB实体。'),
    'deactivateUser':('/api/mobile/admin/users/{user_id}/deactivate','停用/撤销device、权限保护和审计保持。'),
    'updateProfile':('/api/mobile/profile','本地编辑长度与provider同步缓存区别处理。'),
    'updateBorderlineSettings':('/api/mobile/admin/borderline-settings','旧全局/角色集合兼容入口保持。'),
    'loadBorderlineSettings':('/api/mobile/admin/borderline-settings','Web master与账号/角色偏好及revision明确分开。'),
    'setBorderlineRoleEnabled':('/api/mobile/admin/borderline-settings/roles/{role_id}','整组覆盖及revision409保持。'),
    'setBorderlineUserEnabled':('/api/mobile/admin/borderline-settings/users/{user_id}','个人覆盖/来源及revision409保持。'),
}


def main():
    tree=ast.parse(MAIN.read_text(encoding='utf-8'))
    routes=[]
    for node in ast.walk(tree):
        if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
        for deco in node.decorator_list:
            if isinstance(deco,ast.Call) and isinstance(deco.func,ast.Attribute) and isinstance(deco.func.value,ast.Name) and deco.func.value.id=='app' and deco.func.attr in {'get','post','put','patch','delete'}:
                path=ast.literal_eval(deco.args[0])
                calls=sorted({call.func.attr for call in ast.walk(node) if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute)
                    and isinstance(call.func.value,ast.Name) and call.func.value.id=='repository'})
                routes.append((node.lineno,deco.func.attr.upper(),path,node.name,calls))
    docs=['# Web / Mobile契约及改写范围清单','',
        '本清单静态读取main.py的全部HTTP路由以及Dart GuardAidRepository接口，未启动应用。每项有目标职责及必须保留的行为。表中direct repository calls只列该路由直接调用，不代表展开全部helper调用；不能把清单当作已通过回归。','',
        f'## 当前HTTP路由：{len(routes)}个（含首页）','',
        '| 路由 | 当前实现 / direct repository calls | 目标与验收重点 |','|---|---|---|']
    for line,method,path,name,calls in sorted(routes):
        target,contract=group(name)
        docs.append(f'| `{method} {path}` | {source_link(MAIN,line,name)}；`{", ".join(calls) or "service/helper"}` | {target}。{contract} |')
    interface=DART.read_text(encoding='utf-8').split('class DemoGuardAidRepository')[0]
    members=re.findall(r'^  (?:Future<[^\n]+?>\s+|Stream<[^\n]+?>\s+get\s+|bool\s+get\s+)(\w+)\s*(?:\(|;)',interface,re.MULTILINE)
    assert set(members)==set(MOBILE), (set(members)-set(MOBILE),set(MOBILE)-set(members))
    docs += ['',f'## Mobile repository契约：{len(members)}项','',
        '| 接口成员 | 当前依赖 | 保留点 |','|---|---|---|']
    for name in members:
        endpoint,contract=MOBILE[name]
        line=next(i for i,x in enumerate(interface.splitlines(),1) if re.search(r'\b'+name+r'\s*(?:\(|;)',x))
        docs.append(f'| {source_link(DART,line,name)} | `{endpoint}` | {contract} |')
    docs += ['', '## 改写必须成组完成','',
        '| 代码区域 | 必要适配 |','|---|---|',
        '| SQLiteRepository配置/身份/偏好 | 五配置JOIN改单行快照；角色/个人偏好一对一JOIN改主表字段；global revision CAS仍同事务。 |',
        '| SQLiteRepository警报读取/动作 | workflow JOIN改alert字段，旧status派生；动作双写结束；legacy展示去重与新key/revision事务保持。 |',
        '| Orchestrator复核 | task输入、claim、retry、terminal/supplemental持久化同一review；结束legacy结果mirror写入，audit_only不进入worker。 |',
        '| 通知outbox worker | JOIN message获取canonical payload；queued_target名单、send-time owner校验、逐设备结果单写；保留FCM错误分类与退避/lease恢复。 |',
        '| escalation worker | 新phases/dispatches/audience SQL；Phase B live角色FK；保留无消息stage、late-repeat及过期替代语义，结束旧plan双写。 |',
        '| main.py DTO/权限 | DTO保持，visibility_policy显式查询；补齐必需模型/量化/context非空验证；警报与通知可见规则分别保持。 |',
        '| Flutter FirebaseApiRepository/Models/Demo | 后端DTO保持可减少改动，仍逐成员回归；deep links/notification payload/409/枚举兼容；demo不新增SQLite。 |',
        '| migrations/runtime health/media | 真正新schema ledger、无损备份/迁移/回退；filesystem处理仍由媒体服务负责；不得造假metadata或升级记录。 |',
        '', '实施验收需覆盖响应字段、排序/分页、错误码、访问权限和写入副作用。DB结构可以表示全部已识别功能，但在repository/worker适配完成前，不能直接启用本DDL。']
    (ROOT/'API_COVERAGE.md').write_text('\n'.join(docs)+'\n',encoding='utf-8')
    print(f'Contract inventory: {len(routes)} routes; {len(members)} Mobile interface members.')


if __name__=='__main__':main()
