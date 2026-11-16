"""大纲与逐页规格生成（provider 驱动）。"""
from __future__ import annotations

import json

from ..contracts import Brief, Outline, OutlinePage, ParsedContent, SlideSpec
from ..providers.base import (
    SYSTEM_RULES,
    ModelProvider,
    ProviderError,
    validate_model_output,
    validate_model_output_loose,
)
from .scenarios import get_scene


def _facts_digest(contents: list[ParsedContent], max_chars: int = 12000) -> str:
    """事实表 + 章节摘要（保留 fact_id 与来源 block_id 引用）。"""
    lines: list[str] = []
    budget = max_chars
    for c in contents:
        doc = c.document
        lines.append(f"## 资料：{doc.display_name}（id={doc.source_id}）")
        for f in c.facts[:80]:
            row = (f"- [{f.fact_id}] {f.raw_evidence[:60]}"
                   + (f"（值={f.normalized_value}{f.unit}）" if f.normalized_value else "")
                   + (f"（时间={f.time}）" if f.time else ""))
            if budget - len(row) < 0:
                break
            budget -= len(row)
            lines.append(row)
    return "\n".join(lines) if lines else "（无可用事实）"


def _blocks_brief(contents: list[ParsedContent], max_chars: int = 16000) -> str:
    """资料原文摘要（按块，保留 block_id 引用）。"""
    lines: list[str] = []
    budget = max_chars
    for c in contents:
        for b in c.blocks:
            text = b.text or (b.table and "；".join("|".join(r) for r in b.table)) or ""
            if not text:
                continue
            row = f"- [{b.block_id}] {text[:400]}"
            if budget - len(row) < 0:
                lines.append("-（资料过长，已截断展示；生成时可要求更多上下文）")
                break
            budget -= len(row)
            lines.append(row)
    return "\n".join(lines) if lines else "（无原文）"


def outline_user_prompt(brief: Brief, scene: dict, facts: str, blocks: str) -> str:
    sections = "、".join(scene.get("sections", []))
    return (
        f"任务：基于资料生成演示大纲。\n"
        f"场景：{scene.get('name', brief.scene_id)}；推荐章节顺序：{sections}\n"
        f"主题：{brief.topic}\n受众：{brief.audience or '未指定'}\n目的：{brief.objective or '未指定'}\n"
        f"页数范围：{brief.page_range_min}-{brief.page_range_max}；时长：{brief.duration_minutes} 分钟\n"
        f"必含项：{'、'.join(brief.must_include) or '无'}；禁止项：{'、'.join(brief.forbidden) or '无'}\n"
        f"补充要求：{brief.extra_requirements or '无'}\n\n"
        f"可用事实表（fact_id 供引用，不得编造）：\n{facts}\n\n"
        f"资料原文摘要（block_id 供引用）：\n{blocks}\n\n"
        "输出 JSON：{\"brief\": {...需求摘要，含资料缺口 gaps...}, \"pages\": [{\"slide_id\": \"p1\", "
        "\"title\": \"...\", \"purpose\": \"...\", \"key_message\": \"...\", \"evidence\": "
        "[{\"source_id\": \"...\", \"block_id\": \"...\", \"quote\": \"...\"}], \"visual_intents\": [\"bullets\"], "
        "\"fact_ids\": [\"...\"], \"target_seconds\": 45, \"is_appendix\": false}]}\n"
        "要求：每页有明确结论；页数在范围内；证据只能引用给出的 source_id/block_id；"
        "资料不足时在 brief.gaps 列出并标注待补充，不得虚构。"
        "brief 只允许这些字段：topic, audience, objective, language, page_range_min, page_range_max, "
        "duration_minutes, must_include, forbidden, extra_requirements, gaps（字符串数组）；"
        "pages 每页只允许：slide_id, title, purpose, key_message, evidence, visual_intents, "
        "fact_ids, target_seconds, is_appendix；不要添加其他字段。"
    )


def generate_outline_from_provider(provider: ModelProvider, brief: Brief, scene: dict,
                                   contents: list[ParsedContent]) -> tuple[Outline, object]:
    user = outline_user_prompt(brief, scene, _facts_digest(contents), _blocks_brief(contents))
    data, usage = provider.generate_json(SYSTEM_RULES, user, max_tokens=4000)
    brief_data = data.get("brief", {})
    # 场景与版本由应用回填，不接受模型改写
    brief_data.setdefault("topic", brief.topic)
    brief_data["scene_id"] = brief.scene_id
    brief_data["scene_version"] = brief.scene_version
    brief_out = validate_model_output_loose(Brief, brief_data)
    pages_data = data.get("pages")
    if not isinstance(pages_data, list) or not pages_data:
        raise ProviderError("模型未返回大纲页面")
    outline = validate_model_output_loose(Outline, {"brief": brief_out.model_dump(), "pages": pages_data})
    return outline, usage


def outline_specs_from_provider(provider: ModelProvider, outline: Outline, scene: dict,
                                contents: list[ParsedContent],
                                slot_budgets: dict[str, str] | None = None,
                                on_page=None) -> tuple[list[SlideSpec], object]:
    """逐页生成 SlideSpec；每页只传相关事实与章节摘要，避免全量上下文。

    on_page(done_index, total)：每页完成后的回调（用于租约续期/进度上报）。
    """
    specs: list[SlideSpec] = []
    usage_total = None
    facts_all = _facts_digest(contents, 8000)
    total = len(outline.pages)
    for page_idx, page in enumerate(outline.pages):
        page_facts = [f"{f.fact_id}: {f.raw_evidence}" for f in
                      _facts_of(contents) if f.fact_id in (page.fact_ids or [])]
        evidence = json.dumps([e.model_dump() for e in page.evidence], ensure_ascii=False)
        user = (
            f"任务：为大纲页生成内容规格（SlideSpec）。\n"
            f"场景：{scene.get('name', '通用')}；本章推荐结构：{'、'.join(scene.get('sections', []))}\n"
            f"页面：{page.title}（目的：{page.purpose}；结论：{page.key_message}）\n"
            f"候选视觉意图：{page.visual_intents}\n"
            f"建议时长：{page.target_seconds} 秒\n"
            f"槽位预算提示：{json.dumps(slot_budgets or {}, ensure_ascii=False)}\n\n"
            f"本页相关事实：{'；'.join(page_facts) or '（无，可引用以下事实表）'}\n\n"
            f"事实表：\n{facts_all}\n\n"
            "输出 JSON（SlideSpec）：{\"slide_id\": \"...\", \"purpose\": \"...\", \"headline\": \"...\", "
            "\"body_blocks\": [{\"kind\": \"bullets\", \"items\": [...]} | {\"kind\": \"table\", \"headers\": [...], \"rows\": [...]} | "
            "{\"kind\": \"chart\", \"chart_type\": \"bar\", \"categories\": [...], \"series\": [{\"name\": \"...\", \"values\": [...]}]}], "
            "\"visual_intent\": \"...\", \"density\": \"normal\", \"source_refs\": [{\"source_id\": \"...\", \"block_id\": \"...\", \"quote\": \"...\"}], "
            "\"must_keep_fact_ids\": [...], \"speaker_notes\": {\"script\": \"口述正文\", \"transition\": \"转场\", "
            "\"source_explanations\": [...]}, \"target_seconds\": 45}\n"
            "要求：数字与限定条件不得改写或省略；无证据的数据标注\"待补充\"；"
            "visual_intent 必须从候选中选择。"
        )
        data, usage = provider.generate_json(SYSTEM_RULES, user, max_tokens=2500)
        usage_total = usage_total or usage
        data.setdefault("slide_id", page.slide_id)
        spec = validate_model_output_loose(SlideSpec, data)
        specs.append(spec)
        if on_page:
            on_page(page_idx + 1, total)
    return specs, usage_total


def _facts_of(contents: list[ParsedContent]):
    for c in contents:
        yield from c.facts


def generate_outline(step) -> dict:
    """Worker 步骤入口：解析产物 → provider（任务绑定的模型）→ Outline 快照 → awaiting_review。"""
    from ..services.model_resolver import resolve_job_provider

    job = step.ctx.jobs.get(step.job_id)
    provider = resolve_job_provider(step.ctx, job["model_profile_id"] if job else None)

    brief = Brief.model_validate_json(job["brief_json"]) if job["brief_json"] else Brief(topic=job["title"])
    scene = get_scene(brief.scene_id, brief.scene_version)
    contents: list[ParsedContent] = []
    for d in step.ctx.records.documents_for_job(step.job_id, kind="material"):
        p = step.ctx.files.resolve(f"jobs/{step.job_id}/parsed/{d['id']}.json")
        if p.exists():
            contents.append(ParsedContent.model_validate_json(p.read_bytes()))
    outline, usage = generate_outline_from_provider(provider, brief, scene, contents)
    rev = step.ctx.records.next_revision(step.job_id)
    data = outline.model_dump(mode="json")
    data["revision"] = rev
    step.ctx.records.save_revision(step.job_id, "outline", data, input_revision=step.input_revision)
    step.update_job_state(status="awaiting_review")
    return {"outline_revision": rev, "provider": provider.name,
            "usage": usage.__dict__ if usage else {}, "gaps": outline.brief.gaps}
