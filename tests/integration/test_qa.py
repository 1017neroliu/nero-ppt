"""S05 门槛 B 集成测试：规则 QA 与有界修复。"""
import json
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Pt

from nero_ppt.contracts import SlideSpec, from_envelope
from nero_ppt.qa import run_qa, run_repair_loop
from nero_ppt.rendering.builder import build_from_specs
from nero_ppt.templates_engine import build_profile, inspect_template

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "assets" / "templates" / "builtin-classic.pptx"
HANDMADE = ROOT / "tests" / "fixtures" / "handmade-spec.json"
TMP = ROOT / "data" / "tmp-qa"


@pytest.fixture(scope="module")
def profile():
    return build_profile(inspect_template(TEMPLATE), "builtin-classic", source_file=str(TEMPLATE))


@pytest.fixture(scope="module")
def specs():
    data = json.loads(HANDMADE.read_text(encoding="utf-8"))
    return [from_envelope(item, SlideSpec) for item in data]


@pytest.fixture(scope="module")
def clean_build(profile, specs):
    out = TMP / "clean.pptx"
    result, plans = build_from_specs(TEMPLATE, profile, specs, out, tmp_dir=TMP)
    return out, plans


def test_clean_deck_passes(profile, specs, clean_build):
    """正常构建通过 QA（来源已知时）。"""
    out, plans = clean_build
    reports = run_qa(out, specs, profile, plans,
                     known_source_ids={"doc-1"},
                     fact_values={"f-rev": "4,820", "f-margin": "38.2"})
    blocking = [c for r in reports for c in r.blocking()]
    assert blocking == [], [c.check_id + c.observed for c in blocking]


def test_source_missing_caught(profile, specs, clean_build):
    out, plans = clean_build
    reports = run_qa(out, specs, profile, plans, known_source_ids=set())
    fails = [c for r in reports for c in r.failed() if c.check_id == "source_missing"]
    assert len(fails) == 1 and fails[0].severity == "error"


def test_must_keep_fact_absent_warns(profile, specs, clean_build):
    out, plans = clean_build
    reports = run_qa(out, specs, profile, plans,
                     known_source_ids={"doc-1"}, fact_values={})
    warns = [c for r in reports for c in r.failed()
             if c.check_id == "must_keep_fact_absent"]
    assert len(warns) >= 1 and warns[0].severity == "warning"


def test_placeholder_leftover_detected(profile, tmp_path):
    """故意残留样例被捕获：标题保持模板示例文字。"""
    spec = SlideSpec(slide_id="bad1", headline="要点页标题",  # 与内置模板示例标题相同
                     speaker_notes={"script": "x"})
    out = tmp_path / "leftover.pptx"
    result, plans = build_from_specs(TEMPLATE, profile, [spec], out, tmp_dir=tmp_path)
    reports = run_qa(out, [spec], profile, plans)
    fails = [c for r in reports for c in r.failed() if c.check_id == "placeholder_leftover"]
    assert fails, "应检测到'要点页标题'残留"


def test_overflow_risk_warning(profile, tmp_path):
    """超长标题触发容量风险（warning 级预筛）。"""
    spec = SlideSpec(slide_id="long1", headline="极" * 120,
                     speaker_notes={"script": "x"})
    out = tmp_path / "long.pptx"
    _, plans = build_from_specs(TEMPLATE, profile, [spec], out, tmp_dir=tmp_path)
    reports = run_qa(out, [spec], profile, plans)
    risks = [c for r in reports for c in r.failed() if c.check_id == "overflow_risk"]
    assert risks and risks[0].severity == "warning"


def test_notes_missing_detected(profile, tmp_path):
    """Notes 被清空后检查发现（error）。"""
    spec = SlideSpec(slide_id="n1", headline="正常标题", speaker_notes={"script": "讲稿"})
    out = tmp_path / "notes.pptx"
    _, plans = build_from_specs(TEMPLATE, profile, [spec], out, tmp_dir=tmp_path)
    prs = Presentation(str(out))
    prs.slides[0].notes_slide.notes_text_frame.clear()
    prs.save(str(out))
    reports = run_qa(out, [spec], profile, plans)
    fails = [c for r in reports for c in r.failed() if c.check_id == "notes_missing"]
    assert fails and fails[0].severity == "error"


def test_t13_repair_loop_stops(profile, specs, tmp_path):
    """无法自动修复的阻塞问题：最多轮数后停止，不无限循环。"""
    spec = SlideSpec(slide_id="s1", headline="要点页标题",  # 残留，不可自动修复
                     speaker_notes={"script": "x"})
    out = tmp_path / "stuck.pptx"
    _, plans = build_from_specs(TEMPLATE, profile, [spec], out, tmp_dir=tmp_path)
    reports, still = run_repair_loop(out, [spec], profile, plans, max_rounds=2)
    assert still  # 残留问题仍在
    rounds = [rr for r in reports for rr in r.repair_rounds]
    assert all(rr.round_index <= 2 for rr in rounds), "不得超过 2 轮"
    assert any(rr.result in ("stopped", "no_change") for rr in rounds)


def test_repair_lift_respects_single_line(profile, tmp_path):
    """tiny_text 提升不得撤销标题 fit 缩字：提升后单行放不下时保持 fit 结果。

    复现 6.1-sol 任务：fit 将 36pt 缩至 26pt 单行放下，repair 按槽位软下限
    27pt 提回后重新超宽出格。
    """
    from pptx.util import Pt

    from nero_ppt.qa.repair import repair_slide
    from nero_ppt.templates_engine.ooxml import est_text_width_emu

    layout = next(l for l in profile.layouts if l.layout_id == "bullets")
    title_slot = next(s for s in layout.slots if s.slot_id == "nero.title")
    spec = SlideSpec(slide_id="s1", headline="要点页标题",
                     speaker_notes={"script": "x"})
    out = tmp_path / "fit-repair.pptx"
    _, plans = build_from_specs(TEMPLATE, profile, [spec], out, tmp_dir=tmp_path)

    prs = Presentation(str(out))
    slide = prs.slides[0]
    title = next(sh for sh in slide.shapes if sh.shape_id == title_slot.object_id)
    run = next(r for p in title.text_frame.paragraphs for r in p.runs)
    run.font.size = Pt(26)  # 模拟 fit 结果：低于软下限但单行放下
    prs.save(str(out))

    repair_slide(str(out), 0, profile, layout)

    prs2 = Presentation(str(out))
    title2 = next(sh for sh in prs2.slides[0].shapes if sh.shape_id == title_slot.object_id)
    run2 = next(r for p in title2.text_frame.paragraphs for r in p.runs)
    est = est_text_width_emu(run2.text, run2.font.size.pt)
    avail = title2.width - 2 * 91440
    if est > avail:
        assert run2.font.size.pt == 26, "提升会重新溢出时必须保持 fit 字号"
    else:
        # 本例标题短，提升到下限仍单行放下 → 允许提升
        assert run2.font.size.pt >= min(27, title_slot.min_font_pt)


def test_report_hash_matches_artifact(profile, specs, clean_build):
    out, plans = clean_build
    import hashlib

    reports = run_qa(out, specs, profile, plans)
    real = hashlib.sha256(Path(out).read_bytes()).hexdigest()
    assert all(r.artifact_hash == real for r in reports)


def test_fixed_card_not_flagged(profile, specs, clean_build):
    """正常底色卡片（two_column 面板）不误判为越界/遮挡。"""
    out, plans = clean_build
    reports = run_qa(out, specs, profile, plans, known_source_ids={"doc-1"})
    geo = [c for r in reports for c in r.failed() if c.check_id == "out_of_bounds"]
    assert geo == []
