"""S16 端到端验收：第二套内置模板、外部模板全链路、支持矩阵基础。"""
import json
from pathlib import Path

import pytest
from pptx import Presentation

from nero_ppt.contracts import SlideSpec
from nero_ppt.qa import run_qa
from nero_ppt.rendering.builder import build_from_specs
from nero_ppt.rendering.libreoffice import convert_to_pdf, pdf_page_count
from nero_ppt.templates_engine import build_profile, inspect_template

ROOT = Path(__file__).resolve().parents[2]
TMP = ROOT / "data" / "tmp-e2e"

SPECS = [
    {"slide_id": "c1", "headline": "外部模板封面", "visual_intent": "title",
     "speaker_notes": {"script": "封面"}},
    {"slide_id": "b1", "headline": "外部正文页", "visual_intent": "bullets",
     "body_blocks": [{"kind": "bullets", "items": ["第一条", "第二条", "第三条"]}],
     "speaker_notes": {"script": "正文讲稿"}},
]


def _specs():
    return [SlideSpec.model_validate(s) for s in SPECS]


@pytest.mark.parametrize("template_name", ["builtin-classic", "builtin-minimal"])
def test_both_builtin_templates_end_to_end(template_name):
    """两套内置模板：档案→绑定→渲染→PDF→QA 全链路。"""
    template = ROOT / "assets" / "templates" / f"{template_name}.pptx"
    profile = build_profile(inspect_template(template), template_name)
    out = TMP / f"{template_name}-e2e.pptx"
    result, plans = build_from_specs(template, profile, _specs(), out, tmp_dir=TMP)
    assert result.slide_count == 2
    pdf = convert_to_pdf(out, TMP / f"work-{template_name}", out_path=TMP / f"{template_name}.pdf")
    assert pdf.pages == 2
    reports = run_qa(out, _specs(), profile, plans, known_source_ids={"x"})
    assert not any(r.blocking() for r in reports), [c.model_dump() for r in reports for c in r.blocking()]
    # Notes 可读回
    prs = Presentation(str(out))
    assert "封面" in prs.slides[0].notes_slide.notes_text_frame.text


def test_external_style_template_full_flow():
    """外部风格测试稿：启发式档案 → 渲染 → 兼容性显式报告。"""
    template = ROOT / "tests" / "fixtures" / "external-style.pptx"
    insp = inspect_template(template)
    profile = build_profile(insp, "external-test")
    assert any("启发式" in i.message for i in profile.compatibility)
    out = TMP / "external-e2e.pptx"
    result, plans = build_from_specs(template, profile, _specs(), out, tmp_dir=TMP)
    assert result.slide_count == 2
    pdf = convert_to_pdf(out, TMP / "work-ext", out_path=TMP / "external.pdf")
    assert pdf.pages == 2
    reports = run_qa(out, _specs(), profile, plans)
    assert not any(r.blocking() for r in reports)


def test_clone_preserves_external_hyperlink():
    """页面克隆保留外部超链接关系（不自动访问）。"""
    from nero_ppt.templates_engine import ooxml

    template = ROOT / "tests" / "fixtures" / "external-style.pptx"
    prs = Presentation(str(template))
    new = ooxml.clone_slide(prs, prs.slides[2])  # 含超链接的页面
    rels = new.part.rels
    assert any(r.is_external and "example.com" in r.target_ref for r in rels.values())


def test_support_matrix_summary():
    """支持矩阵：已验证的版式覆盖。"""
    matrix = {}
    for name in ("builtin-classic", "builtin-minimal"):
        profile = build_profile(inspect_template(ROOT / "assets" / "templates" / f"{name}.pptx"), name)
        matrix[name] = sorted(l.layout_id for l in profile.layouts)
    assert matrix["builtin-classic"] == ["bullets", "chart", "cover", "image_text",
                                         "process", "section", "summary", "table", "two_column"]
    assert matrix["builtin-minimal"] == matrix["builtin-classic"]
