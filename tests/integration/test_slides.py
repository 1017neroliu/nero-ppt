"""S11 集成测试：逐页规格、局部修改、ChangePlan、修复轮边界。"""
import json
from pathlib import Path

import pytest

from nero_ppt.contracts import ChangePlan, Outline, SlideSpec
from nero_ppt.planning.outline import generate_outline_from_provider
from nero_ppt.planning.scenarios import get_scene
from nero_ppt.planning.slides import (
    apply_page_change,
    generate_specs,
    plan_change,
    specs_revision_payload,
    validate_change_plan,
)
from nero_ppt.providers.base import FakeProvider
from nero_ppt.templates_engine import build_profile, inspect_template

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "assets" / "templates" / "builtin-classic.pptx"
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def profile():
    return build_profile(inspect_template(TEMPLATE), "t")


def _contents():
    from nero_ppt.parsers import parse_document

    return [parse_document((FIX / "sample-notes.txt").read_bytes(), "sample-notes.txt")]


def _outline():
    provider = FakeProvider()
    brief_outline, _ = generate_outline_from_provider(
        provider, Brief(topic="客户调研汇报"), get_scene("general"), _contents())
    return brief_outline


from nero_ppt.contracts import Brief  # noqa: E402


def test_generate_specs_with_budgets(profile):
    """批准大纲 → 逐页规格；槽位预算作为提示传入。"""
    outline = _outline()
    provider = FakeProvider(responses={"SlideSpec": {
        "slide_id": "p-body", "headline": "客户反馈",
        "body_blocks": [{"kind": "bullets", "items": ["周报导出需求", "移动端速度 4.8 秒"]}],
        "visual_intent": "bullets",
        "speaker_notes": {"script": "讲稿：客户反馈整理"},
        "source_refs": [],
        "must_keep_fact_ids": [],
    }})
    specs, usage = generate_specs(provider, outline, _contents(), profile, "general")
    assert len(specs) == len(outline.pages)
    # 每页一次调用
    assert usage.calls == len(outline.pages)
    # 模型未返回的页面使用 Fake 默认最小规格（封面）
    assert all(s.speaker_notes.script for s in specs)


def test_plan_change_page_scope():
    outline = _outline()
    provider = FakeProvider(responses={"修改意见": {
        "scope": "page",
        "items": [{"page_ids": ["p-body"], "action": "regenerate",
                   "instruction": "把第二条要点改成移动端性能"}],
        "affects_outline": False, "affects_template": False, "affects_timing": False,
        "explanation": "仅重写 p-body 页内容",
    }})
    plan = plan_change(provider, "把第二条要点改成移动端性能", outline, target_revision=3)
    assert isinstance(plan, ChangePlan)
    assert plan.scope == "page" and plan.items[0].page_ids == ["p-body"]
    assert plan.target_revision == 3
    assert validate_change_plan(plan, {p.slide_id for p in outline.pages}) == []


def test_plan_change_global_flags():
    outline = _outline()
    provider = FakeProvider(responses={"模板": {
        "scope": "global",
        "items": [{"page_ids": [], "action": "restyle", "instruction": "全部换深色风格"}],
        "affects_outline": False, "affects_template": True, "affects_timing": False,
        "explanation": "模板变化，样张需重新审核",
    }})
    plan = plan_change(provider, "全部换深色模板", outline, 1)
    assert plan.scope == "global" and plan.affects_template
    assert validate_change_plan(plan, {p.slide_id for p in outline.pages}) == []


def test_validate_change_plan_rejects_unknown_pages():
    plan = ChangePlan(scope="page", items=[
        {"page_ids": ["ghost"], "action": "regenerate"}])
    problems = validate_change_plan(plan, {"p1", "p2"})
    assert any("ghost" in p for p in problems)
    global_bad = ChangePlan(scope="global", items=[{"page_ids": [], "action": "restyle"}],
                            affects_template=False, affects_outline=False)
    assert validate_change_plan(global_bad, {"p1"})


def test_apply_page_change_keeps_others(profile):
    """T12 基础：单页修改只替换目标页；无关页是同一对象（内容/哈希不变）。"""
    outline = _outline()
    provider = FakeProvider(responses={"SlideSpec": {
        "slide_id": "p-body", "headline": "改后的标题",
        "body_blocks": [{"kind": "bullets", "items": ["新要点"]}],
        "visual_intent": "bullets",
        "speaker_notes": {"script": "新讲稿"},
    }})
    specs, _ = generate_specs(provider, outline, _contents(), profile, "general")
    before = [s.model_dump_json() for s in specs]
    target = next(s for s in specs if s.slide_id == "p-body")
    others_before = [s.model_dump_json() for s in specs if s.slide_id != "p-body"]
    new_spec = target.model_copy(deep=True)
    new_spec.headline = "局部修改后的标题"
    new_spec.speaker_notes.script = "同步更新的讲稿"
    out, _ = apply_page_change(specs, "p-body", new_spec)
    others_after = [s.model_dump_json() for s in out if s.slide_id != "p-body"]
    assert others_after == others_before  # 无关页逐字节一致
    assert out[[s.slide_id for s in out].index("p-body")].headline == "局部修改后的标题"
    # 旧列表未被破坏（不可变语义）
    assert [s.model_dump_json() for s in specs] == before


def test_apply_page_change_stable_identity():
    """slide_id 必须稳定：改 ID 拒绝（页面身份不得漂移）。"""
    specs = [SlideSpec(slide_id="a", headline="A", speaker_notes={"script": "x"})]
    with pytest.raises(ValueError):
        apply_page_change(specs, "a", SlideSpec(slide_id="b", headline="B", speaker_notes={"script": "x"}))


def test_specs_revision_snapshot(profile):
    outline = _outline()
    provider = FakeProvider(responses={"SlideSpec": {
        "slide_id": "p1", "headline": "快照页", "visual_intent": "bullets",
        "speaker_notes": {"script": "x"},
    }})
    specs, _ = generate_specs(provider, outline, _contents(), profile, "general")
    payload = specs_revision_payload(specs, revision=5, note="第二批")
    assert payload["revision"] == 5 and len(payload["specs"]) == len(specs)
    # 快照可回读
    specs2 = [SlideSpec.model_validate(s) for s in payload["specs"]]
    assert [s.slide_id for s in specs2] == [s.slide_id for s in specs]
