"""绑定层：SlideSpec + TemplateProfile → RenderPlan（程序生成，模型不参与）。

预算（text_budget_chars）在此做预筛：超出时产出警告交 QA 层，不静默截断。
"""
from __future__ import annotations

from ..contracts import (
    ChartBlock,
    ImageRef,
    ProcessStepBlock,
    RenderPlan,
    SlideSpec,
    Slot,
    TableBlock,
    TemplateProfile,
    TextBullets,
)
from ..contracts import BodyBlock

# visual_intent → 候选 layout purpose（按优先级）
INTENT_TO_PURPOSES: dict[str, list[str]] = {
    "title": ["cover"],
    "section": ["section"],
    "bullets": ["bullets", "summary"],
    "quote": ["bullets"],
    "two_column": ["two_column"],
    "image_text": ["image_text", "two_column"],
    "table": ["table"],
    "chart_bar": ["chart"],
    "chart_line": ["chart"],
    "chart_pie": ["chart"],
    "image": ["image_text"],
    "process": ["process"],
    "summary": ["summary", "bullets"],
    "appendix": ["summary", "bullets"],
}


class BindingError(ValueError):
    """无法绑定的内容/版式组合：显式报错，不静默降级。"""


def _layout_capacity(layout) -> int:
    """版式的文本承载能力（正文槽位预算之和），用于回退排序。"""
    return sum(s.text_budget_chars for s in layout.slots if s.kind in ("text", "bullets"))


def _layout_can_host(layout, spec: SlideSpec) -> bool:
    """版式是否能容纳该意图的内容。"""
    kinds = {s.kind for s in layout.slots}
    text_slots = [s for s in layout.slots if s.kind in ("text", "bullets")]
    intent = spec.visual_intent
    if intent == "table":
        # 无表格版式时可降级为要点列表（数据保真）
        return "table" in kinds or bool(text_slots)
    if intent in ("chart_bar", "chart_line", "chart_pie"):
        # 无图表版式时可降级为表格/要点（数据保真）
        return "chart" in kinds or "table" in kinds or bool(text_slots)
    if intent == "process":
        return len(text_slots) >= 2 or any(s.slot_id.startswith("nero.step") for s in layout.slots)
    if intent in ("two_column", "image_text"):
        return len(text_slots) >= 2
    if intent == "image":
        return "image" in kinds or bool(text_slots)
    # title/section/bullets/quote/summary/appendix 等
    return bool(text_slots)


def choose_layout(profile: TemplateProfile, spec: SlideSpec) -> tuple[object, list[str]]:
    """按 visual_intent 选版式；无精确匹配时按内容承载能力回退到最接近的版式。"""
    warnings: list[str] = []
    candidates = INTENT_TO_PURPOSES.get(spec.visual_intent, ["bullets"])
    by_purpose = {l.purpose: l for l in profile.layouts}
    for purpose in candidates:
        layout = by_purpose.get(purpose)
        if layout is not None:
            if purpose not in (layout.supported_intents or []) and layout.supported_intents:
                warnings.append(
                    f"版式 {layout.layout_id} 未显式声明支持 intent={spec.visual_intent}，按候选顺序使用")
            return layout, warnings
    # 二级回退（外部模板常见：用途启发式与实际内容类型不完全一致）
    hostable = [l for l in profile.layouts if _layout_can_host(l, spec)]
    if hostable:
        best = max(hostable, key=_layout_capacity)
        warnings.append(
            f"模板无 {spec.visual_intent} 专用版式，已回退到最接近的版式「{best.layout_id}」"
            f"（如需精确版式请更换模板或调整该页意图）")
        return best, warnings
    raise BindingError(
        f"模板缺少支持 {spec.visual_intent} 的版式（候选 {candidates}），"
        f"且没有任何版式能容纳该内容类型；请更换模板或精简该页")


def _slot(layout, slot_id: str):
    for s in layout.slots:
        if s.slot_id == slot_id:
            return s
    return None


def _slot_by_kind(layout, kind: str, index: int = 0):
    matches = [s for s in layout.slots if s.kind == kind]
    if len(matches) <= index:
        return None
    return matches[index]


def _check_budget(slot: Slot, text: str, warnings: list[str], where: str) -> None:
    if slot.text_budget_chars and text and len(text) > slot.text_budget_chars:
        warnings.append(
            f"{where} 内容 {len(text)} 字超出槽位 {slot.slot_id} 预算 {slot.text_budget_chars}（预筛提示，最终以渲染检查为准）")


def _downgrade_blocks(blocks: list, layout, warnings: list[str], slide_id: str) -> list:
    """版式缺槽位时的内容降级：chart→table→bullets，数据保真不删减。"""
    kinds = {s.kind for s in layout.slots}
    out = list(blocks)
    if any(isinstance(b, ChartBlock) for b in out) and "chart" not in kinds:
        new = []
        for b in out:
            if isinstance(b, ChartBlock):
                headers = ["类别"] + [s.name for s in b.series]
                rows = []
                for i, cat in enumerate(b.categories):
                    row = [cat]
                    for s in b.series:
                        row.append(str(s.values[i]) if i < len(s.values) else "")
                    rows.append(row)
                new.append(TableBlock(headers=headers, rows=rows, note=b.note or ""))
                warnings.append(f"页面 {slide_id}：模板无图表版式，图表数据已转为表格展示")
            else:
                new.append(b)
        out = new
    if any(isinstance(b, TableBlock) for b in out) and "table" not in kinds:
        new = []
        for b in out:
            if isinstance(b, TableBlock):
                items = [" | ".join(b.headers)] if b.headers else []
                for row in b.rows:
                    items.append("；".join(x for x in row if x)[:200])
                new.append(TextBullets(items=items or ["（无数据）"], label=b.label))
                warnings.append(f"页面 {slide_id}：模板无表格版式，表格已转为要点列表")
            else:
                new.append(b)
        out = new
    return out


def _infer_dispatch_purpose(layout, spec: SlideSpec) -> str:
    """渲染策略分派：按 spec 内容类型优先（覆盖外部模板启发式的用途误判）；
    纯标题页（无正文内容块）按版式原用途。"""
    standard = ("cover", "section", "bullets", "summary", "two_column",
                "table", "chart", "process", "image_text")
    blocks = spec.body_blocks
    kinds = {s.kind for s in layout.slots}
    text_slots = [s for s in layout.slots if s.kind in ("text", "bullets")]
    if any(isinstance(b, ImageRef) for b in blocks) and "image" in kinds:
        return "image_text"
    if any(isinstance(b, TableBlock) for b in blocks) and "table" in kinds:
        return "table"
    if any(isinstance(b, ChartBlock) for b in blocks) and "chart" in kinds:
        return "chart"
    if any(isinstance(b, ProcessStepBlock) for b in blocks) and len(text_slots) >= 2:
        return "process"
    if len(text_slots) >= 2 and len([b for b in blocks if isinstance(b, TextBullets)]) >= 2:
        return "two_column"
    if any(isinstance(b, (TextBullets, TableBlock, ChartBlock, ProcessStepBlock, ImageRef))
           for b in blocks):
        return "bullets"
    return layout.purpose if layout.purpose in standard else "bullets"


def plan_slide(spec: SlideSpec, profile: TemplateProfile,
               page_index: int) -> tuple[RenderPlan, list[str], SlideSpec]:
    """生成单页 RenderPlan。返回 (计划, 警告, 生效规格——版式缺槽位时内容已降级)。"""
    warnings: list[str] = []
    layout, w = choose_layout(profile, spec)
    warnings.extend(w)
    # 内容降级：版式缺 chart/table 槽位时转换数据形态
    kinds = {s.kind for s in layout.slots}
    if ((any(isinstance(b, ChartBlock) for b in spec.body_blocks) and "chart" not in kinds)
            or (any(isinstance(b, TableBlock) for b in spec.body_blocks) and "table" not in kinds)):
        downgraded = _downgrade_blocks(spec.body_blocks, layout, warnings, spec.slide_id)
        spec = spec.model_copy(update={"body_blocks": downgraded})
    layout.purpose = _infer_dispatch_purpose(layout, spec)  # 回退版式按槽位推断渲染策略
    bindings = []
    notes_parts = [spec.speaker_notes.script]
    if spec.speaker_notes.transition:
        notes_parts.append(f"【转场】{spec.speaker_notes.transition}")
    if spec.speaker_notes.source_explanations:
        notes_parts.extend(f"【数据解释】{e}" for e in spec.speaker_notes.source_explanations)
    notes_parts.append(f"【建议时长】{spec.target_seconds} 秒")

    def bind(slot: Slot | None, action: str, **kw) -> None:
        if slot is None:
            raise BindingError(f"版式 {layout.layout_id} 缺少必要槽位（{action}）")
        kw.update({"slot_id": slot.slot_id, "object_id": slot.object_id})
        kw.setdefault("edit_action", action)
        bindings.append(kw)

    # 标题：text 类槽位（title 优先，其次第一个 text 槽位）
    title_slot = _slot(layout, "nero.title") or _slot_by_kind(layout, "text", 0)
    if title_slot is None:
        raise BindingError(f"版式 {layout.layout_id} 无标题槽位")
    _check_budget(title_slot, spec.headline, warnings, "headline")
    bind(title_slot, "set_text", source="constant", constant_text=spec.headline)

    blocks = spec.body_blocks
    if layout.purpose == "cover":
        sub = _slot(layout, "nero.subtitle")
        if sub is not None:
            text = spec.purpose or spec.speaker_notes.script[:40]
            bind(sub, "set_text", source="constant", constant_text=text)
    elif layout.purpose == "section":
        no_slot = _slot(layout, "nero.section_no")
        if no_slot is not None:
            bind(no_slot, "set_text", source="constant",
                 constant_text=f"{page_index:02d}")
    elif layout.purpose in ("bullets", "summary"):
        items: list[str] = []
        subs: list[str] = []
        for b in blocks:
            if isinstance(b, TextBullets):
                items.extend(b.items)
                # 子要点按主条数对齐补充
                for i in range(len(b.items)):
                    subs.append(b.items_sub[i] if i < len(b.items_sub) else "")
        body_slot = (_slot(layout, "nero.body") or _slot_by_kind(layout, "bullets"))
        if body_slot is None:
            # 回退：排除已绑标题的槽位，选正文预算最大的 text 槽位
            candidates = [s for s in layout.slots
                          if s.kind in ("text", "bullets") and s.slot_id != title_slot.slot_id]
            body_slot = max(candidates, key=lambda s: s.text_budget_chars) if candidates else None
        if not items:
            items = [spec.purpose or spec.headline]
            warnings.append("正文无 bullets 块，回退使用 purpose 作为单条要点")
        _check_budget(body_slot, "".join(items), warnings, "body")
        lines: list[str] = []
        levels: list[int] = []
        for i, it in enumerate(items):
            lines.append(it)
            levels.append(0)
            if i < len(subs) and subs[i]:
                lines.append(subs[i])
                levels.append(1)
        bind(body_slot, "set_bullets", source="spec", text_lines=lines, line_levels=levels)
    elif layout.purpose == "two_column":
        # 槽位回退：优先 nero.left/right；否则按 y 坐标排序的 text 槽位分左右（排除标题槽）
        text_like = sorted([s for s in layout.slots if s.kind in ("text", "bullets")
                            and s.slot_id != title_slot.slot_id],
                           key=lambda s: (s.bounds_emu[1], s.bounds_emu[0]))
        left_slot = _slot(layout, "nero.left") or _slot_by_kind(layout, "bullets", 0)
        if left_slot is None:
            left_slot = text_like[0] if text_like else None
        right_slot = _slot(layout, "nero.right") or _slot_by_kind(layout, "bullets", 1)
        if right_slot is None:
            right_slot = text_like[1] if len(text_like) >= 2 else None
        left_blocks = [b for b in blocks if isinstance(b, TextBullets)]
        if not left_blocks:
            raise BindingError("双栏页需要至少一个 bullets 块")
        bind(left_slot, "set_bullets", source="spec",
             text_lines=list(left_blocks[0].items),
             line_levels=[0] * len(left_blocks[0].items))
        if right_slot is not None:
            if len(left_blocks) > 1:
                bind(right_slot, "set_bullets", source="spec",
                     text_lines=list(left_blocks[1].items),
                     line_levels=[0] * len(left_blocks[1].items))
            else:
                bind(right_slot, "set_text", source="constant", constant_text="")
        else:
            warnings.append("版式无第二栏槽位，双栏内容已合并至左栏")
    elif layout.purpose == "table":
        tb = next((b for b in blocks if isinstance(b, TableBlock)), None)
        if tb is None:
            raise BindingError("表格页需要 table 块")
        table_slot = _slot(layout, "nero.table") or _slot_by_kind(layout, "table")
        bind(table_slot, "set_table", source="spec", block_index=blocks.index(tb))
        if tb.note:
            warnings.append(f"表格注释将写入讲稿：{tb.note}")
            notes_parts.append(f"【表格注释】{tb.note}")
    elif layout.purpose == "chart":
        cb = next((b for b in blocks if isinstance(b, ChartBlock)), None)
        if cb is None:
            raise BindingError("图表页需要 chart 块")
        chart_slot = _slot(layout, "nero.chart") or _slot_by_kind(layout, "chart")
        bind(chart_slot, "set_chart_data", source="spec", block_index=blocks.index(cb))
        if cb.note:
            notes_parts.append(f"【图表注释】{cb.note}")
    elif layout.purpose == "process":
        pb = next((b for b in blocks if isinstance(b, ProcessStepBlock)), None)
        step_slots = [s for s in layout.slots if s.slot_id.startswith("nero.step")]
        if pb is None:
            raise BindingError("流程页需要 process 块")
        if len(pb.steps) > len(step_slots):
            raise BindingError(
                f"流程步骤 {len(pb.steps)} 个超出版式槽位 {len(step_slots)} 个；请精简或换版式")
        for i, step in enumerate(pb.steps):
            bind(step_slots[i], "set_text", source="spec",
                 constant_text=step)
    elif layout.purpose == "image_text":
        img = next((b for b in blocks if isinstance(b, ImageRef)), None)
        image_slot = _slot(layout, "nero.image") or _slot_by_kind(layout, "image")
        if img is not None:
            bind(image_slot, "replace_image", source="resource", resource_id=img.resource_id)
            if img.caption:
                notes_parts.append(f"【图片说明】{img.caption}")
        body_slot = (_slot(layout, "nero.body") or _slot_by_kind(layout, "bullets")
                     or _slot_by_kind(layout, "text"))
        text_items: list[str] = []
        for b in blocks:
            if isinstance(b, TextBullets):
                text_items.extend(b.items)
        if text_items:
            bind(body_slot, "set_bullets", source="spec",
                 text_lines=text_items, line_levels=[0] * len(text_items))
    else:
        raise BindingError(f"未实现的版式用途：{layout.purpose}")

    plan = RenderPlan(
        slide_id=spec.slide_id,
        revision=1,
        template_version=profile.version,
        layout_id=layout.layout_id,
        source_slide_id=layout.source_slide_id,
        slot_bindings=bindings,  # type: ignore[arg-type]
        notes_text="\n".join(notes_parts),
        resources=[b["resource_id"] for b in bindings if b.get("source") == "resource"],
    )
    return plan, warnings, spec
