"""S02 集成测试：内置模板检查器与档案；外部风格测试稿识别。"""
import hashlib
from pathlib import Path

import pytest

from nero_ppt.contracts import TemplateProfile, envelope
from nero_ppt.templates_engine import build_profile, inspect_template

ROOT = Path(__file__).resolve().parents[2]
BUILTIN = ROOT / "assets" / "templates" / "builtin-classic.pptx"
EXTERNAL = ROOT / "tests" / "fixtures" / "external-style.pptx"
HANDMADE = ROOT / "tests" / "fixtures" / "handmade-spec.json"


@pytest.fixture(scope="module")
def builtin_insp():
    return inspect_template(BUILTIN)


@pytest.fixture(scope="module")
def builtin_profile(builtin_insp):
    return build_profile(builtin_insp, "builtin-classic", source_file=str(BUILTIN))


def test_inspect_locates_every_object(builtin_insp):
    """每个示例对象可定位：shape_id 页内唯一且非零。"""
    assert len(builtin_insp.slides) == 9
    for s in builtin_insp.slides:
        ids = [sh.shape_id for sh in s.shapes]
        assert all(i > 0 for i in ids)
        assert len(ids) == len(set(ids)), f"slide {s.index} 内 shape_id 重复"


def test_inspect_detects_table_and_chart(builtin_insp):
    table_page = builtin_insp.slides[4]
    assert any(sh.has_table for sh in table_page.shapes)
    chart_page = builtin_insp.slides[5]
    chart = [sh for sh in chart_page.shapes if sh.has_chart]
    assert len(chart) == 1 and chart[0].chart_type


def test_profile_refers_to_template_hash(builtin_insp, builtin_profile):
    """档案能回指具体模板哈希。"""
    assert isinstance(builtin_profile, TemplateProfile)
    real = hashlib.sha256(BUILTIN.read_bytes()).hexdigest()
    assert builtin_profile.file_hash == real
    assert builtin_profile.slide_width_emu == 12192000


def test_profile_builtin_layouts(builtin_profile):
    purposes = {l.layout_id: l for l in builtin_profile.layouts}
    for expected in ("cover", "section", "bullets", "two_column", "table", "chart", "process", "summary", "image_text"):
        assert expected in purposes, f"缺少版式 {expected}"
    chart_layout = purposes["chart"]
    chart_slots = [s for s in chart_layout.slots if s.kind == "chart"]
    assert len(chart_slots) == 1
    assert chart_slots[0].bounds_emu != (0, 0, 0, 0)


def test_profile_fixed_objects_protected(builtin_profile):
    bullets = {l.layout_id: l for l in builtin_profile.layouts}["bullets"]
    fixed_names = {f.name for f in bullets.fixed_objects}
    assert "nero.fixed.sidebar" in fixed_names and "nero.fixed.band" in fixed_names


def test_profile_process_slots(builtin_profile):
    proc = {l.layout_id: l for l in builtin_profile.layouts}["process"]
    step_slots = [s for s in proc.slots if s.slot_id.startswith("nero.step")]
    assert len(step_slots) == 4


def test_external_profile_flags_heuristic():
    insp = inspect_template(EXTERNAL)
    profile = build_profile(insp, "external-test", source_file=str(EXTERNAL))
    # 外部模板显式标注启发式识别
    assert any("启发式" in i.message for i in profile.compatibility)
    # 超链接被报告且不自动访问
    assert any("hyperlink" == i.object_kind for i in profile.compatibility)
    # 封面/正文建议存在
    purposes = [l.purpose for l in profile.layouts]
    assert "cover" in purposes
    # 槽位已识别（标题+正文占位符）
    bullets_layout = [l for l in profile.layouts if l.purpose == "bullets"][0]
    kinds = {s.kind for s in bullets_layout.slots}
    assert "text" in kinds and "bullets" in kinds


def test_unsupported_ext_rejected(tmp_path):
    p = tmp_path / "x.ppt"
    p.write_bytes(b"not-a-pptx")
    from nero_ppt.templates_engine.inspect import inspect_template

    with pytest.raises(ValueError):
        inspect_template(p)


def test_handmade_spec_roundtrip():
    """手工 SlideSpec 可反序列化（S03 Builder 输入有效性）。"""
    import json

    from nero_ppt.contracts import SlideSpec, from_envelope

    data = json.loads(HANDMADE.read_text(encoding="utf-8"))
    specs = [from_envelope(item, SlideSpec) for item in data]
    assert len(specs) == 7
    assert specs[1].must_keep_fact_ids == ["f-rev", "f-margin"]
    # 持久化往返一致
    for s in specs:
        assert from_envelope(envelope(s), SlideSpec).model_dump() == s.model_dump()
