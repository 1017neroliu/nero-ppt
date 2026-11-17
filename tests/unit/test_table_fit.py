"""表格垂直自适应单元测试。"""
from __future__ import annotations

import pytest
from pptx import Presentation
from pptx.util import Emu, Pt

from nero_ppt.templates_engine.ooxml import fit_table, set_table_data


def _make_table_frame(prs, n_rows: int, shape_h_emu: int, top_emu: int, long_text: str = "确认应用需求与数据定义口径",
                      row_h_emu: int | None = None):
    """构造一个带表格的 graphicFrame；row_h_emu 模拟 set_table_data 复制模板行高。"""
    from pptx.util import Inches
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # 空白版式
    gf = slide.shapes.add_table(n_rows, 3, Emu(top_emu), Emu(top_emu),
                                Inches(8), Emu(shape_h_emu))
    tbl = gf.table
    for r in range(n_rows):
        if row_h_emu:
            tbl.rows[r].height = Emu(row_h_emu)  # 注意：row.height setter 会联动扩 frame 高
        for c in range(3):
            tbl.cell(r, c).text = long_text if c == 1 else f"R{r}C{c}"
    gf.height = Emu(shape_h_emu)  # 最后还原 frame 为模板形状高（真实链路 frame 高来自模板 XML）
    return gf


def test_fit_table_compresses_overflow(tmp_path):
    """复现 6.1-sol 任务 p12：8 行 × 模板行高 0.405in = 3.24in > 形状 2.32in，超出页面。"""
    prs = Presentation()
    prs.slide_height = Emu(6858000)  # 7.5in
    gf = _make_table_frame(prs, 8, shape_h_emu=2120900, top_emu=3987936,
                           row_h_emu=370840)
    info = fit_table(gf, prs.slide_height)
    assert info is not None
    rows = list(gf.table.rows)
    total = sum(r.height for r in rows)
    assert total <= 2120900, "压缩后总高不得超过形状高度"
    # 页面底沿校验：表格底不超页高
    assert gf.top + total <= prs.slide_height
    assert info["rows"] == 8


def test_fit_table_shrinks_font_when_needed(tmp_path):
    prs = Presentation()
    prs.slide_height = Emu(6858000)
    long_text = "确认应用需求、数据定义及验收口径；需要基线与验收清单支撑完成确认"
    gf = _make_table_frame(prs, 8, shape_h_emu=2120900, top_emu=3987936, long_text=long_text,
                           row_h_emu=370840)
    info = fit_table(gf, prs.slide_height, min_font_pt=10.0, default_font_pt=14.0)
    assert info is not None
    assert info["changed_font"] is True
    assert info["font_pt"] <= 12, f"长文字应触发缩字号，实际 {info['font_pt']}"
    # 所有单元格 run 写入显式字号
    for r in gf.table.rows:
        for c in r.cells:
            for p in c.text_frame.paragraphs:
                for run in p.runs:
                    assert run.font.size is not None


def test_fit_table_noop_when_within_bounds():
    prs = Presentation()
    prs.slide_height = Emu(6858000)
    gf = _make_table_frame(prs, 3, shape_h_emu=2120900, top_emu=3987936, long_text="短")
    assert fit_table(gf, prs.slide_height) is None  # 行少不溢出，不干预
