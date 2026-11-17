"""从检查结果构建 TemplateProfile。

内置模板：nero.* 命名约定 + notes 元数据（nero-layout:<purpose>）精确识别。
外部模板：启发式建议（占位符类型、对象类型、页面位置），档案 compatibility 中
显式标注"启发式识别"，第一版允许界面确认后修正。
"""
from __future__ import annotations

import re

from ..contracts.template import (
    CompatibilityIssue,
    FixedObject,
    Layout,
    Slot,
    TemplateProfile,
)
from .inspect import InspectShape, InspectSlide, TemplateInspection

EMU_PER_PT = 12700

# 内置槽位名 → (kind, 支持内容)
BUILTIN_SLOT_KINDS = {
    "nero.title": "text",
    "nero.subtitle": "text",
    "nero.section_no": "text",
    "nero.body": "bullets",
    "nero.left": "bullets",
    "nero.right": "bullets",
    "nero.table": "table",
    "nero.chart": "chart",
    "nero.image": "image",
}

LAYOUT_INTENTS = {
    "cover": ["title"],
    "section": ["section"],
    "bullets": ["bullets", "quote"],
    "two_column": ["two_column"],
    "image_text": ["image_text", "two_column"],
    "table": ["table"],
    "chart": ["chart_bar", "chart_line", "chart_pie"],
    "process": ["process"],
    "summary": ["summary", "bullets"],
}

_PURPOSE_RE = re.compile(r"nero-layout:([a-z_]+)")


def _estimate_budget(w_emu: int, h_emu: int, font_pt: float, is_table_or_chart: bool = False) -> tuple[int, int]:
    """按字号估算 (text_budget_chars, line_budget)。预筛用，不等于 Office 排版。"""
    if is_table_or_chart or w_emu <= 0 or h_emu <= 0:
        return 0, 0
    font_pt = font_pt if font_pt >= 8 else 14.0
    chars_per_line = max(4, int(w_emu / (font_pt * EMU_PER_PT)))
    line_count = max(1, int(h_emu / (font_pt * 1.3 * EMU_PER_PT)))
    return chars_per_line * line_count, line_count


def _font_pt_of(shape: InspectShape, default: float = 14.0) -> float:
    for para in shape.paragraphs:
        for run in para.runs:
            if run.size_pt:
                return run.size_pt
    # run 无显式字号：用占位符继承链解析的生效字号（inspect 阶段提取），
    # 避免 14pt 默认值对母版大字号标题（如 36pt）产生严重虚高的容量预算。
    if shape.effective_font_pt:
        return shape.effective_font_pt
    return default


def _make_slot(shape: InspectShape, kind: str, slot_id: str | None = None) -> Slot:
    font_pt = _font_pt_of(shape)
    is_bc = kind in ("table", "chart")
    chars, lines = _estimate_budget(shape.w, shape.h, font_pt, is_bc)
    allowed = {
        "text": ["text"],
        "bullets": ["bullets", "text"],
        "table": ["table"],
        "chart": ["chart"],
        "image": ["image"],
        "process": ["text"],
    }[kind]
    return Slot(
        slot_id=slot_id or shape.name,
        object_id=shape.shape_id,
        kind=kind,  # type: ignore[arg-type]
        bounds_emu=(shape.x, shape.y, shape.w, shape.h),
        text_budget_chars=chars,
        line_budget=lines,
        max_font_pt=int(font_pt) if font_pt else 0,
        min_font_pt=max(10, int(font_pt * 0.75)) if font_pt else 10,
        allowed_kinds=allowed,  # type: ignore[arg-type]
    )


def _shape_kind_by_name(name: str) -> str | None:
    if name in BUILTIN_SLOT_KINDS:
        return BUILTIN_SLOT_KINDS[name]
    if re.fullmatch(r"nero\.step\d+", name):
        return "process"
    return None


def _build_layout_from_builtin(slide: InspectSlide) -> Layout:
    m = _PURPOSE_RE.search(slide.notes_text or "")
    purpose = m.group(1) if m else f"slide{slide.index}"
    slots: list[Slot] = []
    fixed: list[FixedObject] = []
    step_slots: list[Slot] = []

    def handle(shape: InspectShape) -> None:
        if shape.is_group:
            for c in shape.children:
                handle(c)
            return
        kind = _shape_kind_by_name(shape.name)
        if kind == "process":
            step_slots.append(_make_slot(shape, "text", slot_id=shape.name))
            return
        if kind:
            slots.append(_make_slot(shape, kind))
            return
        fixed.append(FixedObject(object_id=shape.shape_id, kind=shape.shape_type, name=shape.name))

    for sh in slide.shapes:
        handle(sh)

    slots.extend(step_slots)
    return Layout(
        layout_id=f"{purpose}",
        purpose=purpose,
        source_page_kind="slide",
        source_slide_id=slide.slide_id,
        slots=slots,
        fixed_objects=fixed,
        supported_intents=LAYOUT_INTENTS.get(purpose, []),
    )


def _layout_purpose_guess(slide: InspectSlide) -> str:
    """外部模板启发式用途建议。"""
    has_table = any(s.has_table for s in slide.shapes)
    has_chart = any(s.has_chart for s in slide.shapes)
    body_ph = any(s.is_placeholder and any(k in s.placeholder_type for k in ("BODY", "OBJECT", "TEXT"))
                  for s in slide.shapes)
    title_ph = any(s.is_placeholder and "TITLE" in s.placeholder_type and "CENTER" not in s.placeholder_type
                   for s in slide.shapes)
    if has_table:
        return "table"
    if has_chart:
        return "chart"
    if slide.index == 0:
        return "cover"
    if body_ph:
        return "bullets"
    if title_ph:
        return "section"
    return f"slide{slide.index}"


def _build_layout_external(slide: InspectSlide, issues: list[CompatibilityIssue]) -> Layout:
    purpose = _layout_purpose_guess(slide)
    slots: list[Slot] = []
    fixed: list[FixedObject] = []

    def handle(shape: InspectShape) -> None:
        if shape.is_group:
            for c in shape.children:
                handle(c)
            return
        if shape.unsupported_kind:
            issues.append(CompatibilityIssue(
                object_id=shape.shape_id, object_kind=shape.unsupported_kind,
                severity="unsupported",
                message=f"对象 {shape.shape_id}（{shape.name}）类型 {shape.unsupported_kind} 不支持内容编辑，将原样保留"))
            fixed.append(FixedObject(object_id=shape.shape_id, kind=shape.shape_type, name=shape.name))
            return
        if shape.is_placeholder:
            pt = shape.placeholder_type
            if "TITLE" in pt or "CENTER" in pt:
                slots.append(_make_slot(shape, "text", slot_id=f"title{len(slots)}"))
                return
            if "BODY" in pt or "OBJECT" in pt or "TEXT" in pt:
                slots.append(_make_slot(shape, "bullets", slot_id=f"body{len(slots)}"))
                return
        if shape.has_table:
            slots.append(_make_slot(shape, "table", slot_id=f"table{len(slots)}"))
            return
        if shape.has_chart:
            slots.append(_make_slot(shape, "chart", slot_id=f"chart{len(slots)}"))
            return
        if shape.paragraphs:
            slots.append(_make_slot(shape, "text", slot_id=f"text{len(slots)}"))
            return
        fixed.append(FixedObject(object_id=shape.shape_id, kind=shape.shape_type, name=shape.name))

    for sh in slide.shapes:
        handle(sh)

    return Layout(
        layout_id=f"ext-slide{slide.index}-{purpose}",
        purpose=purpose,
        source_page_kind="slide",
        source_slide_id=slide.slide_id,
        slots=slots,
        fixed_objects=fixed,
        supported_intents=LAYOUT_INTENTS.get(purpose, []),
    )


def build_profile(inspection: TemplateInspection, template_id: str,
                  source_file: str = "") -> TemplateProfile:
    issues: list[CompatibilityIssue] = []
    is_builtin = any(_PURPOSE_RE.search(s.notes_text or "") for s in inspection.slides)
    layouts: list[Layout] = []
    for slide in inspection.slides:
        if is_builtin:
            layouts.append(_build_layout_from_builtin(slide))
        else:
            layouts.append(_build_layout_external(slide, issues))
    if not is_builtin:
        issues.append(CompatibilityIssue(
            object_kind="profile", severity="limited",
            message="外部模板采用启发式识别；用途与槽位需在工作台确认后再投入生成"))
    for link in inspection.external_links:
        issues.append(CompatibilityIssue(
            object_kind="hyperlink", severity="limited",
            message=f"模板包含外部链接 {link}，生成内容不会自动访问该链接"))
    return TemplateProfile(
        template_id=template_id,
        file_hash=inspection.file_hash,
        file_ext=inspection.ext,  # type: ignore[arg-type]
        slide_width_emu=inspection.width_emu,
        slide_height_emu=inspection.height_emu,
        layouts=layouts,
        fonts=[],
        compatibility=issues,
        source_file=source_file,
    )
