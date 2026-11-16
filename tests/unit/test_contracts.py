"""协议层单元测试：严格模式、坐标拒绝、联合类型、来源引用。"""
import pytest
from pydantic import ValidationError

from nero_ppt.contracts import (
    Brief,
    Outline,
    OutlinePage,
    QAReport,
    RenderPlan,
    SlideSpec,
    Slot,
    SpeakerNotes,
    TableBlock,
    TextBullets,
    envelope,
)
from nero_ppt.contracts.common import SCHEMA_VERSION


def _spec(**over):
    base = dict(
        slide_id="s1",
        headline="标题",
        body_blocks=[{"kind": "bullets", "items": ["要点一", "要点二"]}],
        speaker_notes={"script": "口述内容"},
    )
    base.update(over)
    return base


def test_envelope_carries_schema_version():
    spec = SlideSpec(**_spec())
    data = envelope(spec)
    assert data["schema_version"] == SCHEMA_VERSION


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        SlideSpec(**_spec(some_unknown_field=1))


def test_coordinate_fields_rejected():
    """协议禁止 x/y/w/h —— 未声明字段全部被 extra=forbid 拒绝。"""
    for coord in ("x", "y", "w", "h", "width", "height"):
        with pytest.raises(ValidationError):
            SlideSpec(**_spec(**{coord: 100}))


def test_bullets_and_table_union():
    spec = SlideSpec(**_spec(body_blocks=[
        {"kind": "bullets", "items": ["a"]},
        {"kind": "table", "headers": ["列1"], "rows": [["1"]]},
    ]))
    assert isinstance(spec.body_blocks[0], TextBullets)
    assert isinstance(spec.body_blocks[1], TableBlock)


def test_unknown_block_kind_rejected():
    with pytest.raises(ValidationError):
        SlideSpec(**_spec(body_blocks=[{"kind": "free_canvas"}]))


def test_notes_required():
    with pytest.raises(ValidationError):
        SlideSpec(**_spec(speaker_notes=None))


def test_headline_length_limit():
    with pytest.raises(ValidationError):
        SlideSpec(**_spec(headline="长" * 201))


def test_slot_bounds_emu_integers_only():
    with pytest.raises(ValidationError):
        Slot(slot_id="t", object_id=2, kind="text", bounds_emu=(0, 0, 1.5, 100))
    s = Slot(slot_id="t", object_id=2, kind="text", bounds_emu=(0, 0, 914400, 914400))
    assert s.bounds_emu == (0, 0, 914400, 914400)


def test_qa_report_status_distinction():
    from nero_ppt.contracts import QACheck

    report = QAReport(slide_id="s1", checks=[
        QACheck(check_id="c1", check_name="overflow", status="fail", severity="error"),
        QACheck(check_id="c2", check_name="visual", status="not_checked", severity="warning"),
    ])
    assert len(report.failed()) == 1
    assert len(report.blocking()) == 1
    assert report.has_not_checked()
    # 非法状态被拒绝
    with pytest.raises(ValidationError):
        QACheck(check_id="c3", check_name="x", status="unknown", severity="error")


def test_render_plan_no_model_coords():
    plan = RenderPlan(slide_id="s1", layout_id="cover",
                      slot_bindings=[{"slot_id": "title", "object_id": 2, "source": "constant",
                                      "constant_text": "标题"}])
    assert plan.slot_bindings[0].edit_action == "set_text"


def test_outline_validation():
    outline = Outline(
        brief=Brief(topic="主题"),
        pages=[OutlinePage(slide_id="p1", title="第一页")],
    )
    assert outline.pages[0].target_seconds == 45
    with pytest.raises(ValidationError):
        Outline(brief=Brief(topic="主题"), pages=[])
