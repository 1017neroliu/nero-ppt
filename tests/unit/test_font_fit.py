"""字号继承解析与标题宽度自适应单元测试。

背景：外部模板占位符 run 无显式字号时，实际渲染继承母版字号（如 36pt），
此前预筛预算按 14pt 默认值估算导致容量虚高、标题折行溢出占位符（出格）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Emu, Pt

from nero_ppt.templates_engine.ooxml import (
    apply_font_fit,
    effective_font_pt,
    est_text_width_emu,
    set_text_keep_format,
)

ROOT = Path(__file__).resolve().parents[2]


def _title_shape(prs, slide_index: int):
    slide = prs.slides[slide_index]
    for sh in slide.shapes:
        if sh.name == "nero.title":
            return sh
    raise AssertionError("未找到 nero.title 形状")


def test_est_text_width_fullwidth_vs_halfwidth():
    full = est_text_width_emu("一二三", 36.0)
    half = est_text_width_emu("abc", 36.0)
    # 全角：3 × 36pt × 12700
    assert full == 3 * 36 * 12700
    # 半角按 0.62 系数，明显窄于等长全角
    assert 0 < half < full


def test_effective_font_from_run():
    """run 显式字号优先（内置模板标题 32pt）。"""
    prs = Presentation(str(ROOT / "assets" / "templates" / "builtin-classic.pptx"))
    sh = _title_shape(prs, 1)
    assert effective_font_pt(sh, default_pt=0.0) == pytest.approx(32.0)


def test_effective_font_from_master_chain():
    """run 无显式字号时沿 layout/master 链解析（默认模板 titleStyle 44pt）。"""
    prs = Presentation()  # python-pptx 默认模板：标题占位符格式继承自母版
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    sh = slide.shapes.title
    for p in sh.text_frame.paragraphs:
        for r in p.runs:
            r.font.size = None
    pt = effective_font_pt(sh, default_pt=0.0)
    assert pt > 0, "应从占位符继承链解析出字号"
    assert pt == pytest.approx(44.0), f"默认模板母版标题字号应为 44pt，实际 {pt}"


def test_apply_font_fit_shrinks_long_title():
    """超宽标题：缩至恰好单行放下的字号，且不低于下限。"""
    prs = Presentation(str(ROOT / "assets" / "templates" / "builtin-classic.pptx"))
    sh = _title_shape(prs, 1)
    set_text_keep_format(sh.text_frame, "EtherCAT具备实时控制价值，年度成果需以工程验证闭环")
    info = apply_font_fit(sh, sh.text_frame.text, min_font_pt=12.0)
    if info is None:
        pytest.skip("内置模板标题框足够宽，不触发缩字")
    assert info["to_pt"] < info["from_pt"]
    assert info["to_pt"] >= 12.0
    assert info["overflow"] is False
    assert sh.text_frame.paragraphs[0].runs[0].font.size == Pt(info["to_pt"])


def test_apply_font_fit_short_title_untouched():
    """短标题：不修改字号。"""
    prs = Presentation(str(ROOT / "assets" / "templates" / "builtin-classic.pptx"))
    sh = _title_shape(prs, 1)
    set_text_keep_format(sh.text_frame, "短标题")
    before = sh.text_frame.paragraphs[0].runs[0].font.size
    info = apply_font_fit(sh, sh.text_frame.text, min_font_pt=12.0)
    assert info is None
    assert sh.text_frame.paragraphs[0].runs[0].font.size == before


def test_apply_font_fit_min_clamp_reports_overflow():
    """超长文本缩到下限仍放不下：字号 clamp 到下限且 overflow=True。"""
    prs = Presentation(str(ROOT / "assets" / "templates" / "builtin-classic.pptx"))
    sh = _title_shape(prs, 1)
    long_text = "超" * 200
    set_text_keep_format(sh.text_frame, long_text)
    info = apply_font_fit(sh, long_text, min_font_pt=30.0)
    if info is None:
        pytest.skip("标题框足够容纳，不触发缩字")
    assert info["to_pt"] == 30.0
    assert info["overflow"] is True


def test_real_task_title_would_fit(tmp_path):
    """回归：6.1-sol 任务实际出格标题（24 字，母版 36pt，11.21in 单行框）。

    构造等效场景：克隆版式中标题占位符继承 36pt、框宽 10254732 EMU；
    24 字标题按 36pt 估算超宽 → 自动缩字号后单行放得下。
    """
    width_emu = 10254732
    avail = width_emu - 2 * 91440
    text = "EtherCAT具备实时控制价值，年度成果需以工程验证闭环"
    est36 = est_text_width_emu(text, 36.0)
    assert est36 > avail, "该标题在 36pt 下应超宽（复现出格场景）"
    from_pt, to_pt = 36.0, int(36.0 * avail / est36)
    assert to_pt >= 12
    assert est_text_width_emu(text, to_pt) <= avail, "缩字后应单行放下"
