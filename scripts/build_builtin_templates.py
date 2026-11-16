#!/usr/bin/env python3
"""生成内置模板 builtin-classic.pptx。

模板页面即版式来源（source_page_kind=slide），对象使用 nero.* 命名约定，
检查器据此精确识别槽位与固定对象。字体：latin 用 Calibri，中文用 Microsoft YaHei
（渲染端由 fontconfig fallback 到 Noto Sans CJK SC）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

# 16:9
SLIDE_W = 12192000
SLIDE_H = 6858000

THEMES = {
    "classic": {"ink": RGBColor(0x1F, 0x29, 0x37), "accent": RGBColor(0x2B, 0x6C, 0xB0),
                "accent_light": RGBColor(0xD9, 0xE6, 0xF5), "muted": RGBColor(0x64, 0x74, 0x8B)},
    "minimal": {"ink": RGBColor(0x21, 0x21, 0x21), "accent": RGBColor(0x8A, 0x6D, 0x3B),
                "accent_light": RGBColor(0xF3, 0xEC, 0xDF), "muted": RGBColor(0x75, 0x75, 0x75)},
}
INK = ACCENT = ACCENT_LIGHT = MUTED = WHITE = None


def _apply_theme(name: str) -> None:
    global INK, ACCENT, ACCENT_LIGHT, MUTED, WHITE
    t = THEMES[name]
    INK, ACCENT, ACCENT_LIGHT, MUTED = t["ink"], t["accent"], t["accent_light"], t["muted"]
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)

FONT_LATIN = "Calibri"
FONT_EA = "Microsoft YaHei"


def set_run_font(run, size_pt: int, bold: bool = False, color=None) -> None:
    if color is None:
        color = INK
    """同时设置 latin 与 east-asian 字体，避免中文回退到默认宋体。"""
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = FONT_LATIN
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", FONT_EA)


def set_shape_text(shape, text: str, size: int, bold: bool = False,
                   color=None, align=PP_ALIGN.LEFT) -> None:
    if color is None:
        color = INK
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    set_run_font(run, size, bold, color)


def name_shape(shape, name: str) -> None:
    shape.name = name


def add_accent_band(slide, prs) -> None:
    """底部品牌色条：固定装饰对象。"""
    band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(0), Emu(SLIDE_H - 360000), Emu(SLIDE_W), Emu(360000))
    band.fill.solid()
    band.fill.fore_color.rgb = ACCENT
    band.line.fill.background()
    band.shadow.inherit = False
    name_shape(band, "nero.fixed.band")
    return band


def add_side_bar(slide) -> None:
    """左侧竖条装饰。"""
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(0), Emu(0), Emu(180000), Emu(SLIDE_H))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()
    bar.shadow.inherit = False
    name_shape(bar, "nero.fixed.sidebar")


def add_textbox(slide, name: str, x: int, y: int, w: int, h: int,
                text: str, size: int, bold=False, color=None, align=PP_ALIGN.LEFT) -> None:
    if color is None:
        color = INK
    box = slide.shapes.add_textbox(Emu(x), Emu(y), Emu(w), Emu(h))
    name_shape(box, name)
    set_shape_text(box, text, size, bold, color, align)
    return box


def add_title_bar(slide, title: str = "页面标题") -> None:
    """正文页统一标题区。"""
    add_side_bar(slide)
    add_textbox(slide, "nero.title", 900000, 350000, 10400000, 800000, title, 30, bold=True)
    divider = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(900000), Emu(1180000), Emu(10400000), Emu(28000))
    divider.fill.solid()
    divider.fill.fore_color.rgb = ACCENT_LIGHT
    divider.line.fill.background()
    divider.shadow.inherit = False
    name_shape(divider, "nero.fixed.divider")


def _set_layout_meta(slide, purpose: str) -> None:
    """在 notes 中写入机器可读的版式用途元数据，供检查器识别。"""
    slide.notes_slide.notes_text_frame.text = f"nero-layout:{purpose}"


def page_cover(prs) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(0), Emu(0), Emu(SLIDE_W), Emu(SLIDE_H))
    bg.fill.solid()
    bg.fill.fore_color.rgb = ACCENT
    bg.line.fill.background()
    bg.shadow.inherit = False
    name_shape(bg, "nero.fixed.cover_bg")
    # 白色内容面板
    panel = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(1100000), Emu(2100000), Emu(9990000), Emu(2400000))
    panel.fill.solid()
    panel.fill.fore_color.rgb = WHITE
    panel.line.fill.background()
    panel.shadow.inherit = False
    name_shape(panel, "nero.fixed.cover_panel")
    add_textbox(slide, "nero.title", 1500000, 2500000, 9200000, 900000,
                "演示文稿主标题", 40, bold=True, align=PP_ALIGN.CENTER)
    add_textbox(slide, "nero.subtitle", 1500000, 3550000, 9200000, 600000,
                "副标题：汇报人 / 日期", 18, color=MUTED, align=PP_ALIGN.CENTER)
    _set_layout_meta(slide, "cover")


def page_section(prs) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_side_bar(slide)
    add_textbox(slide, "nero.section_no", 1400000, 2200000, 2200000, 1400000,
                "01", 60, bold=True, color=ACCENT)
    add_textbox(slide, "nero.title", 1400000, 3700000, 8800000, 900000,
                "章节标题", 32, bold=True)
    add_accent_band(slide, prs)
    _set_layout_meta(slide, "section")


def page_bullets(prs) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_title_bar(slide, "要点页标题")
    body = add_textbox(slide, "nero.body", 900000, 1450000, 10400000, 4600000,
                       "第一个要点", 16)
    tf = body.text_frame
    tf.vertical_anchor = MSO_ANCHOR.TOP
    # 预置 3 个要点段落，形成可复制的样式
    for text in ("第二个要点", "第三个要点"):
        p = tf.add_paragraph()
        r = p.add_run()
        r.text = text
        set_run_font(r, 16)
    add_accent_band(slide, prs)
    _set_layout_meta(slide, "bullets")


def page_two_column(prs) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_title_bar(slide, "双栏页标题")
    left_panel = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(900000), Emu(1500000), Emu(5020000), Emu(4400000))
    left_panel.fill.solid()
    left_panel.fill.fore_color.rgb = ACCENT_LIGHT
    left_panel.line.fill.background()
    left_panel.shadow.inherit = False
    name_shape(left_panel, "nero.fixed.left_panel")
    right_panel = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(6180000), Emu(1500000), Emu(5020000), Emu(4400000))
    right_panel.fill.solid()
    right_panel.fill.fore_color.rgb = RGBColor(0xEF, 0xF3, 0xF8)
    right_panel.line.fill.background()
    right_panel.shadow.inherit = False
    name_shape(right_panel, "nero.fixed.right_panel")
    add_textbox(slide, "nero.left", 1100000, 1700000, 4620000, 4000000, "左栏内容", 15)
    add_textbox(slide, "nero.right", 6380000, 1700000, 4620000, 4000000, "右栏内容", 15)
    add_accent_band(slide, prs)
    _set_layout_meta(slide, "two_column")


def page_table(prs) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_title_bar(slide, "表格页标题")
    gfx = slide.shapes.add_table(4, 3, Emu(900000), Emu(1500000), Emu(10400000), Emu(4200000))
    gfx.name = "nero.table"
    tbl = gfx.table
    headers = ["指标", "数值", "说明"]
    for j, h in enumerate(headers):
        cell = tbl.cell(0, j)
        cell.text = ""
        r = cell.text_frame.paragraphs[0].add_run()
        r.text = h
        set_run_font(r, 14, bold=True, color=WHITE)
        cell.fill.solid()
        cell.fill.fore_color.rgb = ACCENT
    for i in range(1, 4):
        for j in range(3):
            cell = tbl.cell(i, j)
            cell.text = ""
            r = cell.text_frame.paragraphs[0].add_run()
            r.text = f"示例{i}-{j+1}"
            set_run_font(r, 13)
    add_accent_band(slide, prs)
    _set_layout_meta(slide, "table")


def page_chart(prs) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_title_bar(slide, "图表页标题")
    data = CategoryChartData()
    data.categories = ["一季度", "二季度", "三季度", "四季度"]
    data.add_series("示例系列", (20, 35, 30, 45))
    gfx = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED,
                                 Emu(900000), Emu(1500000), Emu(10400000), Emu(4300000), data)
    gfx.name = "nero.chart"
    chart = gfx.chart
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    add_accent_band(slide, prs)
    _set_layout_meta(slide, "chart")


def page_image_text(prs, image_path: Path | None = None) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_title_bar(slide, "图文页标题")
    if image_path is None:
        # 运行时生成临时占位图
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            image_path = Path(f.name)
        _make_placeholder_image(image_path)
    pic = slide.shapes.add_picture(str(image_path), Emu(900000), Emu(1500000), Emu(5400000), Emu(4050000))
    name_shape(pic, "nero.image")
    add_textbox(slide, "nero.body", 6600000, 1600000, 4600000, 3900000, "图片说明要点", 15)
    add_accent_band(slide, prs)
    _set_layout_meta(slide, "image_text")


def _make_placeholder_image(path: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 600), (217, 230, 245))
    d = ImageDraw.Draw(img)
    for i in range(600):
        d.line([(0, i), (800, i)], fill=(217 - i // 8, 230 - i // 12, 245))
    d.rectangle([100, 150, 700, 450], outline=(43, 108, 176), width=8)
    d.text((300, 280), "IMAGE", fill=(43, 108, 176))
    img.save(path)


def page_process(prs) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_title_bar(slide, "流程页标题")
    steps = ["准备", "执行", "检查", "交付"]
    box_w, gap = 2300000, 360000
    x0 = (SLIDE_W - (box_w * 4 + gap * 3)) // 2
    y = 2700000
    for i, s in enumerate(steps):
        box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                     Emu(x0 + i * (box_w + gap)), Emu(y), Emu(box_w), Emu(1100000))
        box.fill.solid()
        box.fill.fore_color.rgb = ACCENT if i % 2 == 0 else ACCENT_LIGHT
        if i % 2 == 0:
            box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
            r = box.text_frame.paragraphs[0].add_run()
            r.text = s
            set_run_font(r, 15, bold=True, color=WHITE)
        else:
            box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
            r = box.text_frame.paragraphs[0].add_run()
            r.text = s
            set_run_font(r, 15, bold=True, color=INK)
        box.line.fill.background()
        box.shadow.inherit = False
        name_shape(box, f"nero.step{i}")
        if i < 3:
            arrow = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW,
                                           Emu(x0 + i * (box_w + gap) + box_w + 60000),
                                           Emu(y + 400000), Emu(gap - 120000), Emu(300000))
            arrow.fill.solid()
            arrow.fill.fore_color.rgb = MUTED
            arrow.line.fill.background()
            arrow.shadow.inherit = False
            name_shape(arrow, f"nero.fixed.arrow{i}")
    add_accent_band(slide, prs)
    _set_layout_meta(slide, "process")


def page_summary(prs) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_title_bar(slide, "总结页标题")
    body = add_textbox(slide, "nero.body", 900000, 1500000, 10400000, 4300000, "总结要点一", 16)
    for text in ("总结要点二", "下一步计划"):
        p = body.text_frame.add_paragraph()
        r = p.add_run()
        r.text = text
        set_run_font(r, 16)
    add_accent_band(slide, prs)
    _set_layout_meta(slide, "summary")


def build(out_path: Path, theme: str = "classic") -> Path:
    _apply_theme(theme)
    prs = Presentation()
    prs.slide_width = Emu(SLIDE_W)
    prs.slide_height = Emu(SLIDE_H)
    for page in (page_cover, page_section, page_bullets, page_two_column,
                 page_table, page_chart, page_process, page_summary, page_image_text):
        page(prs)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out_path)
    return out_path


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    theme = "minimal" if (len(sys.argv) > 2 and sys.argv[2] == "minimal") else "classic"
    if out is None:
        out = Path(f"assets/templates/builtin-{theme}.pptx")
    path = build(out, theme)
    print(f"written: {path} (theme={theme})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
