"""有界修复：只允许已验收槽位内调整；最多 max_rounds 轮，无改善即停止。

第一版可自动修复：字号低于槽位下限时提升至下限（内容不变）。
不可自动修复（需模型精简/拆页/人工）：overflow_risk、placeholder_leftover、
source_missing 等——生成报告交还用户，不静默改写内容。
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.util import Pt

from ..contracts import QAReport, RepairRound, SlideSpec, TemplateProfile
from ..templates_engine.ooxml import body_available_width_emu, est_text_width_emu
from .engine import run_qa

AUTOFIXABLE = {"tiny_text"}


def repair_slide(pptx_path: str | Path, page_index: int, profile: TemplateProfile,
                 layout) -> list[str]:
    """对单页执行可自动修复的动作，返回动作描述。"""
    prs = Presentation(str(pptx_path))
    slide = prs.slides[page_index]
    actions: list[str] = []
    for slot in layout.slots:
        if slot.min_font_pt <= 0:
            continue
        shape = None
        for sh in slide.shapes:
            if sh.shape_id == slot.object_id:
                shape = sh
                break
        if shape is None or not shape.has_text_frame:
            continue
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                if run.font.size is not None and run.font.size.pt < slot.min_font_pt:
                    new_pt = slot.min_font_pt
                    # 提升字号不得重新制造溢出：标题 fit 缩字后恰好单行放下时，
                    # 提回槽位软下限（font_pt×0.75）会再次超宽出格
                    if run.text and est_text_width_emu(run.text, new_pt) > body_available_width_emu(shape):
                        continue
                    run.font.size = Pt(new_pt)
                    actions.append(f"槽位 {slot.slot_id} 字号 {run.font.size.pt}→{new_pt}pt")
    if actions:
        prs.save(str(pptx_path))
    return actions


def run_repair_loop(pptx_path: str | Path, specs: list[SlideSpec], profile: TemplateProfile,
                    plans: list, max_rounds: int = 2,
                    known_source_ids: set[str] | None = None,
                    fact_values: dict[str, str] | None = None) -> tuple[list[QAReport], bool]:
    """QA → 修复 → 重跑，最多 max_rounds 轮。

    返回 (最终报告列表, 是否仍有阻塞失败)。
    停止条件：无 error 级失败；或达轮数上限；或重复相同问题无改善。
    """
    reports = run_qa(pptx_path, specs, profile, plans, known_source_ids, fact_values)
    layout_by_id = {l.layout_id: l for l in profile.layouts}
    for round_index in range(1, max_rounds + 1):
        blocking = [(i, r) for i, r in enumerate(reports) if r.blocking()]
        if not blocking:
            break
        prev_signature = _failure_signature(reports)
        any_action = False
        for i, report in blocking:
            slide_plan = plans[i]
            layout = layout_by_id[slide_plan.layout_id]
            actions = repair_slide(pptx_path, i, profile, layout)
            report.repair_rounds.append(
                RepairRound(round_index=round_index, actions=actions,
                            result="improved" if actions else "no_change"))
            any_action = any_action or bool(actions)
        if not any_action:
            # 无可自动修复动作：停止（T13：两轮内不循环调用）
            for report in reports:
                if report.repair_rounds and report.repair_rounds[-1].result == "no_change":
                    report.repair_rounds[-1].result = "stopped"
            break
        old_rounds = {r.slide_id: list(r.repair_rounds) for r in reports}
        reports = run_qa(pptx_path, specs, profile, plans, known_source_ids, fact_values)
        # 保留修复历史
        for report in reports:
            report.repair_rounds = old_rounds.get(report.slide_id, [])
        new_sig = _failure_signature(reports)
        if new_sig == prev_signature:
            # 相同问题重复出现：停止
            for report in reports:
                report.repair_rounds.append(
                    RepairRound(round_index=round_index, actions=[], result="stopped"))
            break
    still_blocking = any(r.blocking() for r in reports)
    return reports, still_blocking


def _failure_signature(reports: list[QAReport]) -> tuple:
    return tuple(
        (r.slide_id, tuple(sorted(c.check_id for c in r.blocking())))
        for r in reports
    )
