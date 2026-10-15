"""Recommended schema PLAN. Generates artifacts; never opens the runtime DB."""
from pathlib import Path
from types import SimpleNamespace
import copy
import json
import re

ROOT = Path(__file__).resolve().parent
from schema_helpers import col, ident, text, enum, boolean, number, document, fk
inputs=json.loads((ROOT/"schema_input_definitions.json").read_text(encoding="utf-8"))
helpers=SimpleNamespace(TABLES=inputs["tables"],policy_cols=inputs["policy_columns"])
TABLES, TRIGGERS = [], []
SENS = ("sensitive","moderate","certain")


def stamp(name, required=False, reason=None):
    digit = "[0-9]"
    pattern = digit*4+"-"+digit*2+"-"+digit*2+"T"+digit*2+":"+digit*2+":"+digit*2+"."+digit*6+"Z"
    shape = (f"length({name})=27 AND {name} GLOB '{pattern}' "
        f"AND CAST(substr({name},1,4) AS INTEGER) BETWEEN 1 AND 9999 "
        f"AND COALESCE(date(substr({name},1,10),'+0 days')=substr({name},1,10),0) "
        f"AND CAST(substr({name},12,2) AS INTEGER) BETWEEN 0 AND 23 "
        f"AND CAST(substr({name},15,2) AS INTEGER) BETWEEN 0 AND 59 "
        f"AND CAST(substr({name},18,2) AS INTEGER) BETWEEN 0 AND 59")
    return col(name,"TEXT",("NOT NULL " if required else "")+f"CHECK({name} IS NULL OR ({shape}))",
        reason or "统一UTC ISO8601微秒格式 YYYY-MM-DDTHH:MM:SS.ffffffZ；保留微秒，固定格式才可按文本精确排序")


def sha(name, reason):
    return col(name,"TEXT",f"CHECK({name} IS NULL OR (length({name})=64 AND {name} NOT GLOB '*[^0-9a-f]*'))",reason+"；SHA-256为64位小写hex，保持现有存储/API格式，不做无必要编码迁移")


def T(name,purpose,columns,rules=(),indexes=(),why="",decision="保留"):
    TABLES.append(dict(name=name,purpose=purpose,columns=columns,rules=list(rules),indexes=list(indexes),why=why,decision=decision))


def clone(name, rename=None):
    original = next(t for t in helpers.TABLES if t["name"]==name)
    obj=copy.deepcopy(original)
    rename=rename or {}
    converted=[]
    for n,typ,sql,reason in obj["columns"]:
        new=rename.get(n,n)
        if n.endswith("_at") or n=="delete_after":
            converted.append(stamp(new,"NOT NULL" in sql))
        else:
            for before,after in rename.items():sql=re.sub(r"\b"+re.escape(before)+r"\b",after,sql)
            converted.append(col(new,typ,sql,reason))
    rules,indexes=[],[]
    for group,out in ((obj["rules"],rules),(obj["indexes"],indexes)):
        for s in group:
            for before,after in rename.items():s=re.sub(r"\b"+re.escape(before)+r"\b",after,s)
            out.append(s)
    return converted,rules,indexes


def add_cloned(name,purpose,why,rename=None):
    cs,rs,ix=clone(name,rename)
    T(name,purpose,cs,rs,ix,why)


add_cloned("users","账号身份、状态、显示资料及所属个人Borderline偏好",
    "一个用户的一份偏好由同一个user_id决定；随账号存在，无独立生命周期。合并原borderline_user_settings，保留NULL=未物化、来源及独立修改时间。Firebase缓存不套用本地编辑80字符限制。",
    {"borderline_updated_by":"borderline_updated_by_user_id"})
add_cloned("roles","角色授权能力、通知敏感度及角色Borderline默认值",
    "权限与角色默认偏好都由role_id决定。合并原borderline_role_settings；系统角色保护、授权上限由服务事务执行。compat选择标记替代单例设置中的活角色ID数组。",
    {"borderline_updated_by":"borderline_updated_by_user_id"})
add_cloned("user_roles","明确的用户↔角色分配关系",
    "用户与角色具有不同身份及生命周期，关系必须独立。当前UI单角色不成为数据库永久唯一限制；保留现有多角色关系能力。",
    {"assigned_by":"assigned_by_user_id"})
add_cloned("devices","当前设备归属、FCM注册值、启用及在线状态",
    "一用户可有多设备；token可以在重新登录时改变当前归属。与逐次通知结果分开，避免历史发送结果阻止token转移。",
    {"fcm_token":"fcm_registration_value"})
add_cloned("locations","摄像头共享的地点主数据",
    "名称标准化去重、建筑/楼层/说明由地点决定，不重复塞进每台摄像头。normalized_name可能因Unicode casefold扩长，不强设与name相同长度。")
add_cloned("cameras","稳定摄像头身份、地点及分析状态",
    "摄像头身份与可以替换的视频来源分离；与可产生多个警报的关系独立。")
cs,rs,ix=clone("media_files")
cs=[sha("sha256","文件内容摘要，可空表示未计算") if c[0]=="sha256" else c for c in cs]
T("media_files","实际媒体文件的唯一元数据",cs,rs,ix,
    "文件可被来源/证据引用。文件与使用关系、证据生命周期不同。视频保存在filesystem，数据库只记录元数据；relative_path唯一。")

T("camera_sources","摄像头输入来源及替换历史",[
    ident(),col("camera_id","TEXT","NOT NULL","来源所属摄像头"),col("media_file_id","TEXT","","上传视频引用文件；RTSP不引用媒体文件"),
    enum("source_type",("uploaded_video","rtsp"),"两个现有来源类型"),text("rtsp_endpoint_ref","仅保存服务器端点或secret引用，不保存URL密码"),
    number("duration_seconds","REAL",0,None,"完整输入视频时长；RTSP可为0",True,0),boolean("is_active","每台摄像头最多一个当前来源",default=1),stamp("created_at",True),
],[fk("camera_id","cameras",delete="CASCADE"),fk("media_file_id","media_files"),
   "CHECK((source_type='uploaded_video' AND media_file_id IS NOT NULL AND rtsp_endpoint_ref IS NULL) OR (source_type='rtsp' AND media_file_id IS NULL AND rtsp_endpoint_ref IS NOT NULL))"],
   ["CREATE UNIQUE INDEX ux_active_camera_source ON camera_sources(camera_id) WHERE is_active=1;","CREATE INDEX ix_camera_source_file ON camera_sources(media_file_id) WHERE media_file_id IS NOT NULL;"],
   "来源替换修改active关系；文件元数据不重复。source并非证据：所有者、输入类型和保留/清理策略不同，因此不合并为多态media_usages。")

T("model_versions","按任务保存不可变模型版本身份与部署来源",[
    ident(),enum("task",("violence","physical_contact_bullying","weapon","borderline_binary","scene_description"),"现有模型用途及save_alert按AlertType登记的身体霸凌；身体霸凌也可共享violence模型"),
    text("model_name","模型或供应商名称是可变长不透明字符串；不臆造80/255上限",True),text("version","模型版本身份，保持历史标签",True),
    text("framework","历史模型登记可能未记录framework，允许空"),text("quantization","量化描述没有当前固定枚举，允许空"),
    text("checkpoint_ref","服务器控制的checkpoint逻辑引用；不能接收客户端电脑任意绝对路径"),stamp("created_at",True),
],["UNIQUE(task,model_name,version)"],[],
    "多个检测/描述/复核共享版本；独立于策略及prompt生命周期。不与完整配置、全局偏好混成runtime_resources。历史未记录metadata不能伪造。")
T("prompt_versions","按用途保存提示词版本、内容及摘要",[
    ident(),enum("task",("borderline_binary","scene_description"),"现有两个VLM prompt任务"),text("version","保留原全局唯一prompt标签",True),
    enum("capture_state",("metadata_only","frozen"),"明确区分历史缺失内容与已冻结内容",default="metadata_only"),
    text("content","真实prompt内容不按任意字符上限截断"),sha("content_sha256","完整prompt的内容摘要，服务计算"),stamp("created_at",True),
],["UNIQUE(version)","CHECK(capture_state='metadata_only' OR (content IS NOT NULL AND content_sha256 IS NOT NULL))"],[],
    "二元复核prompt与场景prompt分别版本化；被引用的历史内容不能原位改变。capture_state是迁移事实标记，不是替未来功能创建的独立entity。")

policy_rename={"detection_debug_enabled":"detection_debug_mode_enabled","violence_input_frames":"violence_detector_input_frames",
    "weapon_legacy_confidence":"weapon_confidence_threshold","weapon_nms_floor":"weapon_nms_candidate_floor","weapon_nms_iou":"weapon_nms_iou_threshold",
    "weapon_thresholds_json":"weapon_class_thresholds_json","vlm_model_ref":"vlm_model_id","vlm_scene_frames":"vlm_scene_description_frames",
    "vlm_borderline_enabled":"vlm_borderline_review_enabled","vlm_scene_enabled":"vlm_scene_description_enabled",
    "binary_prompt_id":"binary_prompt_version_id","scene_prompt_id":"scene_prompt_version_id",
    "repeat_after_seconds":"same_audience_repeat_after_seconds","escalate_after_seconds":"all_users_escalation_after_seconds"}
policy=[]
for n,typ,sql,reason in helpers.policy_cols:
    n2=policy_rename.get(n,n)
    for before,after in policy_rename.items():sql=re.sub(r"\b"+re.escape(before)+r"\b",after,sql)
    constraint="NOT NULL "+sql
    if n2 in ("vlm_model_id","vlm_quantization"):
        constraint+=f" CHECK(length(trim({n2}))>0)"
    policy.append(col(n2,typ,constraint,reason))
policy=[p for p in policy if p[0]!="detection_debug_mode_enabled"]
T("config_versions","同一版本的完整运行策略快照",[
    ident("沿用cfg_000001等既有配置ID"),number("version_number","INTEGER",1,None,"唯一递增配置版本；不是用户输入的任意标识",True),
    text("created_by","保留local-demo、migration标签等来源；不能假定每个字符串都是user_id",True),
    col("created_by_user_id","TEXT","","能够确认是已认证用户时额外登记真实FK；后台/本地配置允许空"),stamp("created_at",True),
    enum("validation_profile",("runtime","historical"),"新配置沿用当前API类别/精度；历史阈值和类别不重舍入",default="runtime"),
    boolean("detection_debug_mode_enabled","保持当前配置开关及默认关闭",default=0),
    *policy,
],["UNIQUE(version_number)",fk("created_by_user_id","users"),fk("binary_prompt_version_id","prompt_versions"),fk("scene_prompt_version_id","prompt_versions"),
   "CHECK(violence_stride_seconds>0 AND violence_stride_seconds<=violence_segment_seconds)",
   "CHECK(violence_sensitive_threshold<=violence_moderate_threshold AND violence_moderate_threshold<=violence_certain_threshold)",
   "CHECK(weapon_n_required<=weapon_m_window)","CHECK(vlm_scene_description_frames%2=0)",
   "CHECK(all_users_escalation_after_seconds>same_audience_repeat_after_seconds)"],
   ["CREATE INDEX ix_config_creator ON config_versions(created_by_user_id) WHERE created_by_user_id IS NOT NULL;",
    "CREATE INDEX ix_config_binary_prompt ON config_versions(binary_prompt_version_id);","CREATE INDEX ix_config_scene_prompt ON config_versions(scene_prompt_version_id);"],
   "当前五张配置表的30个版本一一对应，save_settings同一事务写五行，get_settings同查。所有属性依赖同一配置版本，合并没有引入跨实体事实；减少缺一张明细就无法读取的故障点。模型/提示词仍独立。",
   "合并config_versions+四张明细表")

T("alerts","稳定事件及其当前唯一手机处理状态",[
    ident(),col("camera_id","TEXT","NOT NULL","事件所属摄像头"),enum("incident_type",("violence","physical_contact_bullying","weapon"),"现有三种事件"),
    enum("workflow_status",("awaiting_confirmation","confirmed_awaiting_handler","in_progress","resolved","dismissed","legacy_in_review","legacy_escalated"),"唯一当前状态来源；旧API status由固定映射计算"),
    enum("sensitivity_mode",SENS,"保留历史标签；不用于限制Active有角色用户浏览"),
    boolean("is_borderline_promoted","保留提升标记",default=0),boolean("is_provisional","旧记录兼容；不恢复已停用的提前告警行为",default=0),
    enum("borderline_review_status",("not_applicable","disabled","queued","running","positive","negative","failed","cancelled","legacy_unknown"),"用于异步复核聚合展示；不同于人工处理状态"),
    enum("evidence_status",("preparing","available","failed","legacy_unknown"),"证据准备状态，与事件解决状态独立"),
    col("opening_config_version_id","TEXT","NOT NULL","开场策略版本"),col("opening_model_version_id","TEXT","NOT NULL","开场detector模型版本"),
    col("binary_prompt_version_id","TEXT","","可选开场复核prompt版本"),col("handler_user_id","TEXT","","当前处理者；停用账号不删除其历史关联"),
    col("active_phase_id","TEXT","","指向同事件且仍active的phase"),
    document("broadcast_role_snapshot_json","当时选择的角色ID展示快照；运行收件资格使用notification_phase_roles的真实FK","array",True,"[]"),
    number("revision","INTEGER",0,None,"工作流程乐观并发token",True,0),stamp("created_at",True),stamp("workflow_updated_at",True),stamp("resolved_at"),
],["UNIQUE(id,camera_id)",fk("camera_id","cameras"),fk("opening_config_version_id","config_versions"),fk("opening_model_version_id","model_versions"),
   fk("binary_prompt_version_id","prompt_versions"),fk("handler_user_id","users"),fk("active_phase_id,id","notification_phases","id,alert_id",deferred=True),
   "CHECK(workflow_updated_at>=created_at)","CHECK(resolved_at IS NULL OR resolved_at>=created_at)","CHECK(workflow_status<>'in_progress' OR handler_user_id IS NOT NULL)"],
   ["CREATE INDEX ix_alert_camera_workflow ON alerts(camera_id,workflow_status);","CREATE INDEX ix_alert_active_phase ON alerts(active_phase_id) WHERE active_phase_id IS NOT NULL;","CREATE INDEX ix_alert_opening_config ON alerts(opening_config_version_id);",
    "CREATE INDEX ix_alert_opening_model ON alerts(opening_model_version_id);","CREATE INDEX ix_alert_handler ON alerts(handler_user_id) WHERE handler_user_id IS NOT NULL;",
    "CREATE INDEX ix_alert_prompt ON alerts(binary_prompt_version_id) WHERE binary_prompt_version_id IS NOT NULL;"],
   "workflow是每个alert恰好一条的当前状态，不具有独立身份/历史生命周期；并入alert。去掉可以由workflow确定的旧status重复真值，DTO仍返回原status。检测统计继续从events聚合；不要加入description或review次数。",
   "合并alerts+mobile_alert_workflows；status改为派生API字段")

T("detection_events","一次被警报接纳的检测事实",[
    ident(),col("alert_id","TEXT","NOT NULL","可聚合进同一事件，但每次检测独立保存"),col("model_version_id","TEXT","NOT NULL","该次模型版本"),
    col("config_version_id","TEXT","NOT NULL","该次策略版本，不冒充opening版本"),number("confidence","REAL",0,1,"模型概率分数",True),
    enum("decision_source",("threshold","vlm_promoted","weapon_n_of_m"),"现有判定来源"),
    number("source_start_seconds","REAL",0,None,"原视频窗口起点，可空表示旧记录未知"),number("source_end_seconds","REAL",0,None,"原视频窗口终点"),
    stamp("detected_at",True),document("qualifications_json","当时资格计算结果，不能用来替代当前权限","object",True,"{}"),
    document("weapon_class_scores_json","类别→概率映射；触发器逐值限制数值0..1","object",True,"{}"),
],[fk("alert_id","alerts",delete="CASCADE"),fk("model_version_id","model_versions"),fk("config_version_id","config_versions"),
   "CHECK(source_start_seconds IS NULL OR source_end_seconds IS NULL OR source_end_seconds>=source_start_seconds)"],
   ["CREATE INDEX ix_detection_alert_time ON detection_events(alert_id,detected_at);","CREATE INDEX ix_detection_model ON detection_events(model_version_id);","CREATE INDEX ix_detection_config ON detection_events(config_version_id);"],
   "检测事实与可运行/失败/重试的VLM任务不同；不合成inference_runs。多次检测的分数、来源窗口及版本不能被事件最高分覆盖。")

T("borderline_reviews","一次Borderline复核的输入、排队执行、结论及补充通知资格",[
    ident("任务沿用reviewtask ID，旧独立审计沿用review ID"),
    enum("record_origin",("task","audit_only"),"历史旧审计没有任务输入/执行时间；必须明确未知，不能捏造任务"),
    col("legacy_review_id","TEXT","","能证明同一事实的旧审计镜像合入task时保留原ID；不靠最近时间猜测合并"),
    col("camera_id","TEXT","NOT NULL","复核来源摄像头"),col("alert_id","TEXT","","task必填；旧negative审计允许没有警报"),
    col("detection_event_id","TEXT","","task必填且唯一；旧审计无已记录窗口key时可空"),
    col("model_version_id","TEXT","NOT NULL","实际复核模型版本，用FK取代任务中的松散标签"),col("prompt_version_id","TEXT","NOT NULL","实际二元复核prompt版本"),
    col("config_version_id","TEXT","NOT NULL","策略版本FK"),number("detector_confidence","REAL",0,1,"保留输入detector分数",True),
    number("source_start_seconds","REAL",0,None,"task必填；旧审计可以未知"),number("source_end_seconds","REAL",0,None,"结束不得早于开始"),
    text("context_path","服务器控制输入引用；迁移老绝对路径先验证归属，不允许客户端任意路径"),
    enum("status",("queued","running","positive","negative","failed","cancelled"),"复核生命周期；audit_only只允许positive/negative"),
    number("attempt_count","INTEGER",0,3,"task尝试最多3；旧审计未记录则NULL"),boolean("raise_alert","尚未得出结论为空；正/负分别1/0",required=False),
    enum("uncertainty",("low","medium","high"),"当前复核输出枚举",required=False),text("brief_reason","保留原因，不臆造没有接口依据的长度"),
    text("error_code","错误分类允许扩展，不只保存一个failed布尔值"),
    document("vlm_settings_json","提交给worker的真正VLM参数快照，可能含对全局配置的覆盖；逐字段校验"),
    enum("supplemental_notification_status",("not_applicable","eligible","handled","suppressed_disabled"),"持久标记防止重启/历史导入重放补充推送",default="not_applicable"),
    stamp("created_at"),stamp("started_at"),stamp("completed_at"),stamp("reviewed_at",reason="旧审计的原reviewed_at；task完成时间与镜像入账时间不同，分别保留"),
],["UNIQUE(detection_event_id)","UNIQUE(legacy_review_id)",fk("camera_id","cameras"),fk("alert_id,camera_id","alerts","id,camera_id"),
   fk("detection_event_id","detection_events"),fk("model_version_id","model_versions"),fk("prompt_version_id","prompt_versions"),fk("config_version_id","config_versions"),
   "CHECK(source_start_seconds IS NULL OR source_end_seconds IS NULL OR source_end_seconds>=source_start_seconds)",
   "CHECK((record_origin='task' AND alert_id IS NOT NULL AND detection_event_id IS NOT NULL AND context_path IS NOT NULL AND length(trim(context_path))>0 AND vlm_settings_json IS NOT NULL AND attempt_count IS NOT NULL AND source_start_seconds IS NOT NULL AND source_end_seconds IS NOT NULL AND created_at IS NOT NULL) OR (record_origin='audit_only' AND detection_event_id IS NULL AND context_path IS NULL AND vlm_settings_json IS NULL AND attempt_count IS NULL AND created_at IS NULL AND started_at IS NULL AND completed_at IS NULL AND reviewed_at IS NOT NULL AND status IN ('positive','negative') AND supplemental_notification_status='not_applicable'))",
   "CHECK(status NOT IN ('positive','negative') OR raise_alert IS NOT NULL)","CHECK(status<>'positive' OR raise_alert=1)","CHECK(status<>'negative' OR raise_alert=0)",
   "CHECK(supplemental_notification_status='not_applicable' OR (record_origin='task' AND status='positive'))",
   "CHECK(started_at IS NULL OR (created_at IS NOT NULL AND started_at>=created_at))",
   "CHECK(completed_at IS NULL OR (created_at IS NOT NULL AND completed_at>=created_at AND (started_at IS NULL OR completed_at>=started_at)))"],
   ["CREATE INDEX ix_review_queue ON borderline_reviews(status,created_at) WHERE record_origin='task' AND status IN ('queued','running');",
    "CREATE INDEX ix_review_alert_time ON borderline_reviews(alert_id,created_at);","CREATE INDEX ix_review_camera ON borderline_reviews(camera_id);",
    "CREATE INDEX ix_review_model ON borderline_reviews(model_version_id);","CREATE INDEX ix_review_prompt ON borderline_reviews(prompt_version_id);","CREATE INDEX ix_review_config ON borderline_reviews(config_version_id);"],
   "原task与review审计主要镜像同一复核结果，属于同一review生命周期。保留audit_only形式来表达旧记录真正缺失的数据，不把legacy结果恢复成可执行任务；detector/description仍独立。",
   "统一borderline_review_tasks和旧borderline_reviews")

T("evidence_records","警报证据关系、来源区间及保留期限",[
    ident(),col("alert_id","TEXT","NOT NULL","关联事件"),col("media_file_id","TEXT","NOT NULL","一份文件对应最多一条证据关系，保留原UNIQUE"),
    enum("evidence_type",("violence_clip","bullying_clip","weapon_screenshot"),"当前证据类型"),
    number("source_start_seconds","REAL",0,None,"来源视频起点；旧记录未知允许空"),number("source_end_seconds","REAL",0,None,"来源视频终点"),stamp("captured_at",True),stamp("delete_after"),
],["UNIQUE(media_file_id)",fk("alert_id","alerts",delete="CASCADE"),fk("media_file_id","media_files"),
   "CHECK(source_start_seconds IS NULL OR source_end_seconds IS NULL OR source_end_seconds>=source_start_seconds)","CHECK(delete_after IS NULL OR delete_after>=captured_at)"],
   ["CREATE INDEX ix_evidence_alert ON evidence_records(alert_id);","CREATE INDEX ix_evidence_retention ON evidence_records(delete_after) WHERE delete_after IS NOT NULL;"],
   "证据有自身captured/retention事实；与camera_sources的输入类型/active关系不同，因此独立。删除证据关系并不让DB自动删除物理文件，服务仍协调filesystem清理。")

T("vlm_descriptions","场景描述生成状态、输出及重新生成历史",[
    ident(),col("alert_id","TEXT","NOT NULL","所属事件"),col("model_version_id","TEXT","NOT NULL","场景模型版本"),
    col("prompt_version_id","TEXT","","旧记录未知prompt可空"),col("config_version_id","TEXT","","新增时记录实际policy；旧表未记录时NULL，不能编造opening版本"),
    enum("status",("pending","ready","failed","disabled"),"保持现有DescriptionStatus"),
    enum("output_format",("none","summary","raw_json","text"),"格式决定适用约束，保留当前fallback读取",default="none"),
    document("structured_json","已验证summary或可兼容读取的原始JSON，不一律强迫旧结构为summary",shape=None),
    text("display_text","原显示文本；仅summary.description受120上限，原text回退不能被截断"),text("error_code","失败原因分类"),stamp("created_at",True),stamp("completed_at"),
],[fk("alert_id","alerts",delete="CASCADE"),fk("model_version_id","model_versions"),fk("prompt_version_id","prompt_versions"),fk("config_version_id","config_versions"),
   "CHECK(completed_at IS NULL OR completed_at>=created_at)",
   "CHECK((output_format='none' AND structured_json IS NULL AND display_text IS NULL) OR (output_format IN ('summary','raw_json') AND structured_json IS NOT NULL) OR (output_format='text' AND structured_json IS NULL AND display_text IS NOT NULL))",
   "CHECK(status<>'ready' OR output_format<>'none')"],
   ["CREATE INDEX ix_description_alert_time ON vlm_descriptions(alert_id,created_at DESC);","CREATE INDEX ix_description_model ON vlm_descriptions(model_version_id);",
    "CREATE INDEX ix_description_prompt ON vlm_descriptions(prompt_version_id) WHERE prompt_version_id IS NOT NULL;","CREATE INDEX ix_description_config ON vlm_descriptions(config_version_id) WHERE config_version_id IS NOT NULL;"],
   "描述是每个事件可以重试/重新生成的一系列输出，并非detector事实或二元复核。读取最新ready结果；没有必要与其他推理记录共享大表和状态枚举。")

T("alert_actions","用户对事件的人工处理历史及幂等请求",[
    ident(),col("alert_id","TEXT","NOT NULL","被处理事件"),col("user_id","TEXT","NOT NULL","操作人必须真实存在；历史身份不硬删除"),
    enum("action",("acknowledge","review","dismiss","resolve","escalate","dismiss_false_alarm","confirm_only","confirm_and_handle","confirm_resolved","take_ownership","simulated_emergency_call"),"完整旧/新操作集合"),
    enum("record_origin",("legacy","workflow"),"保持Mobile isLegacy语义；不是不同类型业务实体"),text("note","新workflow沿用1000字符上限；旧无限制note保留原值"),
    document("broadcast_role_snapshot_json","用户当时选择的角色ID历史快照；实际Phase B关系另有FK","array"),
    text("idempotency_key","新workflow请求8..120；旧操作无key允许空"),stamp("created_at",True),
],[fk("alert_id","alerts"),fk("user_id","users"),
   "CHECK(record_origin='legacy' OR (idempotency_key IS NOT NULL AND length(idempotency_key) BETWEEN 8 AND 120 AND broadcast_role_snapshot_json IS NOT NULL AND (note IS NULL OR length(note)<=1000)))"],
   ["CREATE UNIQUE INDEX ux_alert_action_idempotency ON alert_actions(alert_id,idempotency_key) WHERE idempotency_key IS NOT NULL;",
    "CREATE INDEX ix_alert_actions_alert_time ON alert_actions(alert_id,created_at DESC);","CREATE INDEX ix_alert_actions_user ON alert_actions(user_id);"],
   "旧alert_actions和新workflow_events都是人的一次事件响应；统一历史事件模型。audit_logs保留另一条行政审计事实，不按相同时间乱删。已读关系仍单独保存。",
   "统一alert_actions+mobile_alert_workflow_events")

T("alert_reads","每用户每警报的首次浏览状态",[
    col("user_id","TEXT","NOT NULL","每人独立，不能放在alerts共享行"),col("alert_id","TEXT","NOT NULL","浏览目标"),stamp("read_at",True),
],["PRIMARY KEY(user_id,alert_id)",fk("user_id","users",delete="CASCADE"),fk("alert_id","alerts",delete="CASCADE")],
   ["CREATE INDEX ix_alert_reads_alert ON alert_reads(alert_id);"],
   "这是独立的用户↔警报状态关系，不是人工响应或行政审计；保留小而明确的联合主键。read不停止提醒。")

T("notifications","面向用户的逻辑通知意图、内容和稳定幂等身份",[
    ident(),col("alert_id","TEXT","NOT NULL","所属警报"),
    enum("message_kind",("initial","broadcast","decision_update","phase_reminder","late_repeat","manual","legacy"),"消息用途；同一领域的有限分类，不混入其他实体"),
    text("event_key","由旧outbox移入的稳定业务幂等键；旧直接发送通知可空；队列清理也不能失去去重身份"),
    enum("visibility_policy",("restricted","broadcast"),"显式通知可见范围；原无recipient的旧通知导入broadcast，不把新名单为空解释为全员授权",default="restricted"),
    col("phase_id","TEXT","","phase_reminder/late_repeat必须引用真实phase"),number("reminder_stage","INTEGER",1,2,"对应阶段1/2，仅提醒消息适用"),
    col("title","TEXT","NOT NULL","保持现有标题；协议最终字节大小由sender验证，不臆造字符上限"),
    col("body","TEXT","NOT NULL","通知正文，保存真实内容，不为压表数截断"),
    document("data_payload_json","逻辑通知与实际发送使用同一canonical payload；冻结的alertId等不是授权来源","object",True),stamp("created_at",True),
],["UNIQUE(event_key)","UNIQUE(id,alert_id)",fk("alert_id","alerts",delete="CASCADE"),
   fk("phase_id,alert_id","notification_phases","id,alert_id",delete="RESTRICT",deferred=True),
   "CHECK((message_kind IN ('phase_reminder','late_repeat') AND phase_id IS NOT NULL AND reminder_stage IS NOT NULL) OR (message_kind NOT IN ('phase_reminder','late_repeat') AND phase_id IS NULL AND reminder_stage IS NULL))"],
   ["CREATE INDEX ix_notifications_alert_time ON notifications(alert_id,created_at DESC);","CREATE INDEX ix_notifications_phase ON notifications(phase_id,reminder_stage) WHERE phase_id IS NOT NULL;",
    "CREATE UNIQUE INDEX ux_main_stage_message ON notifications(phase_id,reminder_stage) WHERE message_kind='phase_reminder';"],
   "通知作为可浏览内容有独立生命周期；发送任务失败/重试不应改变消息身份。保留notifications，不把跳过阶段造成为假的隐藏消息。payload镜像当前一致，采用一份canonical值；若切换时发现差异先阻断核对并归档原值。")

T("notification_outbox","某条通知的持久发送任务、领取与重试状态",[
    ident("保留现有dispatch/outbox ID，FCM dispatchId与内部队列引用不改变"),
    col("notification_id","TEXT","NOT NULL","一条消息最多一个当前发送任务；内容/默认payload通过FK读取，不再重复"),
    text("channel_id","Android beep channel的逻辑ID，与正文不同用途，不设无依据长度",True),
    enum("status",("pending","dispatching","sent","partial","failed","no_recipients"),"现有outbox生命周期"),
    number("attempt_count","INTEGER",0,None,"领取次数；正常失败重试阈值3，crash/lease恢复可增加领取数，不把它误作设备实际发送次数或硬截断",True,0),
    stamp("next_attempt_at",True),stamp("claimed_at"),text("last_error_code","队列级错误区别于逐设备错误与阶段错误"),stamp("created_at",True),stamp("updated_at",True),
],["UNIQUE(notification_id)",fk("notification_id","notifications",delete="CASCADE"),"CHECK(updated_at>=created_at)"],
   ["CREATE INDEX ix_outbox_due ON notification_outbox(status,next_attempt_at);"],
   "这是可领取、重试和恢复的任务，和用户看到的消息不同；保留独立表有明确运行职责。移除重复event_key及payload：幂等键与正文归notifications。只统一数据真值，不为了减少表数混合生命周期。",
   "保留并重命名mobile_notification_outbox；删除重复内容列")

T("notification_recipients","通知展示名单与实际排队用户快照",[
    col("id","INTEGER","PRIMARY KEY","关系行没有公开ID需求；用SQLite内部整数键，修复旧表无PK问题"),
    col("notification_id","TEXT","NOT NULL","所属逻辑消息"),col("user_id","TEXT","","用户收件人；和role_id互斥"),col("role_id","TEXT","","旧role可见名单；和user_id互斥"),
    boolean("feed_visible","该成员是否属于Mobile通知展示名单",default=1),
    boolean("queued_target","用户是否属于实际outbox目标快照；role行不能冒充已物化用户快照",default=0),
],[fk("notification_id","notifications",delete="CASCADE"),fk("user_id","users",delete="CASCADE"),fk("role_id","roles",delete="CASCADE"),
   "CHECK((user_id IS NOT NULL AND role_id IS NULL) OR (user_id IS NULL AND role_id IS NOT NULL))",
   "CHECK(feed_visible=1 OR queued_target=1)","CHECK(role_id IS NULL OR queued_target=0)"],
   ["CREATE UNIQUE INDEX ux_notification_recipient_user ON notification_recipients(notification_id,user_id) WHERE user_id IS NOT NULL;",
    "CREATE UNIQUE INDEX ux_notification_recipient_role ON notification_recipients(notification_id,role_id) WHERE role_id IS NOT NULL;",
    "CREATE INDEX ix_notification_recipient_user ON notification_recipients(user_id,feed_visible);","CREATE INDEX ix_notification_recipient_role ON notification_recipients(role_id,feed_visible);"],
   "旧队列与feed使用同一用户名单，却保存两套关系。合并为一行两个有意义标记，可保留两名单不同时的事实；live FK仍明确。原UNIQUE(notification_id,role_id,user_id)含NULL不能可靠去重，改为两个partial UNIQUE。",
   "统一notification_recipients+mobile_notification_outbox_recipients")

T("notification_deliveries","每通知每设备的最新实际发送结果",[
    ident("原notification_deliveries.id继续保留；仅旧outbox有行时创建明确映射的新ID"),
    col("notification_id","TEXT","NOT NULL","所属消息；原outbox_id可通过outbox.notification_id唯一关系回查"),col("device_id","TEXT","NOT NULL","目标设备身份，历史设备只停用而不硬删除"),
    col("recipient_user_id","TEXT","","新发送捕获当时用户；旧表未保存时NULL，不能猜成设备当前owner"),
    enum("status",("queued","sent","failed","invalid_device"),"完整原delivery状态，和队列聚合状态分开"),
    text("provider_message_id","FCM返回的不透明ID"),number("attempt_count","INTEGER",0,None,"设备实际尝试计数，不与队列领取次数混用、不把两镜像次数相加",True,0),
    stamp("last_attempt_at"),text("error_code","该设备的错误，而非共享消息错误"),stamp("updated_at",reason="新结果及原outbox镜像有实际更新时间；仅旧feed有的65条未保存此字段，保持NULL，不能冒充last_attempt_at；新写入由服务必填"),
],["UNIQUE(notification_id,device_id)",fk("notification_id","notifications",delete="CASCADE"),fk("device_id","devices"),fk("recipient_user_id","users"),
   "CHECK(status NOT IN ('sent','failed','invalid_device') OR last_attempt_at IS NOT NULL)"],
   ["CREATE INDEX ix_delivery_device ON notification_deliveries(device_id);","CREATE INDEX ix_delivery_recipient ON notification_deliveries(recipient_user_id) WHERE recipient_user_id IS NOT NULL;"],
   "当前worker同一事务双写两份结果，115份镜像逐字段一致。统一一条消息/设备事实；不采用会因设备换用户而失效的永久(device_id,user_id)复合FK。新插入在服务事务校验当时owner，历史NULL诚实表达未知。",
   "统一notification_deliveries+mobile_notification_outbox_deliveries")

T("notification_reads","每用户每消息的首次已读关系",[
    col("notification_id","TEXT","NOT NULL","读取的消息"),col("user_id","TEXT","NOT NULL","读取者"),stamp("read_at",True),
],["PRIMARY KEY(notification_id,user_id)",fk("notification_id","notifications",delete="CASCADE"),fk("user_id","users",delete="CASCADE")],
   ["CREATE INDEX ix_notification_reads_user ON notification_reads(user_id,read_at);"],
   "通知读取与警报浏览是不同用户行为，不合并到共享read字段，也不混进不可删的行政审计。mark-all-read仍逐用户执行。")

T("alert_notification_audience","每警报每用户的初次通知资格及已送达事实",[
    col("alert_id","TEXT","NOT NULL","警报身份"),col("user_id","TEXT","NOT NULL","该警报累计具备初次通知资格的用户"),
    document("qualification_reasons_json","当时ordinary_threshold/borderline_review/legacy等资格来源；逐元素验证字符串","array",True,"[]"),
    col("first_notification_id","TEXT","","最近一次初次尝试的消息；未有消息或旧消息删除时可空"),stamp("first_queued_at",True),stamp("first_sent_at"),
],["PRIMARY KEY(alert_id,user_id)",fk("alert_id","alerts",delete="CASCADE"),fk("user_id","users",delete="CASCADE"),
   fk("first_notification_id","notifications",delete="SET NULL"),"CHECK(first_sent_at IS NULL OR first_sent_at>=first_queued_at)"],
   ["CREATE INDEX ix_alert_audience_user ON alert_notification_audience(user_id,alert_id);","CREATE INDEX ix_alert_audience_notification ON alert_notification_audience(first_notification_id) WHERE first_notification_id IS NOT NULL;"],
   "此关系跨越多条通知，负责初次推送去重与阶段1真实audience。不能与单条消息recipient混为同一事实；资格缓存不是浏览授权。")

T("notification_phases","事件Phase A/B的计时快照及旧计划归档",[
    ident(),col("alert_id","TEXT","NOT NULL","所属事件"),enum("phase_kind",("awaiting_decision","awaiting_handler"),"当前两种计时阶段"),
    enum("phase_status",("active","cancelled","completed","archived"),"前三种为当前流程，archived只用于不再运行的旧计划事实"),
    stamp("started_at",True),number("first_after_seconds","INTEGER",15,3600,"阶段建立时冻结的首次重复延迟",True),number("all_after_seconds","INTEGER",30,7200,"全体升级延迟必须更晚",True),
    stamp("first_due_at",True),stamp("all_due_at",True),document("broadcast_role_snapshot_json","建立阶段时的历史选择；live关系在notification_phase_roles","array",True,"[]"),
    col("initiated_by_user_id","TEXT","","Phase B发起人；自动Phase A可空"),col("source_notification_id","TEXT","","启动计时的消息；即使没有设备也可建立Phase B"),
    text("cancel_reason","取消/替代原因，可扩展"),stamp("completed_at"),boolean("is_legacy","允许诚实归档旧v10计划，不伪造人工取消",default=0),
],["UNIQUE(id,alert_id)",fk("alert_id","alerts",delete="CASCADE"),fk("initiated_by_user_id","users"),fk("source_notification_id","notifications"),
   "CHECK(all_after_seconds>first_after_seconds)","CHECK(first_due_at<all_due_at AND first_due_at>started_at)",
   "CHECK(completed_at IS NULL OR completed_at>=started_at)","CHECK(phase_status<>'active' OR completed_at IS NULL)","CHECK(phase_status<>'archived' OR is_legacy=1)"],
   ["CREATE UNIQUE INDEX ux_one_active_phase ON notification_phases(alert_id) WHERE phase_status='active';","CREATE INDEX ix_phase_due ON notification_phases(phase_status,first_due_at,all_due_at);",
    "CREATE INDEX ix_phase_initiator ON notification_phases(initiated_by_user_id) WHERE initiated_by_user_id IS NOT NULL;","CREATE INDEX ix_phase_source_notice ON notification_phases(source_notification_id) WHERE source_notification_id IS NOT NULL;"],
   "计时从一个明确phase anchor开始，响应可取消；与消息/队列任务独立。同一事件可有多个历史phase，但最多一个active。old schedules也是计时快照，迁移后停止双写。精确微秒deadline计算由Python datetime执行，不用SQLite毫秒舍入函数代替。",
   "统一mobile_alert_reminder_phases+旧alert_notification_escalation_schedules")

T("notification_phase_roles","Phase B当时选择的live Broadcast Roles",[
    col("phase_id","TEXT","NOT NULL","具体Phase B，不能只按alert覆盖所有历史阶段"),col("role_id","TEXT","NOT NULL","当前存在的角色FK；删除role后live选择消失，历史snapshot仍在"),
],["PRIMARY KEY(phase_id,role_id)",fk("phase_id","notification_phases",delete="CASCADE"),fk("role_id","roles",delete="CASCADE")],
   ["CREATE INDEX ix_phase_roles_role ON notification_phase_roles(role_id,phase_id);"],
   "这张新增表有真实职责：把当前用于即时收件资格的JSON角色ID列表改为可执行FK、可索引的M:N关系。选中时最多20角色由事务验证；以后角色删除不抹掉历史快照。",
   "新增一个明确关联实体，取代live JSON关系")

T("notification_phase_dispatches","一个phase-stage的领取、防重与完成结果",[
    col("phase_id","TEXT","NOT NULL","阶段"),number("stage","INTEGER",1,2,"联合主键成员，阶段1/2",True),
    enum("status",("dispatching","queued","sent","partial","failed","no_recipients","skipped_stopped","skipped_superseded","skipped_responded"),"完整当前/旧阶段执行状态"),
    col("notification_id","TEXT","","真正产生消息时才引用；claim/skipped/no-recipients允许无消息"),
    enum("recipient_count_unit",("user","device"),"明确旧代码排队统计用户、完成统计设备的不同单位；历史无法证明时NULL",required=False),
    number("recipient_count","INTEGER",0,None,"阶段收件数快照，须结合单位",True,0),number("sent_count","INTEGER",0,None,"设备成功数快照",True,0),number("failed_count","INTEGER",0,None,"设备失败数快照",True,0),
    text("error_code","阶段错误与outbox错误各自保存"),stamp("claimed_at",True),stamp("completed_at"),
],["PRIMARY KEY(phase_id,stage)","UNIQUE(notification_id)",fk("phase_id","notification_phases",delete="CASCADE"),fk("notification_id","notifications",delete="SET NULL"),
   "CHECK(completed_at IS NULL OR completed_at>=claimed_at)"],
   ["CREATE INDEX ix_phase_dispatch_status ON notification_phase_dispatches(status,claimed_at);"],
   "一条stage执行记录可能没有任何消息或设备，所以它不能被并入notifications而造假通知。claim事实是提醒worker的幂等保障；outbox任务处理的是已有消息的发送与重试，两者语义不同。",
   "统一mobile_alert_reminder_dispatches+旧alert_notification_escalation_dispatches")

T("notification_phase_audience","每阶段、每用户、每stage的发送防重与结果",[
    col("phase_id","TEXT","NOT NULL","phase已决定alert，不重复存alert_id"),col("user_id","TEXT","NOT NULL","用户身份"),number("stage","INTEGER",1,2,"阶段1/2",True),
    enum("status",("queued","sent","failed","skipped"),"当前用户阶段状态"),col("notification_id","TEXT","","主提醒或late-repeat消息；旧outbox唯一映射到消息，保留稳定关联"),stamp("updated_at",True),
],["PRIMARY KEY(phase_id,user_id,stage)",fk("phase_id","notification_phases",delete="CASCADE"),fk("user_id","users",delete="CASCADE"),fk("notification_id","notifications",delete="SET NULL")],
   ["CREATE INDEX ix_phase_audience_user ON notification_phase_audience(user_id,phase_id);","CREATE INDEX ix_phase_audience_notice ON notification_phase_audience(notification_id) WHERE notification_id IS NOT NULL;"],
   "late-repeat可在主stage完成后为新增资格账号创建另一条消息；逐用户stage防重仍独立。不是message recipient的重复：这里跨越该phase-stage的多个通知。删除冗余alert_id依赖，查旧API时JOIN phase。",
   "重命名mobile_alert_reminder_audience并去除冗余依赖")

T("mobile_settings","全局管理偏好、Borderline并发revision与模拟通话不变量",[
    col("id","TEXT","PRIMARY KEY NOT NULL CHECK(id='global')","真实单例设置，不伪装成模型版本资源"),
    boolean("borderline_alert_enabled","保留旧Mobile全局设置；与config.vlm_borderline_review_enabled Web master不同",default=1),
    number("borderline_revision","INTEGER",1,None,"角色/个人接收设置共用的乐观并发版本",True,1),
    col("emergency_call_simulation_only","INTEGER","NOT NULL DEFAULT 1 CHECK(emergency_call_simulation_only=1)","产品当前只模拟通话；不能通过数据库值0启用真实拨号"),
    col("updated_by_user_id","TEXT","","后台初始化可空，否则记录修改者"),stamp("updated_at",True),
],[fk("updated_by_user_id","users")],[],
   "全局偏好与角色/用户配置的统一revision属于同一单例。合并borderline_settings_revision；旧角色集合转roles.compat_borderline_selected，历史集合留审计。系统运行策略版本仍在config_versions。",
   "合并mobile_settings+borderline_settings_revision")

T("audit_logs","行政、安全与配置操作的不可变审计事实",[
    ident(),col("user_id","TEXT","","系统操作或已删除actor允许NULL；敏感内容不得写入detail"),
    text("action","行政操作名可扩展，不把未来所有操作封死成少量枚举",True),text("entity_type","操作目标的历史类别，非动态授权或可执行外键",True),
    text("entity_id","历史目标标识，可指已删除角色；故意不是live FK"),
    document("detail_json","当时操作及迁移例外的冻结JSON；原始合法JSON类型保留，运行审计由服务生成对象",shape=None),stamp("created_at",True),
],[fk("user_id","users",delete="SET NULL")],
   ["CREATE INDEX ix_audit_actor_time ON audit_logs(user_id,created_at DESC);","CREATE INDEX ix_audit_subject_time ON audit_logs(entity_type,entity_id,created_at DESC);"],
   "审计与事件人工响应、首次已读、schema账表有不同查询/保留职责，保留独立。删除role不能让过去管理员操作消失；entity_id是历史snapshot，不能谎称它受FK保障。")

T("schema_migrations","数据库结构升级的持久账表",[
    number("version","INTEGER",1,None,"结构版本主键，与业务配置版本完全不同",True),text("name","升级名称，仅version用作身份，不要求名称永远不重复",True),stamp("applied_at",True),
],["PRIMARY KEY(version)"],[],
   "基础设施元数据有明确长期职责。保留当前migration runner识别的账表，不为了少一张而混入用户活动。schema版本只以本表为真值；DDL样稿不插入假装已执行的升级行。")

# Whole-second delays preserve the six microsecond digits. Parse only the
# fraction-free portion to avoid SQLite's millisecond date-function rounding.
phase=next(t for t in TABLES if t["name"]=="notification_phases")
for due,delay in (("first_due_at","first_after_seconds"),("all_due_at","all_after_seconds")):
    phase["rules"].append(f"CHECK(COALESCE(CAST(strftime('%s',substr({due},1,19)||'Z') AS INTEGER)=CAST(strftime('%s',substr(started_at,1,19)||'Z') AS INTEGER)+{delay},0) AND substr({due},21,6)=substr(started_at,21,6))")


def reject(condition,message):
    return f"SELECT CASE WHEN {condition} THEN RAISE(ABORT,'{message}') END;"


def pair(name,target,statements):
    body="\n    ".join(statements)
    for op in ("INSERT","UPDATE"):
        TRIGGERS.append(f"CREATE TRIGGER {name}_{op.lower()} BEFORE {op} ON {target}\nBEGIN\n    {body}\nEND;")


def typed_ref(field,target,condition):
    return reject(f"NEW.{field} IS NOT NULL AND NOT EXISTS(SELECT 1 FROM {target} r WHERE r.id=NEW.{field} AND {condition})",field+": wrong reference task/context")


pair("config_prompt_tasks","config_versions",[
    typed_ref("binary_prompt_version_id","prompt_versions","r.task='borderline_binary'"),
    typed_ref("scene_prompt_version_id","prompt_versions","r.task='scene_description'"),
])
pair("alert_model_prompt_phase","alerts",[
    typed_ref("opening_model_version_id","model_versions","(r.task=NEW.incident_type OR (NEW.incident_type='physical_contact_bullying' AND r.task='violence'))"),
    typed_ref("binary_prompt_version_id","prompt_versions","r.task='borderline_binary'"),
    typed_ref("active_phase_id","notification_phases","r.alert_id=NEW.id AND r.phase_status='active' AND ((r.phase_kind='awaiting_decision' AND NEW.workflow_status='awaiting_confirmation') OR (r.phase_kind='awaiting_handler' AND NEW.workflow_status='confirmed_awaiting_handler'))"),
])
pair("detection_model_task","detection_events",[
    typed_ref("model_version_id","model_versions","EXISTS(SELECT 1 FROM alerts a WHERE a.id=NEW.alert_id AND (r.task=a.incident_type OR (a.incident_type='physical_contact_bullying' AND r.task='violence')))"),
])
pair("review_references","borderline_reviews",[
    typed_ref("model_version_id","model_versions","r.task='borderline_binary'"),typed_ref("prompt_version_id","prompt_versions","r.task='borderline_binary'"),
    reject("NEW.detection_event_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM detection_events e JOIN alerts a ON a.id=e.alert_id WHERE e.id=NEW.detection_event_id AND e.alert_id=NEW.alert_id AND a.camera_id=NEW.camera_id)","review event must belong to its alert/camera"),
])
pair("description_version_tasks","vlm_descriptions",[
    typed_ref("model_version_id","model_versions","r.task='scene_description'"),typed_ref("prompt_version_id","prompt_versions","r.task='scene_description'"),
])
pair("source_file_kind","camera_sources",[
    typed_ref("media_file_id","media_files","r.media_kind='camera_source'"),
])
pair("evidence_file_kind","evidence_records",[
    typed_ref("media_file_id","media_files","r.media_kind=CASE WHEN NEW.evidence_type='weapon_screenshot' THEN 'evidence_image' ELSE 'evidence_clip' END"),
])
pair("phase_source_alert","notification_phases",[
    typed_ref("source_notification_id","notifications","r.alert_id=NEW.alert_id"),
])
pair("phase_role_kind","notification_phase_roles",[
    reject("NOT EXISTS(SELECT 1 FROM notification_phases p WHERE p.id=NEW.phase_id AND p.phase_kind='awaiting_handler')","live broadcast role belongs to Phase B"),
])
pair("phase_dispatch_notice","notification_phase_dispatches",[
    typed_ref("notification_id","notifications","r.message_kind='phase_reminder' AND r.phase_id=NEW.phase_id AND r.reminder_stage=NEW.stage"),
])
pair("phase_user_notice","notification_phase_audience",[
    typed_ref("notification_id","notifications","r.message_kind IN ('phase_reminder','late_repeat') AND r.phase_id=NEW.phase_id AND r.reminder_stage=NEW.stage"),
])
pair("alert_audience_notice","alert_notification_audience",[
    typed_ref("first_notification_id","notifications","r.alert_id=NEW.alert_id"),
])
pair("outbox_has_stable_event","notification_outbox",[
    reject("NOT EXISTS(SELECT 1 FROM notifications n WHERE n.id=NEW.notification_id AND n.event_key IS NOT NULL)","queued notification requires stable event key"),
])

pair("config_weapon_profiles","config_versions",[
    reject("NOT json_valid(NEW.weapon_class_thresholds_json)","malformed threshold JSON"),
    reject("(SELECT count(*) FROM json_each(NEW.weapon_class_thresholds_json))<>3","three sensitivity profiles required"),
    reject("EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p WHERE p.key NOT IN ('sensitive','moderate','certain') OR p.type<>'object')","invalid threshold profile"),
    reject("EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p WHERE NOT EXISTS(SELECT 1 FROM json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key='knife') OR NOT EXISTS(SELECT 1 FROM json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key IN ('gun','long_gun')))","knife and gun/legacy long_gun thresholds required"),
    reject("EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p JOIN json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.type NOT IN ('integer','real') OR c.value<0 OR c.value>1)","threshold values must be probabilities"),
    reject("NEW.validation_profile='runtime' AND EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_thresholds_json) p JOIN json_each(CASE WHEN p.type='object' THEN p.value ELSE '{}' END) c WHERE c.key NOT IN ('knife','gun') OR abs(c.value*10000-round(c.value*10000))>0.00000001)","runtime classes/precision must follow current API"),
])
pair("detection_class_scores","detection_events",[
    reject("NOT json_valid(NEW.weapon_class_scores_json)","malformed score JSON"),
    reject("EXISTS(SELECT 1 FROM json_each(NEW.weapon_class_scores_json) WHERE type NOT IN ('integer','real') OR value<0 OR value>1)","scores must be numeric probabilities"),
])
for table,field in (("alerts","broadcast_role_snapshot_json"),("alert_actions","broadcast_role_snapshot_json"),
                    ("notification_phases","broadcast_role_snapshot_json"),("alert_notification_audience","qualification_reasons_json")):
    pair("strings_"+table,table,[
        reject(f"NEW.{field} IS NOT NULL AND NOT json_valid(NEW.{field})","malformed snapshot array"),
        reject(f"NEW.{field} IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.{field}) WHERE type<>'text')","snapshot entries must be strings"),
    ])
TRIGGERS.append("""CREATE TRIGGER native_confirm_roles BEFORE INSERT ON alert_actions
WHEN NEW.record_origin='workflow' AND NEW.action='confirm_only'
BEGIN SELECT CASE WHEN json_array_length(NEW.broadcast_role_snapshot_json) NOT BETWEEN 1 AND 20
THEN RAISE(ABORT,'confirm-only selects 1..20 roles') END; END;""")

vlm=[]
for n in ("model_id","quantization","binary_prompt_version","scene_prompt_version"):
    vlm.append(f"COALESCE(json_type(NEW.vlm_settings_json,'$.{n}')='text' AND length(trim(json_extract(NEW.vlm_settings_json,'$.{n}')))>0,0)")
for n,lo,hi,integer in (("input_fps",.25,12,False),("video_token_budget",256,16384,True),("max_output_tokens",32,2048,True),("scene_description_frames",4,128,True)):
    types="('integer')" if integer else "('integer','real')"
    vlm.append(f"COALESCE(json_type(NEW.vlm_settings_json,'$.{n}') IN {types} AND json_extract(NEW.vlm_settings_json,'$.{n}') BETWEEN {lo} AND {hi},0)")
vlm.append("COALESCE(json_extract(NEW.vlm_settings_json,'$.scene_description_frames')%2=0,0)")
for n in ("borderline_review_enabled","scene_description_enabled"):
    vlm.append(f"COALESCE(json_type(NEW.vlm_settings_json,'$.{n}') IN ('true','false'),0)")
pair("review_vlm_snapshot","borderline_reviews",[
    reject("NEW.vlm_settings_json IS NOT NULL AND NOT json_valid(NEW.vlm_settings_json)","malformed VLM snapshot"),
    reject("NEW.record_origin='task' AND NOT ("+" AND ".join(vlm)+")","VLM snapshot field schema failed"),
])
summary=["COALESCE(json_type(NEW.structured_json)='object',0)",
    "COALESCE(json_type(NEW.structured_json,'$.description')='text' AND length(json_extract(NEW.structured_json,'$.description')) BETWEEN 1 AND 120,0)",
    "COALESCE(json_type(NEW.structured_json,'$.visible_evidence')='array' AND json_array_length(NEW.structured_json,'$.visible_evidence')<=2,0)",
    "COALESCE(json_extract(NEW.structured_json,'$.uncertainty') IN ('low','medium','high'),0)",
    "NOT EXISTS(SELECT 1 FROM json_each(NEW.structured_json,'$.visible_evidence') WHERE type<>'text')"]
pair("description_summary_schema","vlm_descriptions",[
    reject("NEW.structured_json IS NOT NULL AND NOT json_valid(NEW.structured_json)","malformed scene JSON"),
    reject("NEW.output_format='summary' AND NOT ("+" AND ".join(summary)+")","SceneSummary schema failed"),
])

# Only genuine version/evidence history is append-only. Mutable operational
# states retain UPDATE; never use one append-only policy for reads and jobs.
for target in ("config_versions","model_versions","prompt_versions","detection_events","alert_actions","schema_migrations"):
    TRIGGERS.append(f"CREATE TRIGGER immutable_{target} BEFORE UPDATE ON {target} BEGIN SELECT RAISE(ABORT,'{target}: append-only fact/version'); END;")
for target in ("config_versions","model_versions","prompt_versions","alert_actions","schema_migrations"):
    TRIGGERS.append(f"CREATE TRIGGER retain_{target} BEFORE DELETE ON {target} BEGIN SELECT RAISE(ABORT,'{target}: retain version/history'); END;")
audit=next(t for t in TABLES if t["name"]=="audit_logs")
audit_guard=[f"NEW.{n} IS NOT OLD.{n}" for n,_,_,_ in audit["columns"] if n!="user_id"]
audit_guard.append("(NEW.user_id IS NOT OLD.user_id AND NEW.user_id IS NOT NULL)")
TRIGGERS.append("CREATE TRIGGER immutable_audit BEFORE UPDATE ON audit_logs WHEN "+" OR ".join(audit_guard)+" BEGIN SELECT RAISE(ABORT,'audit is append-only except actor FK deletion cleanup'); END;")
TRIGGERS.append("CREATE TRIGGER retain_audit BEFORE DELETE ON audit_logs BEGIN SELECT RAISE(ABORT,'retain administrative audit'); END;")
for t in TABLES:
    if t["name"] in ("config_versions","model_versions","prompt_versions","detection_events","alert_actions","audit_logs","schema_migrations"):
        continue
    if any(x[0]=="id" for x in t["columns"]):
        TRIGGERS.append(f"CREATE TRIGGER immutable_{t['name']}_id BEFORE UPDATE OF id ON {t['name']} WHEN NEW.id IS NOT OLD.id BEGIN SELECT RAISE(ABORT,'identity is immutable'); END;")
TRIGGERS.append("""CREATE TRIGGER clear_stopped_phase AFTER UPDATE OF phase_status ON notification_phases
WHEN NEW.phase_status<>'active'
BEGIN UPDATE alerts SET active_phase_id=NULL WHERE active_phase_id=NEW.id; END;""")
TRIGGERS.append("""CREATE TRIGGER keep_notification_identity BEFORE UPDATE ON notifications
WHEN NEW.alert_id IS NOT OLD.alert_id OR NEW.event_key IS NOT OLD.event_key OR NEW.phase_id IS NOT OLD.phase_id OR NEW.reminder_stage IS NOT OLD.reminder_stage OR NEW.message_kind IS NOT OLD.message_kind
BEGIN SELECT RAISE(ABORT,'notification identity/context is immutable'); END;""")
TRIGGERS.append("""CREATE TRIGGER freeze_dispatched_content BEFORE UPDATE OF title,body,data_payload_json ON notifications
WHEN (NEW.title IS NOT OLD.title OR NEW.body IS NOT OLD.body OR NEW.data_payload_json IS NOT OLD.data_payload_json)
AND (EXISTS(SELECT 1 FROM notification_outbox o WHERE o.notification_id=OLD.id AND o.attempt_count>0)
     OR EXISTS(SELECT 1 FROM notification_deliveries d WHERE d.notification_id=OLD.id AND d.attempt_count>0))
BEGIN SELECT RAISE(ABORT,'content is frozen after dispatch begins'); END;""")
for target,fields in (
    ("notification_outbox",["notification_id","created_at"]),
    ("notification_deliveries",["notification_id","device_id","recipient_user_id"]),
    ("borderline_reviews",["record_origin","camera_id","alert_id","detection_event_id","model_version_id","prompt_version_id","config_version_id","detector_confidence","source_start_seconds","source_end_seconds","context_path","vlm_settings_json"]),
    ("notification_phases",["alert_id","phase_kind","started_at","first_after_seconds","all_after_seconds","first_due_at","all_due_at"]),
):
    TRIGGERS.append(f"CREATE TRIGGER keep_{target}_context BEFORE UPDATE ON {target} WHEN "+" OR ".join(f"NEW.{n} IS NOT OLD.{n}" for n in fields)+" BEGIN SELECT RAISE(ABORT,'context snapshot is immutable'); END;")

STATUS_MAP={"awaiting_confirmation":"open","confirmed_awaiting_handler":"in_review","in_progress":"in_review",
    "legacy_in_review":"in_review","resolved":"resolved","dismissed":"dismissed","legacy_escalated":"escalated"}
MAPPING={
    "users":"users", "roles":"roles", "user_roles":"user_roles", "devices":"devices", "locations":"locations", "cameras":"cameras", "media_files":"media_files", "camera_sources":"camera_sources",
    "model_versions":"model_versions", "prompt_versions":"prompt_versions",
    "config_versions":"config_versions", "violence_configs":"config_versions.violence_*", "weapon_configs":"config_versions.weapon_*", "vlm_configs":"config_versions.vlm_*及prompt FK", "alert_notification_escalation_configs":"config_versions两阶段延迟",
    "alerts":"alerts；status由workflow_status固定映射派生", "mobile_alert_workflows":"alerts的当前流程/处理人/phase/revision字段",
    "detection_events":"detection_events", "borderline_review_tasks":"borderline_reviews [task]", "borderline_reviews":"borderline_reviews [audit_only]；能证明相同的镜像合并并保留legacy_review_id",
    "evidence_records":"evidence_records", "vlm_descriptions":"vlm_descriptions",
    "alert_actions":"alert_actions [legacy]", "mobile_alert_workflow_events":"alert_actions [workflow]；审计事实留audit_logs",
    "alert_reads":"alert_reads", "notifications":"notifications", "notification_recipients":"notification_recipients.feed_visible",
    "notification_reads":"notification_reads", "notification_deliveries":"notification_deliveries",
    "mobile_notification_outbox":"notification_outbox；event_key移notifications，payload统一读取notifications",
    "mobile_notification_outbox_recipients":"notification_recipients.queued_target",
    "mobile_notification_outbox_deliveries":"notification_deliveries；以(notification_id,device_id)单一事实，不加总次数",
    "alert_notification_audience":"alert_notification_audience",
    "mobile_alert_reminder_phases":"notification_phases；live JSON角色选择展开到notification_phase_roles",
    "alert_notification_escalation_schedules":"notification_phases的legacy归档或可证明相同phase",
    "mobile_alert_reminder_dispatches":"notification_phase_dispatches",
    "alert_notification_escalation_dispatches":"notification_phase_dispatches的历史对应phase-stage",
    "mobile_alert_reminder_audience":"notification_phase_audience；alert_id通过phase推导，outbox_id唯一映射notification_id",
    "borderline_role_settings":"roles.borderline_*", "borderline_user_settings":"users.borderline_*",
    "mobile_settings":"mobile_settings；角色集合改为roles.compat_borderline_selected",
    "borderline_settings_revision":"mobile_settings.borderline_revision", "audit_logs":"audit_logs", "schema_migrations":"schema_migrations",
}

COUNT_LEDGER=[
    ("基线",44,"原schema v14"), ("五张配置表合并",-4,"相同版本和保存生命周期"),
    ("用户/角色偏好并入主实体",-2,"两个一对一属性组"), ("revision并入全局设置",-1,"同一全局设置聚合"),
    ("当前workflow并入alert",-1,"每事件唯一当前状态"), ("统一复核请求/结论镜像",-1,"同一review生命周期"),
    ("统一旧/新人工响应历史",-1,"同一事件动作事实"), ("旧计划/dispatch统一当前phase模型",-2,"保留全部旧事实，结束双写"),
    ("统一用户目标名单",-1,"同一message-user关系的两个标记"), ("统一设备结果镜像",-1,"同一message-device结果"),
    ("新增live phase-role关联",1,"替代可执行JSON关联，提高FK保障"),
]


def generate():
    assert len(TABLES)==31 and len(MAPPING)==44
    assert sum(x[1] for x in COUNT_LEDGER)==31
    sql=["-- PLANNING ARTIFACT: create in a NEW EMPTY database only. NOT a migration.","-- Recommended targeted refactor: 30 business/operational tables + schema_migrations.",
         "-- SQLite >=3.37 with JSON built-in. Schema ledger is populated only by real migration execution.","PRAGMA foreign_keys=ON;","BEGIN;"]
    for t in TABLES:
        defs=[f"    {n} {typ} {s}".rstrip() for n,typ,s,_ in t["columns"]]
        defs += ["    "+s for s in t["rules"]]
        sql += ["-- "+t["purpose"],"CREATE TABLE "+t["name"]+" (\n"+",\n".join(defs)+"\n) STRICT;"]
    for t in TABLES:sql += t["indexes"]
    sql += TRIGGERS + ["COMMIT;"]
    (ROOT/"schema_proposal.sql").write_text("\n\n".join(sql)+"\n",encoding="utf-8")
    docs=[INTRO,"\n## 实体职责与独立存在的依据\n","| # | Entity | 核心职责 | 保留/合并理由 |","|---:|---|---|---|"]
    docs += [f"| {i} | `{t['name']}` | {t['purpose']} | {t['why']} |" for i,t in enumerate(TABLES,1)]
    docs += ["\n## 表数量由哪些决策产生\n","| 决策 | 表数变化 | 依据 |","|---|---:|---|"]
    docs += [f"| {a} | {b:+d} | {r} |" for a,b,r in COUNT_LEDGER]
    docs += ["\n结果为31张实际表（30张业务/配置/运行表加1张schema账表）。没有以预定表数倒推实体。",CONVENTIONS,
             "\n## 逐属性数据字典\n","列约束为可执行DDL；下面另列完整表级PK/FK/CHECK及索引。默认值只用于有明确语义的初始化，不能用默认值掩盖未知历史信息。"]
    for i,t in enumerate(TABLES,1):
        docs += [f"\n### {i}. `{t['name']}`\n",t["purpose"],t["why"],"\n| Attribute | Type | 列级constraint | 理由与NULL含义 |","|---|---|---|---|"]
        docs += [f"| `{n}` | `{typ}` | `{s or 'NULL allowed'}` | {r} |" for n,typ,s,r in t["columns"]]
        docs += ["\n表级PK/FK/CHECK：","\n```sql",*t["rules"],"```","\n索引：","\n```sql",*(t["indexes"] or ["-- 只有PK/UNIQUE自动索引，无额外查询索引"]),"```"]
    docs += ["\n## 跨表与内容约束\n","只有跨行/跨表规则使用trigger；普通枚举、范围、NULL及唯一性优先原生约束。权限/状态转换/并发/外部系统约束仍由下面的事务契约负责。","\n```sql",*TRIGGERS,"```",
             "\n## 44张旧表逐一去向\n","| 原表 | 目标 |","|---|---|"]
    docs += [f"| `{a}` | {b} |" for a,b in sorted(MAPPING.items())]
    docs += [BEHAVIOR,TRANSACTIONS,MIGRATION,TRADEOFFS,SOURCES]
    (ROOT/"DESIGN.md").write_text("\n".join(docs)+"\n",encoding="utf-8")
    machine=dict(tables=TABLES,mapping=MAPPING,count_ledger=COUNT_LEDGER,status_map=STATUS_MAP)
    (ROOT/"design_spec.json").write_text(json.dumps(machine,ensure_ascii=False,indent=2),encoding="utf-8")
    # FK topology generated from columns and actual full UNIQUE/PK keys.
    # Partial uniqueness (one ACTIVE phase) is not a global 1:1 relation.
    edges=[]
    for t in TABLES:
        required={n for n,_,s,_ in t["columns"] if "NOT NULL" in s or "PRIMARY KEY" in s}
        unique=[]
        for n,_,s,_ in t["columns"]:
            if "PRIMARY KEY" in s or "UNIQUE" in s:unique.append({n})
        for rule in t["rules"]:
            m=re.match(r"(?:UNIQUE|PRIMARY KEY)\(([^)]+)\)",rule)
            if m:unique.append(set(x.strip() for x in m[1].split(',')))
        for rule in t["rules"]:
            m=re.search(r"FOREIGN KEY \(([^)]+)\) REFERENCES (\w+)\(([^)]+)\)",rule)
            if m:
                fields={x.strip() for x in m[1].split(',')}
                parent="||" if fields<=required else "o|"
                child="o|" if t["name"]=="mobile_settings" or any(k<=fields for k in unique) else "o{"
                edges.append(f"    {m[2]} {parent}--{child} {t['name']} : {m[1].replace(',','_')}")
    erd=["erDiagram"]
    for t in TABLES:
        keys=set()
        for n,_,s,_ in t["columns"]:
            if "PRIMARY KEY" in s:keys.add(n)
        foreign=set()
        for rule in t["rules"]:
            m=re.match(r"PRIMARY KEY\(([^)]+)\)",rule)
            if m:keys.update(x.strip() for x in m[1].split(','))
            m=re.match(r"FOREIGN KEY \(([^)]+)\)",rule)
            if m:foreign.update(x.strip() for x in m[1].split(','))
        erd.append("    "+t["name"]+" {")
        for n,typ,_,_ in t["columns"]:
            if n in keys or n in foreign:
                flags="PK, FK" if n in keys and n in foreign else "PK" if n in keys else "FK"
                erd.append(f"        {typ} {n} {flags}")
        erd.append("    }")
    erd += edges
    (ROOT/"erd_full.mmd").write_text("\n".join(erd)+"\n",encoding="utf-8")
    for name,body in MODULE_ERDS.items():(ROOT/f"erd_{name}.mmd").write_text(body+"\n",encoding="utf-8")
    links={"DESIGN.md":"完整设计及305个属性的约束理由", "ATTRIBUTE_MAPPING.md":"原44表、339个属性的迁移去向",
        "KEYS.md":"31表的PK/FK与删除规则速查", "API_COVERAGE.md":"44个HTTP路由及30项Mobile契约",
        "schema_proposal.sql":"可执行的新空库DDL样稿", "VALIDATION.md":"本次实际执行的验证与范围",
        "erd_full.mmd":"完整ERD源码", "erd_identity.mmd":"身份模块ERD", "erd_monitoring.mmd":"监控模块ERD", "erd_response.mmd":"通知与响应模块ERD"}
    navigation="\n## 可点击的阅读入口\n\n"+"\n".join(f"- [{label}]({(ROOT/name).as_posix()})" for name,label in links.items())
    (ROOT/"OVERVIEW.md").write_text(OVERVIEW+navigation+"\n",encoding="utf-8")
    keys=["# 31表PK/FK速查", "", "完整值域、NULL/默认值、CHECK、唯一索引和跨表trigger请配合DESIGN.md及SQL阅读。复合FK中某个成员为NULL时，SQLite不检查该组外键；任务/阶段等条件必填规则另有CHECK。", "",
        "| Entity | Primary key | Foreign key / ON DELETE |", "|---|---|---|"]
    for t in TABLES:
        pk=[n for n,_,s,_ in t['columns'] if "PRIMARY KEY" in s]
        for rule in t['rules']:
            m=re.match(r"PRIMARY KEY\(([^)]+)\)",rule)
            if m:pk += [x.strip() for x in m[1].split(',')]
        refs=[r.replace("FOREIGN KEY ","").replace(" REFERENCES "," → ").replace(" ON DELETE "," / ") for r in t['rules'] if r.startswith("FOREIGN KEY")]
        keys.append(f"| `{t['name']}` | `{', '.join(pk)}` | "+"<br>".join(f"`{r}`" for r in refs or ["无FK"])+" |")
    keys += ["", "特别关系：一个alert最多一个active phase是partial UNIQUE；同phase-stage只有一个主提醒消息，但允许late-repeat；notification_recipients两个可选FK要求XOR且分别partial UNIQUE；send-time recipient FK不与可转移设备owner组成永久复合FK。schema_migrations独立基础设施账表；实体图只显示PK/FK列，全部其他属性见字典。"]
    (ROOT/"KEYS.md").write_text("\n".join(keys)+"\n",encoding="utf-8")
    print(f"Generated 31 STRICT tables; {sum(len(t['columns']) for t in TABLES)} documented attributes; 44 source mappings.")


INTRO="""# GuardAId / Mobile App — 推荐的针对性数据库重构规划

日期：2026-10-13（Asia/Kuala_Lumpur）。基线为当前schema v14的44张SQLite表及当前Web/Mobile功能。

**推荐结果为31张实际实体表，其中30张业务/配置/运行表，1张schema_migrations基础设施账表。** 这个数量来自逐项职责判断，未预设必须少于15、25或30。目标是一个清晰、可约束、可迁移的系统模型。此方案替代“15实体硬上限”方案作为无ERD数量限制时的推荐方向。

本次只生成规划、完整逐属性字典、31表可执行DDL样稿、ERD及独立验证程序。运行数据库、FastAPI/worker和Flutter源码不作修改；新DDL不能直接替换现有SQLiteRepository运行文件。功能在设计层面逐项覆盖，实际迁移后的端到端兼容性尚须证明。

实体保留规则：独立身份/生命周期、真实1:N或M:N关系、独立状态及保留职责，至少满足一个且不能只是把DTO拆成表。合并规则：同一事实双写，或相同主键决定的一对一属性组，且不会丢独立历史。每个合并需要旧数据映射及事务行为说明。

不合并的关键边界：model/prompt/config各自版本；detector/review/description不同事实；alert_read/notification_read/人工动作/行政审计不同用途；通知内容/outbox任务/阶段执行不同生命周期。不会用一张宽大多态表统一六种通知关系。
"""

CONVENTIONS="""
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
"""

BEHAVIOR="""
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
"""

TRANSACTIONS="""
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
"""

MIGRATION="""
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
"""

TRADEOFFS="""
## 选择与未做的优化

- 不合并model/prompt/policy/admin为一个资源池，不合并detector/review/description为任务大表；实体目的更直接，正常FK和状态约束能表达多数规则。
- config变宽但所有字段属于同一版本，不出现与该行不适用的一堆子类型NULL；这是明确的一对一水平合并。如果以后组件独立版本保存，重新拆分需要新业务依据。
- notification_recipients仅用户/角色同一名单关系，两个标记区分feed/queue用途；audience、阶段用户状态、设备结果仍独立，不用六scope表。新phase_role虽然增加一表，但换得真实FK与独立关系语义。
- alert workflow当前状态合并、兼容status派生；动作仍历史表，避免把历史改成当前状态字段。跨系统时间/错误码没有凭印象固定长度。没有新建分析job/debug实体，因为现功能不要求DB持久化。
- 索引与执行效率需要真实数据、EXPLAIN QUERY PLAN及负载测量；31表不是性能证明。新方案相对原44更少重复，相对15少多态约束复杂度，但也没有数学证明全局最优。
- 未建立新的notification attempts表：目前系统只保存每设备最新结果与计数，不拥有逐次尝试历史功能。若未来确需完整attempt audit，再有理由新增该实体。不会预先为了“专业看起来齐全”新增临时用途表。
- 无固定工期/性能数字猜测。迁移策略与约束样稿可以检查；代码改写、端到端验收和上线授权另属实施工作。
"""

SOURCES="""
## 依据与附件

本地事实依据：guardaid_monitor/migrations/001_initial.sql..014_borderline_notification_eligibility.sql；DATABASE_DESIGN.md；app/domain.py、sqlite_repository.py、main.py、inference/orchestrator.py、notification_escalation.py、notification_outbox.py；mobileapp/lib/src/repository.dart与models.dart。基线数据库只读核对，未把邮箱/token写入文档。

原生SQLite规则见[类型](https://www.sqlite.org/datatype3.html)、[STRICT](https://www.sqlite.org/stricttables.html)、[CREATE TABLE](https://www.sqlite.org/lang_createtable.html)、[外键](https://www.sqlite.org/foreignkeys.html)、[局部索引](https://www.sqlite.org/partialindex.html)、[JSON](https://www.sqlite.org/json1.html)、[日期函数](https://www.sqlite.org/lang_datefunc.html)。Firebase UID范围见[Firebase Manage Users](https://firebase.google.com/docs/auth/admin/manage-users)。外部来源用于确认平台规则，实体职责以当前项目证据为主。

OVERVIEW.md为阅读入口；KEYS.md为31表PK/FK速查；ATTRIBUTE_MAPPING.md列原44表全部339属性去向；API_COVERAGE.md静态列44个HTTP路由及30项Mobile契约；schema_proposal.sql为新空库样稿；erd_full.mmd是完整FK图，erd_identity/monitoring/response.mmd是模块图；VALIDATION.md记录本次真正执行的验证。设计是规划，不能把附件当成运行库已改造。
"""

OVERVIEW="""# GuardAId 推荐重构规划：44 → 31 张表

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
"""

MODULE_ERDS={
    "identity":"""erDiagram
    users ||--o{ user_roles : user_id
    roles ||--o{ user_roles : role_id
    users o|--o{ user_roles : assigned_by
    users ||--o{ devices : user_id
    users o|--o{ users : preference_updated_by
    users o|--o{ roles : default_updated_by
    users o|--o| mobile_settings : updated_by
    users o|--o{ audit_logs : actor
""",
    "monitoring":"""erDiagram
    locations ||--o{ cameras : location_id
    cameras ||--o{ camera_sources : camera_id
    media_files o|--o{ camera_sources : uploaded_file
    cameras ||--o{ alerts : camera_id
    alerts ||--o{ detection_events : alert_id
    model_versions ||--o{ detection_events : detector_model
    config_versions ||--o{ detection_events : per_event_policy
    alerts o|--o{ borderline_reviews : alert_id
    detection_events o|--o| borderline_reviews : task_window
    model_versions ||--o{ borderline_reviews : review_model
    prompt_versions ||--o{ borderline_reviews : binary_prompt
    config_versions ||--o{ borderline_reviews : policy
    alerts ||--o{ evidence_records : alert_id
    media_files ||--o| evidence_records : unique_evidence_file
    alerts ||--o{ vlm_descriptions : alert_id
    model_versions ||--o{ vlm_descriptions : description_model
    prompt_versions o|--o{ vlm_descriptions : optional_historical_prompt
    config_versions o|--o{ vlm_descriptions : optional_historical_policy
    prompt_versions ||--o{ config_versions : binary_and_scene_prompt
""",
    "response":"""erDiagram
    alerts ||--o{ alert_actions : human_response
    users ||--o{ alert_actions : actor
    alerts ||--o{ alert_reads : first_view
    users ||--o{ alert_reads : viewer
    alerts ||--o{ notifications : messages
    notifications ||--o| notification_outbox : durable_push_job
    notifications ||--o{ notification_recipients : feed_and_queued_targets
    users o|--o{ notification_recipients : user
    roles o|--o{ notification_recipients : role
    notifications ||--o{ notification_deliveries : delivery_result
    devices ||--o{ notification_deliveries : target_device
    notifications ||--o{ notification_reads : first_read
    users ||--o{ notification_reads : reader
    alerts ||--o{ alert_notification_audience : initial_user_qualification
    users ||--o{ alert_notification_audience : user
    alerts ||--o{ notification_phases : timer_history
    notification_phases o|--o| alerts : current_active_phase
    notification_phases ||--o{ notification_phase_roles : selected_role
    roles ||--o{ notification_phase_roles : role
    notification_phases ||--o{ notification_phase_dispatches : stage_claim
    notifications o|--o| notification_phase_dispatches : optional_actual_message
    notification_phases ||--o{ notification_phase_audience : user_stage_guard
    users ||--o{ notification_phase_audience : user
    notifications o|--o{ notification_phase_audience : main_or_late_repeat
""",
}


if __name__=="__main__":generate()



