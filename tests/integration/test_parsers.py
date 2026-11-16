"""S08 集成测试：资料解析、可追溯素材、确定性事实、扫描件与编码。"""
import json
from pathlib import Path

import pytest

from nero_ppt.contracts import ParsedContent, from_envelope
from nero_ppt.parsers import ParserError, parse_document

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures"


def test_docx_parse_traceable():
    doc = parse_document((FIX / "sample-report.docx").read_bytes(), "sample-report.docx")
    assert doc.document.parse_status == "ok"
    assert any("4,820" in b.text for b in doc.blocks if b.text)
    # 事实提取：数值+单位
    facts = {(f.normalized_value, f.unit) for f in doc.facts}
    assert ("4,820", "万元") in {(v.replace("，", ","), u) for v, u in facts} or any(
        f.normalized_value == "4,820" and f.unit == "万元" for f in doc.facts)
    # 可追溯：事实指回 block，block 有段落索引 locator
    fact = next(f for f in doc.facts if f.normalized_value == "4,820")
    block = next(b for b in doc.blocks if b.block_id == fact.source_block_ids[0])
    assert "paragraph_index" in block.locator
    # 表格块保留结构
    tables = [b for b in doc.blocks if b.kind == "table"]
    assert tables and tables[0].table[0] == ["指标", "Q2", "Q3"]


def test_pdf_parse_pages_and_facts():
    doc = parse_document((FIX / "sample-report.pdf").read_bytes(), "sample-report.pdf")
    assert doc.document.parse_status == "ok"
    pages = {b.locator["page"] for b in doc.blocks if b.text}
    assert pages and min(pages) == 1
    # PDF 保留页码，不制造其他格式定位
    for b in doc.blocks:
        assert set(b.locator) <= {"page", "part"}
    assert any(f.unit == "%" or f.unit == "万元" for f in doc.facts)


def test_txt_md_line_ranges():
    for name in ("sample-notes.txt", "sample-tech.md"):
        doc = parse_document((FIX / name).read_bytes(), name)
        assert doc.document.parse_status == "ok"
        for b in doc.blocks:
            assert set(b.locator) <= {"start_line", "end_line", "part"}
            assert b.locator["end_line"] >= b.locator["start_line"]
    # MD 事实：12 万（次）/ 78%
    md = parse_document((FIX / "sample-tech.md").read_bytes(), "sample-tech.md")
    assert any(f.normalized_value == "12" and f.unit == "万" for f in md.facts)
    assert any(f.normalized_value == "78" and f.unit == "%" for f in md.facts)
    # TXT 事实：预算 15 万元 / 4.8 秒
    txt = parse_document((FIX / "sample-notes.txt").read_bytes(), "sample-notes.txt")
    vals = {(f.normalized_value, f.unit) for f in txt.facts}
    assert ("4.8", "秒") in vals
    assert ("15", "万元") in vals


def test_pptx_source_parse():
    doc = parse_document((FIX / "sample-deck.pptx").read_bytes(), "sample-deck.pptx")
    assert doc.document.parse_status == "ok"
    assert any("87.5%" in b.text for b in doc.blocks)
    for b in doc.blocks:
        assert "slide_id" in b.locator
    assert any(f.unit == "%" for f in doc.facts)


def test_scanned_pdf_needs_input(tmp_path):
    """无文本层 PDF → needs_input，不返回空内容继续生成。"""
    from PIL import Image

    img_pdf = tmp_path / "scan.pdf"
    Image.new("RGB", (400, 300), (255, 255, 255)).save(img_pdf)  # 纯图片 PDF，无文本层
    doc = parse_document(img_pdf.read_bytes(), "scan.pdf")
    assert doc.document.parse_status == "needs_input"
    assert "扫描" in doc.document.parse_error
    assert all(not b.text for b in doc.blocks)


def test_bad_encoding_reported():
    with pytest.raises(ParserError) as e:
        parse_document(b"\xff\xfe\x00\x01binary", "bad.txt")
    assert "编码" in str(e.value)


def test_gbk_fallback_flagged():
    data = "2026年三季度收入4,820万元".encode("gbk")
    doc = parse_document(data, "gbk.txt")
    assert doc.document.parse_status == "ok"
    assert doc.document.parse_error  # 显式报告自动转码
    assert any(f.normalized_value == "4,820" for f in doc.facts)


def test_unsupported_ext_rejected():
    with pytest.raises(ParserError):
        parse_document(b"x", "file.xlsx")


def test_parsed_content_roundtrip():
    doc = parse_document((FIX / "sample-notes.txt").read_bytes(), "sample-notes.txt")
    data = doc.model_dump(mode="json")
    doc2 = ParsedContent.model_validate(data)
    assert doc2 == doc


def test_conflict_facts_both_kept():
    """冲突口径同时保留（T07 的解析层基础）。"""
    data = "甲口径：收入 100 万元。\n乙口径：收入 120 万元。".encode()
    doc = parse_document(data, "conflict.txt")
    rev_facts = [f for f in doc.facts if f.unit == "万元"]
    values = {f.normalized_value for f in rev_facts}
    assert {"100", "120"} <= values  # 两个都保留，不合并


def test_handmade_spec_remains_valid():
    from nero_ppt.contracts import SlideSpec

    data = json.loads((FIX / "handmade-spec.json").read_text(encoding="utf-8"))
    specs = [from_envelope(item, SlideSpec) for item in data]
    assert len(specs) == 7
