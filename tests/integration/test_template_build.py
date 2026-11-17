"""S03 门槛 A 集成测试：确定性 Builder。

验收点：可编辑文本/图片/表格/图表；固定对象几何保留；关系无悬空；
多页同图表样例互不污染（T02）；备注可读回。
"""
import json
import zipfile
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Emu

from nero_ppt.contracts import ChartBlock, SlideSpec, from_envelope
from nero_ppt.rendering.builder import BuildError, build_deck, build_from_specs
from nero_ppt.templates_engine import build_profile, inspect_template

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "assets" / "templates" / "builtin-classic.pptx"
HANDMADE = ROOT / "tests" / "fixtures" / "handmade-spec.json"
TMP = ROOT / "data" / "tmp-build"


@pytest.fixture(scope="module")
def profile():
    return build_profile(inspect_template(TEMPLATE), "builtin-classic", source_file=str(TEMPLATE))


@pytest.fixture(scope="module")
def handmade_specs():
    data = json.loads(HANDMADE.read_text(encoding="utf-8"))
    return [from_envelope(item, SlideSpec) for item in data]


@pytest.fixture(scope="module")
def built(profile, handmade_specs):
    out = TMP / "full-build.pptx"
    result, plans = build_from_specs(TEMPLATE, profile, handmade_specs, out, tmp_dir=TMP)
    return result, plans, out


def test_build_success(built, handmade_specs):
    result, plans, out = built
    assert Path(result.output_path).exists() and Path(result.output_path).stat().st_size > 0
    assert result.slide_count == len(handmade_specs) == 7


def test_output_opens_and_order(built):
    _, _, out = built
    prs = Presentation(str(out))
    assert len(prs.slides.__iter__.__self__._sldIdLst) == 7


def test_text_editable_and_content(built):
    """第 1 页（bullets）：标题与要点写入且可编辑。"""
    _, _, out = built
    prs = Presentation(str(out))
    slide = prs.slides[1]
    title = next(sh for sh in slide.shapes if sh.name == "nero.title")
    assert "核心经营指标" in title.text_frame.text
    body = next(sh for sh in slide.shapes if sh.name == "nero.body")
    text = body.text_frame.text
    assert "4,820" in text and "91.5%" in text
    # 富文本保留：首 run 有字体设置且非全框替换破坏
    runs = [r for p in body.text_frame.paragraphs for r in p.runs]
    assert len(runs) >= 3
    assert all(r.font.size is not None for r in runs)


def test_table_data_roundtrip(built):
    _, _, out = built
    prs = Presentation(str(out))
    slide = prs.slides[2]  # table-1
    gfx = next(sh for sh in slide.shapes if sh.name == "nero.table")
    table = gfx.table
    assert table.cell(0, 0).text == "指标"
    assert table.cell(1, 2).text == "4,820"
    assert table.cell(2, 1).text == "39.3%"


def test_chart_data_roundtrip(built):
    _, _, out = built
    prs = Presentation(str(out))
    slide = prs.slides[3]  # chart-1
    gfx = next(sh for sh in slide.shapes if sh.name == "nero.chart")
    chart = gfx.chart
    cats = list(chart.plots[0].categories)
    assert cats == ["Q1", "Q2", "Q3", "Q4目标"]
    values = list(chart.series[0].values)
    assert values == [3950, 4288, 4820, 5500]


def test_t02_two_charts_no_pollution(profile, tmp_path):
    """同模板两页图表：改一页数据不污染另一页。"""
    specs = [
        SlideSpec(slide_id="c1", headline="图表 A", visual_intent="chart_bar",
                  body_blocks=[{"kind": "chart", "categories": ["x", "y"],
                                "series": [{"name": "A", "values": [1, 2]}]}],
                  speaker_notes={"script": "A"}),
        SlideSpec(slide_id="c2", headline="图表 B", visual_intent="chart_bar",
                  body_blocks=[{"kind": "chart", "categories": ["m", "n"],
                                "series": [{"name": "B", "values": [77, 88]}]}],
                  speaker_notes={"script": "B"}),
    ]
    out = tmp_path / "two-charts.pptx"
    build_from_specs(TEMPLATE, profile, specs, out, tmp_dir=tmp_path)
    prs = Presentation(str(out))
    c1 = next(sh for sh in prs.slides[0].shapes if sh.has_chart).chart
    c2 = next(sh for sh in prs.slides[1].shapes if sh.has_chart).chart
    assert list(c1.plots[0].categories) == ["x", "y"]
    assert list(c2.plots[0].categories) == ["m", "n"]
    assert list(c1.series[0].values) == [1, 2]
    assert list(c2.series[0].values) == [77, 88]


def test_fixed_objects_geometry_preserved(built):
    """固定装饰（侧条、色条）结构与几何保留。"""
    _, _, out = built
    prs = Presentation(str(out))
    slide = prs.slides[1]
    sidebar = next(sh for sh in slide.shapes if sh.name == "nero.fixed.sidebar")
    assert (sidebar.left, sidebar.top, sidebar.width, sidebar.height) == (0, 0, 180000, 6858000)


def test_notes_readable(built):
    _, _, out = built
    prs = Presentation(str(out))
    notes = prs.slides[1].notes_slide.notes_text_frame.text
    assert "收入环比增长" in notes
    assert "【建议时长】" in notes


def test_no_dangling_relationships(built):
    """包内 slide 的所有内部关系目标均存在（无悬空）。"""
    import os
    import re

    _, _, out = built
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        slide_names = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        assert len(slide_names) == 7
        for sn in slide_names:
            rels_name = sn.replace("ppt/slides/", "ppt/slides/_rels/") + ".rels"
            assert rels_name in names, f"{sn} 缺少 rels"
            rels_xml = z.read(rels_name).decode("utf-8")
            for target in re.findall(r'Target="([^"]+)"', rels_xml):
                if target.startswith("http"):
                    continue
                normalized = os.path.normpath(os.path.join(os.path.dirname(sn), target))
                assert normalized in names, f"{sn} 引用 {target} 不存在"


def test_t02_image_page_replace(profile, tmp_path):
    """图片槽位替换：使用传入资源。"""
    from PIL import Image

    img_path = tmp_path / "res.png"
    Image.new("RGB", (400, 300), (200, 100, 50)).save(img_path)
    spec = SlideSpec(
        slide_id="img1", headline="图文页",
        body_blocks=[
            {"kind": "image", "resource_id": "res-1"},
            {"kind": "bullets", "items": ["图片说明"]},
        ],
        visual_intent="image_text",
        speaker_notes={"script": "图示。"},
    )
    out = tmp_path / "img-page.pptx"
    build_from_specs(TEMPLATE, profile, [spec], out,
                     resource_map={"res-1": str(img_path)}, tmp_dir=tmp_path)
    prs = Presentation(str(out))
    pic = next(sh for sh in prs.slides[0].shapes if sh.name == "nero.image")
    assert pic.image.size == (400, 300)


def test_process_page_steps(built):
    _, _, out = built
    prs = Presentation(str(out))
    slide = prs.slides[5]  # process-1
    texts = []
    for sh in slide.shapes:
        if sh.name.startswith("nero.step"):
            texts.append(sh.text_frame.text)
    assert texts == ["需求冻结", "批量交付", "验收测试", "复盘归档"]


def test_binding_error_too_many_steps(profile):
    """流程步骤超出槽位：显式报错，不静默截断。"""
    spec = SlideSpec(
        slide_id="p9", headline="流程",
        body_blocks=[{"kind": "process", "steps": ["1", "2", "3", "4", "5", "6"]}],
        visual_intent="process",
        speaker_notes={"script": "x"},
    )
    from nero_ppt.templates_engine.binding import BindingError

    with pytest.raises(BindingError):
        build_from_specs(TEMPLATE, profile, [spec], TMP / "bad-process.pptx", tmp_dir=TMP)


def test_pptx_archive_integrity(built):
    _, _, out = built
    with zipfile.ZipFile(out) as z:
        bad = z.testzip()
        assert bad is None


def test_potx_normalize(tmp_path):
    """POTX 包类型规范化：main content type 转为 presentation。"""
    import zipfile

    src = tmp_path / "tpl.potx"
    with zipfile.ZipFile(TEMPLATE) as zin, zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in zin.namelist():
            data = zin.read(n)
            if n == "[Content_Types].xml":
                data = data.replace(
                    b"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
                    b"application/vnd.openxmlformats-officedocument.presentationml.template.main+xml")
            zout.writestr(n, data)
    out = tmp_path / "normalized.pptx"
    from nero_ppt.templates_engine.ooxml import normalize_potx_to_pptx

    normalize_potx_to_pptx(str(src), str(out))
    with zipfile.ZipFile(out) as z:
        assert b"presentationml.presentation.main+xml" in z.read("[Content_Types].xml")
    prs = Presentation(str(out))  # 能正常打开
    assert len(prs.slides._sldIdLst) == 9


def test_external_template_bullets_fallback(tmp_path):
    """外部模板无 bullets 专用版式时：回退到有正文槽位的版式（真实用户模板场景）。"""
    from nero_ppt.contracts import Layout, Slot, TemplateProfile
    from nero_ppt.rendering.builder import build_from_specs

    profile = TemplateProfile(
        template_id="user-tpl", file_hash="h" * 64, file_ext=".pptx",
        slide_width_emu=12192000, slide_height_emu=6858000,
        layouts=[
            Layout(layout_id="cover", purpose="cover", source_page_kind="slide",
                   source_slide_id=256, supported_intents=["title"],
                   slots=[Slot(slot_id="title0", object_id=2, kind="text",
                               bounds_emu=(1000000, 2000000, 9000000, 1000000))]),
            Layout(layout_id="sec1", purpose="section", source_page_kind="slide",
                   source_slide_id=257,
                   slots=[Slot(slot_id="title0", object_id=2, kind="text",
                               bounds_emu=(1000000, 800000, 9000000, 800000)),
                          Slot(slot_id="text1", object_id=3, kind="text",
                               bounds_emu=(1000000, 1800000, 9000000, 4200000),
                               text_budget_chars=399)]),
        ])
    spec = SlideSpec(slide_id="b1", headline="要点页", visual_intent="bullets",
                     body_blocks=[{"kind": "bullets", "items": ["要点一", "要点二", "要点三"]}],
                     speaker_notes={"script": "x"})
    out = tmp_path / "fallback.pptx"
    result, plans = build_from_specs(TEMPLATE, profile, [spec], out, tmp_dir=tmp_path)
    assert result.slide_count == 1
    assert plans[0].layout_id == "sec1"  # 回退到正文槽位最大的版式
    assert any("回退" in w for w in result.warnings)


def test_title_font_fit_on_overflow(tmp_path):
    """超长标题渲染时自动缩字号，保证单行放下（复现 6.1-sol 任务出格场景）。

    外部模板标题槽无 run 显式字号（继承母版字号），超长标题此前直接写入后
    折行溢出占位符；现在绑定层标 fit_font，渲染层按生效字号估算并缩小。
    """

    from nero_ppt.contracts import Layout, Slot, TemplateProfile
    from nero_ppt.rendering.builder import build_from_specs
    from nero_ppt.templates_engine.ooxml import est_text_width_emu

    # 标题槽：宽 9in（去掉内边距后 ≈8.8in）；无预算/字号信息，模拟外部模板提取结果
    profile = TemplateProfile(
        template_id="fit-tpl", file_hash="f" * 64, file_ext=".pptx",
        slide_width_emu=12192000, slide_height_emu=6858000,
        layouts=[
            Layout(layout_id="sec1", purpose="section", source_page_kind="slide",
                   source_slide_id=257,
                   slots=[Slot(slot_id="title0", object_id=4, kind="text",
                               bounds_emu=(1400000, 3700000, 8800000, 900000)),
                          Slot(slot_id="text1", object_id=3, kind="text",
                               bounds_emu=(1000000, 1800000, 9000000, 4200000),
                               text_budget_chars=399)]),
        ])
    headline = "EtherCAT具备实时控制价值，年度成果需以工程验证闭环及实测证据支撑"  # 33 字，36pt 下超宽
    spec = SlideSpec(slide_id="b1", headline=headline, visual_intent="bullets",
                     body_blocks=[{"kind": "bullets", "items": ["要点一", "要点二", "要点三"]}],
                     speaker_notes={"script": "x"})
    out = tmp_path / "fit.pptx"
    result, plans = build_from_specs(TEMPLATE, profile, [spec], out, tmp_dir=tmp_path)
    assert result.slide_count == 1

    prs = Presentation(str(out))
    slide = prs.slides[0]
    title = next(sh for sh in slide.shapes if sh.shape_id == 4)
    run = title.text_frame.paragraphs[0].runs[0]
    assert run.text == headline  # 文本不删减
    assert run.font.size is not None, "超宽标题应写入显式缩字字号"
    to_pt = run.font.size.pt
    avail = title.width - 2 * 91440
    assert est_text_width_emu(headline, to_pt) <= avail, "缩字后应单行放下"
    assert to_pt >= 12.0, "不低于可读下限"
    assert any("自适应" in w for w in result.warnings)
    # 内容写入 text1 槽位
    prs = Presentation(str(out))
    body = next(sh for sh in prs.slides[0].shapes if sh.shape_id == 3)
    assert "要点三" in body.text_frame.text
