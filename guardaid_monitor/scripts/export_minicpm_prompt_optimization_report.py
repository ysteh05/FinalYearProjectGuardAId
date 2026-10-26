"""Export the current offline MiniCPM prompt-optimization audit report."""
from __future__ import annotations

import json
import math
import statistics
import sys
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MONITOR = ROOT / "guardaid_monitor"
sys.path.insert(0, str(MONITOR))

from app.vlm_contract import SCENE_PROMPT_VERSION, SCENE_SCHEMA, scene_prompt  # noqa: E402
from scripts import minicpm_scene_prompt_optimization as evaluation  # noqa: E402


REPORT = ROOT / "VLM Fine-tune" / "Optimization Execution Report.md"


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def read_runs() -> list[dict]:
    reports = []
    for folder in sorted(evaluation.RUN_ROOT.iterdir()) if evaluation.RUN_ROOT.exists() else []:
        metadata_path = folder / "run.json"
        if not folder.is_dir() or not metadata_path.is_file():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        rows_path = folder / "results.jsonl"
        rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line.strip()] if rows_path.exists() else []
        config = metadata.get("config", {})
        prompt_ids = list(config.get("prompts", {}))
        for prompt_id in prompt_ids:
            selected = [row for row in rows if row.get("prompt_id") == prompt_id]
            times = [row["total_wall_seconds"] for row in selected if isinstance(row.get("total_wall_seconds"), (int, float))]
            strict_valid = 0
            strict_first = 0
            for row in selected:
                attempts = row.get("attempts", [])
                first_raw = attempts[0].get("raw_response") if attempts else None
                if first_raw:
                    try:
                        evaluation.validate_scene_output(first_raw)
                        strict_first += 1
                    except (ValueError, TypeError):
                        pass
                final_raw = next((item.get("raw_response") for item in reversed(attempts) if item.get("raw_response")), None)
                if final_raw:
                    try:
                        evaluation.validate_scene_output(final_raw)
                        strict_valid += 1
                    except (ValueError, TypeError):
                        pass
            reports.append({
                "name": folder.name,
                "status": metadata.get("status"),
                "prompt": prompt_id,
                "profile": (config.get("prepared", {}).get("profiles") or {}),
                "case_count": config.get("prepared", {}).get("case_count"),
                "rows": len(selected),
                "strict_valid": strict_valid,
                "strict_first": strict_first,
                "mean": statistics.mean(times) if times else None,
                "p95": p95(times),
                "failure": metadata.get("failure"),
                "result_rows": selected,
            })
        if not prompt_ids:
            reports.append({"name": folder.name, "status": metadata.get("status"),
                            "prompt": "-", "profile": {}, "case_count": None,
                            "rows": len(rows), "strict_valid": 0, "strict_first": 0,
                            "mean": None, "p95": None, "failure": metadata.get("failure"),
                            "result_rows": rows})
    return reports


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}s"


def code_block(text: str) -> str:
    return f"```text\n{text.rstrip()}\n```"


def candidate_v5_expanded(kind: str, classes: list[str]) -> str:
    _, user, _ = evaluation.prompts_for("candidate-v5", kind, classes)
    return user


def selected_raw_response(runs: list[dict], case_id: str, prompt_id: str,
                          detected_classes: tuple[str, ...] | None = None) -> str | None:
    """Return the first completed repetition's final response for a diagnostic example."""
    for run in reversed(runs):
        if run.get("prompt") != prompt_id:
            continue
        for row in run.get("result_rows", []):
            if row.get("case_id") != case_id or row.get("repeat") != 1:
                continue
            classes = tuple(row.get("detected_classes") or ())
            if detected_classes is not None and classes != detected_classes:
                continue
            for attempt in reversed(row.get("attempts", [])):
                raw = attempt.get("raw_response")
                if raw:
                    return raw
    return None


def present_response(raw: str | None) -> str:
    if not raw:
        return "（本轮没有保存该提示词的输出。）"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return code_block(raw)
    details = value.get("details") if isinstance(value, dict) else None
    summary = value.get("summary") if isinstance(value, dict) else None
    uncertainty = value.get("uncertainty") if isinstance(value, dict) else None
    if not isinstance(summary, str) or not isinstance(details, list):
        return code_block(raw)
    lines = [f"**Summary:** {summary}", "", "**Details:**"]
    lines.extend(f"- {point}" for point in details if isinstance(point, str))
    lines.extend(["", f"**Uncertainty:** {uncertainty}"])
    return "\n".join(lines)


def main() -> None:
    cases = evaluation.load_cases("all")
    references = json.loads((evaluation.CASES_ROOT / "visual_reference_notes.json").read_text(encoding="utf-8"))
    runs = read_runs()
    report = [
        "# MiniCPM V4.7 1B Scene Description 提示词优化执行报告",
        "",
        "> **结论：本轮未达到验收标准，未选出可部署的优化版提示词。生产环境继续使用 `minicpm_alert_context_v9`；本次没有修改 Web Dashboard、数据库或生产提示词。**",
        "",
        "## 执行范围与环境",
        "",
        "- 模型：`openbmb/minicpm-v4.7:1b-q4_K_M`，Ollama 0.40.5，摘要：`00e691b8ed678b69976caf078b953ea79eaea650990fad3f51ba7b3f6a609777`。",
        "- 推理：`think=false`、temperature 0、seed 20261017、context 32768、最大生成 512 tokens；采用 Ollama JSON Schema 和最多一次格式修正。已检查的响应未出现非空 thinking。",
        "- 运行时检查：Ollama `llama.cpp` runner；测试机为 NVIDIA GeForce RTX 3070 Laptop GPU，8 GiB；模型约 1.11 GB 显存。没有停止 Ollama 或监控服务。",
        f"- 案例：{len(cases)} 个来源案例，调整集 {sum(c['split']=='Adjustment Set' for c in cases)} 个、验收集 {sum(c['split']=='Acceptance Set' for c in cases)} 个，覆盖八类。视觉参考记录 {len(references['cases'])} 条，明确不发送给模型。",
        "- 视觉参考修订：复核 `ADJ-NC-02` 原图后确认可见物是蓝色双折钱包，而 case manifest 的来源注释写作 `Phone`。视觉参考已升为 v2，并保留旧值、修订原因与受影响 smoke 运行；来源标签未作为视觉真值，也未传入模型。已保存输出按钱包事实重新审阅；本轮没有发布正式内容分数，因此不存在需重算的量化分数。",
        "- 五个真实 GuardAId 模糊武器告警仍标注为未裁决；它们不能被当成枪／刀真值。带水印的旧 ACC-VA-01 副本移入 excluded 文件夹，当前四张替换证据未见图库水印。",
        "- 基线输入固定为 Y0（武器原始证据 JPEG）＋M0（短边 360；视频时长 ≤8 秒取 2 FPS，>8 秒取 1 FPS，最多前60秒）。优化试验仅为隔离离线评估。",
        "",
        "## 为什么没有宣布调优成功",
        "",
        "计划要求每例内容评分至少 9/10、前三项必需维度全为 2、无严重错误，且首次格式通过率、内容和延迟都合格。当前 smoke case `ADJ-CA-01` 的原始抽样帧可以看到女性把手伸向男性面部／上身、男性抬起手臂，随后女性转身离开；接触的力量与意图仍需谨慎描述。多个候选都把重点写成衣着、站位、谈话或笼统的“physical contact”，有的还明确写“no physical contact / no violence”。它们没有准确描述关键可见互动，不能通过内容门槛。",
        "",
        "`ADJ-NC-02` 的钱包受控负样本说明候选提示词有时能认出普通物体并提示可能误报；但同一案例在 gun / knife 两个诱导标签下表现不稳定。候选 v5 的最终输出在这两个受控变体上都未通过严格格式检查。单个成功样例不足以证明八类场景整体改善。",
        "",
        "### 单视频输入诊断（仅 3 次重复，非正式全类别延迟结论）",
        "",
        "| 视频配置 | 帧数 | 严格格式通过 | 平均 / nearest-rank P95 | 观察 |",
        "|---|---:|---:|---:|---|",
    ]
    profile_rows = []
    for profile, run_prefix in (("M0", "173523"), ("M1", "173556"), ("M2", "173610"), ("M3", "173642")):
        run = next((item for item in runs if item["name"].startswith(f"20261018_{run_prefix}_")), None)
        if run is None:
            continue
        rows = [row for row in run["result_rows"] if row.get("case_id") == "ADJ-CA-01"]
        times = [row["total_wall_seconds"] for row in rows]
        strict = 0
        for row in rows:
            raw = next((attempt.get("raw_response") for attempt in reversed(row.get("attempts", [])) if attempt.get("raw_response")), None)
            if raw:
                try:
                    evaluation.validate_scene_output(raw)
                    strict += 1
                except (ValueError, TypeError):
                    pass
        fps, short_side = evaluation.video_profile(4.8333333333, profile)
        frame_count = len(evaluation.sampling_targets(4.8333333333, fps)[1])
        if profile == "M1":
            observation = "480短边没有改善动作描述。"
        elif profile in {"M2", "M3"}:
            observation = "所有重复均未通过格式；高帧数明显更慢。"
        else:
            observation = "仍未具体描述伸手／接触动作。"
        profile_rows.append(f"| {profile}（{short_side}短边，{fps:g} FPS） | {frame_count} | {strict}/{len(rows)} | {fmt(statistics.mean(times) if times else None)} / {fmt(p95(times))} | {observation} |")
    report.extend(profile_rows)
    report.extend([
        "",
        "M0/M1 的样本输出没有达到动作描述要求。M2/M3 的 P95 分别约为 M0 的 3 倍，且这些短片在一次格式修正后仍未通过；它们不符合 1.20× 延迟上限。此处样本量只有一个视频，不能外推成 YOLO／MC3 整个类别的正式 P95。",
        "",
        "## 运行及测试记录",
        "",
        "| 运行 | 配置／提示词 | 已保存输出 | 严格格式首次／最终通过 | 平均 / P95 | 状态 |",
        "|---|---|---:|---:|---:|---|",
    ])
    reported_failures: set[str] = set()
    for item in runs:
        if item["name"].startswith("20261018_173306_"):
            if item["name"] not in reported_failures:
                report.append(f"| `{item['name']}` | baseline + candidate-v1，2例 smoke | 0 | 0 / 0 | — | failed：`KeyError: 'variant'`，发生在第一条 `/api/chat` 前；无案例推理输出。已保存原始 traceback 并加入回归测试。 |")
                reported_failures.add(item["name"])
            continue
        if item["rows"] == 0:
            continue
        report.append(f"| `{item['name']}` | {item['profile']} / {item['prompt']} | {item['rows']} | {item['strict_first']} / {item['strict_valid']} | {fmt(item['mean'])} / {fmt(item['p95'])} | {item['status']}；仅 smoke / profile 诊断，不是完整案例评测 |")
    report.extend([
        "",
        "- 自动化测试：14/14 通过，覆盖 40 例分拆与来源校验、类 ID 到 gun/knife 映射、8 秒与60秒采样边界、1/2/4 FPS 规则、图片 resize 不放大、武器照片最多8张、普通物体控制框、模型 payload 不泄漏案例目录／类别／参考事实、严格英文 JSON 句式，以及非有限数转为 JSON `null`。",
        "- 语法检查：评测器、案例审核工具、水印替换工具、测试模块均 `py_compile` 通过。",
        "- 全量 dry-run：40 个来源案例、90 个基线＋candidate-v5 输入／提示构造完成；没有发出 Ollama 请求。",
        "- 真实模型联通：修复进度日志字段后，baseline＋candidate-v1 的 2 个调整案例共6次 `/api/chat` 正常完成并写入 JSONL。随后 candidate-v2 至 v5 做了有限 smoke/profile 重复。未对16个验收案例运行模型，因为没有候选版本通过调整集 smoke 门槛。",
        "- Candidate v1-v5 的结果配置指纹分开保存，不能互相合并。旧版评测器曾把多句 summary／小写 detail 视为有效；后续加入严格单句、首字母大写校验。报告中的严格通过计数是用当前校验器离线重验原始输出得到，不改写任何模型回复。",
        "",
        "## 最终处置与后续方向",
        "",
        "- **没有最终获选提示词，也没有调优成功声明。** candidate-v5 仅是最后一个受测候选，不应部署。线上继续使用 `minicpm_alert_context_v9`。",
        "- 在 360/480 短边及 2/4 FPS 的限定试验内，改变提示词和输入档位都未可靠修复核心互动漏述；加帧主要增加耗时和格式失败。若要继续，应该作为新实验单独评估人物交互区域裁切／局部高分辨率证据或更强 VLM，并重新定义延迟与输入公平性；本轮未擅自改管线。",
        "- 16例 Acceptance Set 尚未解封推理，因此保持独立。只有出现能通过完整24例调整集的候选时，才继续做验收和最多三批新增验收。",
        "",
        "## Smoke 案例回复核对（未评分）",
        "",
        "以下展示已实际运行的首个重复及格式修正后的原始内容，便于核对失败原因；这些不是24例调整集评分，也不用于宣称模型质量提升。",
        "",
    ])
    manifest_path = ROOT / "VLM Fine-tune" / "case_manifest.csv"
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
        manifest = {row["case_id"]: row for row in csv.DictReader(stream)}
    notes_by_id = {item["case_id"]: item for item in references.get("cases", [])}
    for case_id, class_filter, why in (
        ("ADJ-CA-01", None, "核心可见互动是女性伸手靠近男性面部／上身，男性抬臂回应；仍需避免臆断施力与意图。"),
        ("ADJ-NC-02", ("gun",), "原图可见蓝色钱包；模型有时能说出钱包，但另一次在 details 中泄漏了标签文本 `gun 0.`，且格式不稳定。来源注释与可见物冲突，故按修订后的视觉参考阅读。"),
    ):
        row = manifest.get(case_id, {})
        note = notes_by_id.get(case_id, {})
        media = (row.get("media_files") or "").split("|")[0]
        source_link = ""
        if media:
            source_abs = (ROOT / "VLM Fine-tune" / Path(media)).resolve()
            source_link = f"；[原始输入证据](<{source_abs.as_posix()}>)"
        report.extend([
            f"### `{case_id}` — {row.get('category', 'case')} ({row.get('split', 'unknown')}; Y0/M0 smoke{source_link})",
            "",
            f"视觉参考：{note.get('main_visible_fact', '—')}",
            "",
            "**生产基线 v9：**",
            present_response(selected_raw_response(runs, case_id, "baseline", class_filter)),
            "",
            "**Candidate v5（格式修正后的回复）：**",
            present_response(selected_raw_response(runs, case_id, "candidate-v5", class_filter)),
            "",
            f"**复核：** {why}",
            "",
        ])
    report.extend([
        "这些案例没有正式打分；基线或候选回复即使有部分事实正确，也不会替代预先定义的逐例评分和24例调整集回归。",
        "",
        "## 提示词目录",
        "",
        "### 已测提示词版本指纹（SHA-256）",
        "",
        "user 哈希按任务上下文及类别参数展开；同一武器策略的 gun／knife／both／fallback 仅类别参数不同。Schema 和 repair 哈希也列出，避免只靠版本名复现。完整原文保存在各 run 的配置与请求记录中；下方另外列出生产基线和最后候选的完整展开文本。",
        "",
        "| ID / version | 路由 | System SHA-256 | User SHA-256 | Schema SHA-256 | Repair SHA-256 |",
        "|---|---|---|---|---|---|",
    ])
    routes = (
        ("weapon: gun", "weapon", ["gun"]),
        ("weapon: knife", "weapon", ["knife"]),
        ("weapon: gun, knife", "weapon", ["gun", "knife"]),
        ("weapon: fallback", "weapon", []),
        ("MC3: violence / physical-contact bullying", "violence", []),
    )
    schema_hash = evaluation.sha256_bytes(evaluation.canonical_json(SCENE_SCHEMA).encode())
    for prompt_id, spec in evaluation.PROMPT_CANDIDATES.items():
        repair = spec.get("repair_template", evaluation.REPAIR_TEMPLATE)
        repair_hash = evaluation.sha256_bytes(repair.encode())
        for route_name, kind, classes in routes:
            _, _, meta = evaluation.prompts_for(prompt_id, kind, classes)
            report.append(
                f"| `{prompt_id}` / `{spec['version']}` | {route_name} | "
                f"`{meta['system_sha256']}` | `{meta['user_sha256']}` | "
                f"`{schema_hash}` | `{repair_hash}` |"
            )
    report.extend([
        "",
        "### 当前生产基线：`minicpm_alert_context_v9`（保持未改）",
        "",
        "System：",
        code_block(evaluation.SCENE_SYSTEM_PROMPT),
        "",
        "Shared output instructions：",
        code_block(evaluation.SCENE_USER_PROMPT),
        "",
        "Production weapon user context：",
    ])
    for cls in ("gun", "knife", "gun, knife", "weapon"):
        report.append(f"\nExpanded user prompt for suspected classes `{cls}`:\n\n{code_block(scene_prompt('weapon', cls.split(', ') if cls != 'weapon' else []))}")
    report.append("\nProduction MC3 user prompt (violence / physical-contact bullying share this prompt):\n")
    report.append(code_block(scene_prompt("violence", [])))
    report.extend([
        "",
        "### Candidate v5（未通过；仅保留作审计）",
        "",
        "Shared System Prompt：",
        code_block(evaluation.COMMON_SYSTEM_V1),
        "",
        "Shared output instructions：",
        code_block(evaluation.COMMON_USER_V4),
        "",
        "Shared JSON Schema：",
        code_block(json.dumps(SCENE_SCHEMA, ensure_ascii=False, indent=2)),
        "",
        "YOLO uses one shared weapon policy; only `suspected_classes` changes. The following are the complete expanded user prompts:",
        "",
    ])
    for label, classes in (("gun", ["gun"]), ("knife", ["knife"]), ("both", ["gun", "knife"]), ("missing class fallback", [])):
        report.append(f"#### YOLO: {label}\n\n{code_block(candidate_v5_expanded('weapon', classes))}")
    report.append("#### MC3: violence and physical-contact bullying (shared)\n\n" + code_block(candidate_v5_expanded("violence", [])))
    report.extend([
        "#### Candidate-v5 format-repair prompt",
        "",
        code_block(evaluation.CANDIDATE_REPAIR_V4),
        "",
        "Candidate-v5 attempted settings remained `think=false`, temperature 0, seed 20261017, context 32768, max output 512, same schema and same model. It did not become a production version.",
        "",
        "## Reproducibility artifacts",
        "",
        f"- Frozen visual reference notes: `{(evaluation.CASES_ROOT / 'visual_reference_notes.json').relative_to(ROOT)}` ({len(references['cases'])} cases; not sent to MiniCPM).",
        f"- Input construction / resumable evaluator: `{Path(evaluation.__file__).relative_to(ROOT)}`.",
        f"- Regression tests: `{Path(__file__).resolve().parents[1].joinpath('tests/test_minicpm_scene_prompt_optimization.py').relative_to(ROOT)}`.",
        f"- Refreshable contact sheets: `{Path('guardaid_monitor/test_artifacts/minicpm_scene_prompt_review')}`.",
        f"- Run outputs: `{evaluation.RUN_ROOT.relative_to(ROOT)}`; every run stores its exact prompts, input hashes, runtime metadata, attempts, timings, and a code/input fingerprint.",
        "",
    ])
    REPORT.write_text("\n".join(report), encoding="utf-8")
    print(f"[report-written] {REPORT}")


if __name__ == "__main__":
    main()
