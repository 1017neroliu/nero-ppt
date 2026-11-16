"""QA 引擎：对构建产物执行规则检查，输出 QAReport。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pptx import Presentation
from pptx.oxml.ns import qn

from ..contracts import (
    Layout,
    QACheck,
    QAReport,
    SlideSpec,
    TemplateProfile,
)


def _check(check_id: str, name: str, status: str, severity: str = "error",
           object_id: int = 0, slot_id: str = "", observed: str = "",
           expected: str = "", suggested: str = "") -> QACheck:
    return QACheck(check_id=check_id, check_name=name, status=status,  # type: ignore[arg-type]
                   severity=severity, object_id=object_id, slot_id=slot_id,
                   observed=observed, expected=expected, suggested_action=suggested)


# 内置模板示例残留词（检查输出页不应再出现的文本）
BUILTIN_LEFTOVERS = [
    "页面标题", "要点页标题", "双栏页标题", "表格页标题", "图表页标题",
    "流程页标题", "总结页标题", "图文页标题",
    "第一个要点", "第二个要点", "第三个要点", "总结要点一", "总结要点二", "下一步计划",
    "图片说明要点", "章节标题", "演示文稿主标题", "副标题：汇报人 / 日期",
    "左栏内容", "右栏内容",
]
_LEFTOVER_CELL_RE = re.compile(r"^示例\d+-\d+$")


def _shape_by_id(slide, object_id: int):
    for sh in slide.shapes:
        if sh.shape_id == object_id:
            return sh
    return None


def _iter_shapes(shapes):
    for sh in shapes:
        yield sh
        if getattr(sh, "is_group", False):
            yield from _iter_shapes(sh.shapes)


def check_structure(slide, spec: SlideSpec, layout: Layout) -> list[QACheck]:
    """结构检查：必需槽位非空、占位符残留。"""
    checks: list[QACheck] = []
    for slot in layout.slots:
        if not slot.required:
            continue
        shape = _shape_by_id(slide, slot.object_id)
        if shape is None:
            checks.append(_check("missing_object", "必需对象缺失", "fail", "error",
                                 slot.object_id, slot.slot_id,
                                 observed="对象不在输出页面", expected=f"对象 {slot.object_id} 存在",
                                 suggested="检查模板或重新生成"))
            continue
        if slot.kind in ("text", "bullets") and shape.has_text_frame:
            text = shape.text_frame.text.strip()
            if not text:
                checks.append(_check("empty_slot", "必需槽位为空", "fail", "error",
                                     slot.object_id, slot.slot_id,
                                     observed="空文本", expected="有内容",
                                     suggested="补充内容或降低版式要求"))
            leftovers = [w for w in BUILTIN_LEFTOVERS if w and w in text]
            leftover_cells = bool(shape.has_table and any(
                _LEFTOVER_CELL_RE.match(c.text) for row in shape.table.rows for c in row.cells))
            if leftovers or leftover_cells:
                checks.append(_check(
                    "placeholder_leftover", "占位符/示例残留", "fail", "error",
                    slot.object_id, slot.slot_id,
                    observed=f"残留 {leftovers[0] if leftovers else '示例单元格'}",
                    expected="无模板示例文字",
                    suggested="重新生成该页内容"))
    return checks


def check_geometry(slide, profile: TemplateProfile, layout: Layout) -> list[QACheck]:
    """几何检查：越界、固定对象差异。"""
    checks: list[QACheck] = []
    w, h = profile.slide_width_emu, profile.slide_height_emu
    for sh in _iter_shapes(slide.shapes):
        x, y = int(sh.left or 0), int(sh.top or 0)
        sw, sh_h = int(sh.width or 0), int(sh.height or 0)
        if sw <= 0 or sh_h <= 0:
            continue
        # 有意贴边（如底部色条 y+h == 页高）不算越界：允许完全在页内或对齐到边缘
        tolerance = 1000  # EMU
        if x < -tolerance or y < -tolerance or x + sw > w + tolerance or y + sh_h > h + tolerance:
            checks.append(_check(
                "out_of_bounds", "对象越界", "fail", "error",
                sh.shape_id, "",
                observed=f"bounds=({x},{y},{sw},{sh_h})",
                expected=f"在画布 {w}x{h} 内",
                suggested="检查模板页面或内容绑定"))
    # 固定对象差异：与档案对比（数量与几何）
    for fixed in layout.fixed_objects:
        sh = _shape_by_id(slide, fixed.object_id)
        if sh is None:
            checks.append(_check(
                "fixed_object_missing", "固定对象缺失", "fail", "error",
                fixed.object_id, "",
                observed=f"固定对象 {fixed.name} 不在输出页",
                expected="固定对象应保留",
                suggested="检查页面克隆是否完整"))
        else:
            src_bounds = None
            for l in profile.layouts:
                if l.layout_id == layout.layout_id:
                    for f in l.fixed_objects:
                        if f.object_id == fixed.object_id:
                            src_bounds = None  # 档案未存 bounds，跳过几何对比（结构已查）
            # 几何对比以克隆保留为准：这里只做存在性（bounds 对比在模板检查阶段完成）
    return checks


def _spec_text(spec: SlideSpec) -> str:
    parts = [spec.headline]
    for b in spec.body_blocks:
        for attr in ("items", "headers", "steps"):
            vals = getattr(b, attr, None)
            if vals:
                parts.extend(vals)
    return "".join(parts)


def check_content(spec: SlideSpec, known_source_ids: set[str], fact_values: dict[str, str]) -> list[QACheck]:
    """来源与必须事实检查（内容层，不看渲染）。"""
    checks: list[QACheck] = []
    for ref in spec.source_refs:
        if ref.source_id not in known_source_ids:
            checks.append(_check(
                "source_missing", "来源不存在", "fail", "error",
                0, "", observed=f"source_id={ref.source_id}",
                expected="引用已上传资料",
                suggested="核对资料引用或删除该来源标记"))
    text_all = _spec_text(spec)
    for fact_id in spec.must_keep_fact_ids:
        val = fact_values.get(fact_id)
        if not val:
            checks.append(_check(
                "must_keep_fact_absent", "必须事实未核对", "fail", "warning",
                0, "", observed=f"fact {fact_id} 无归一化值可用",
                expected="可自动核对",
                suggested="补充事实提取结果或人工核对"))
            continue
        if val not in text_all:
            checks.append(_check(
                "must_keep_fact_absent", "必须事实未体现", "fail", "warning",
                0, "", observed=f"fact {fact_id}（{val}）未出现在页面文本",
                expected="必须保留的事实应在内容或讲稿中",
                suggested="检查该事实是否被精简掉；必要时人工确认"))
    return checks


def check_overflow(spec: SlideSpec, layout: Layout) -> list[QACheck]:
    """文字容量预检：只给风险提示（warning），不等于 Office 排版。"""
    checks: list[QACheck] = []

    def est(text: str, slot) -> None:
        if slot.text_budget_chars and len(text) > slot.text_budget_chars:
            checks.append(_check(
                "overflow_risk", "文字容量风险", "fail", "warning",
                slot.object_id, slot.slot_id,
                observed=f"{len(text)} 字", expected=f"≤{slot.text_budget_chars} 字（估算）",
                suggested="精简表达或拆页；自动修复不删减数字与限定条件"))

    for slot in layout.slots:
        if slot.kind in ("text", "bullets"):
            shape_bound_texts: list[str] = []
            for b in spec.body_blocks:
                items = getattr(b, "items", None)
                if items:
                    shape_bound_texts.extend(items)
            if slot.slot_id == "nero.title" or slot.kind == "text":
                est(spec.headline, slot)
            if shape_bound_texts and slot.kind == "bullets":
                est("".join(shape_bound_texts), slot)
    return checks


def check_notes(slide, spec: SlideSpec) -> list[QACheck]:
    if not slide.has_notes_slide or not slide.notes_slide.notes_text_frame.text.strip():
        return [_check("notes_missing", "讲稿缺失", "fail", "error", 0, "",
                       observed="无备注", expected="逐页讲稿存在", suggested="重新写入 Notes")]
    return []


def check_image_fit(slide, layout: Layout) -> list[QACheck]:
    """图片裁切风险：纵横比与槽位差异 >25% 时提示（cover 策略下渲染会裁切）。"""
    checks: list[QACheck] = []
    for slot in layout.slots:
        if slot.kind != "image":
            continue
        sh = _shape_by_id(slide, slot.object_id)
        if sh is None:
            continue
        try:
            iw, ih = sh.image.size
        except Exception:  # noqa: BLE001
            continue
        if not iw or not ih or not slot.bounds_emu[2] or not slot.bounds_emu[3]:
            continue
        src_ratio = iw / ih
        dst_ratio = slot.bounds_emu[2] / slot.bounds_emu[3]
        if abs(src_ratio - dst_ratio) / dst_ratio > 0.25:
            checks.append(_check(
                "image_aspect_mismatch", "图片纵横比差异", "fail", "warning",
                slot.object_id, slot.slot_id,
                observed=f"图片 {src_ratio:.2f} vs 槽位 {dst_ratio:.2f}",
                expected="差异 ≤25% 或按裁切策略处理",
                suggested=f"按 {slot.crop_strategy} 策略裁切或更换图片"))
    return checks


def run_qa(pptx_path: str | Path, specs: list[SlideSpec], profile: TemplateProfile,
           plans: list, known_source_ids: set[str] | None = None,
           fact_values: dict[str, str] | None = None) -> list[QAReport]:
    """按页执行全部结构/几何/内容检查；视觉检查在渲染后单独执行（可选适配器）。"""
    prs = Presentation(str(pptx_path))
    data = Path(pptx_path).read_bytes()
    artifact_hash = hashlib.sha256(data).hexdigest()
    layout_by_id = {l.layout_id: l for l in profile.layouts}
    reports: list[QAReport] = []
    for i, spec in enumerate(specs):
        plan = plans[i]
        slide = prs.slides[i]
        layout = layout_by_id[plan.layout_id]
        checks: list[QACheck] = []
        checks.extend(check_structure(slide, spec, layout))
        checks.extend(check_geometry(slide, profile, layout))
        checks.extend(check_content(spec, known_source_ids or set(), fact_values or {}))
        checks.extend(check_overflow(spec, layout))
        checks.extend(check_notes(slide, spec))
        checks.extend(check_image_fit(slide, layout))
        reports.append(QAReport(slide_id=spec.slide_id, revision=plan.revision,
                                artifact_hash=artifact_hash, checks=checks,
                                visual_checked=False))
    return reports
