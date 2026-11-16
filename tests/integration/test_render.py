"""S04 集成测试：LibreOffice 转换、Poppler 缩略图、超时与校验。"""
import shutil
import signal
import time
from pathlib import Path

import pytest

from nero_ppt.rendering.libreoffice import (
    ConversionError,
    convert_to_pdf,
    extract_pdf_text,
    pdf_page_count,
    run_soffice,
)
from nero_ppt.rendering.preview import render_thumbnails

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "assets" / "templates" / "builtin-classic.pptx"


@pytest.fixture(scope="module")
def build_pptx(tmp_path_factory):
    """自行构建渲染输入（不依赖其他测试的产物）。"""
    import json

    from nero_ppt.contracts import SlideSpec, from_envelope
    from nero_ppt.rendering.builder import build_from_specs
    from nero_ppt.templates_engine import build_profile, inspect_template

    out = tmp_path_factory.mktemp("src") / "in.pptx"
    profile = build_profile(inspect_template(TEMPLATE), "t")
    specs = [
        SlideSpec(slide_id="c1", headline="标题一", speaker_notes={"script": "n1"}),
        SlideSpec(slide_id="b1", headline="标题二", visual_intent="bullets",
                  body_blocks=[{"kind": "bullets", "items": ["要点一", "要点二"]}],
                  speaker_notes={"script": "n2"}),
        SlideSpec(slide_id="t1", headline="表格页", visual_intent="table",
                  body_blocks=[{"kind": "table", "headers": ["a", "b", "c"],
                                "rows": [["1", "2", "3"]]}],
                  speaker_notes={"script": "n3"}),
    ]
    build_from_specs(TEMPLATE, profile, specs, out, tmp_dir=out.parent)
    return out


@pytest.fixture(scope="module")
def converted(tmp_path_factory, build_pptx):
    work = tmp_path_factory.mktemp("lo-work")
    final = work / "final.pdf"
    return convert_to_pdf(build_pptx, work / "transient", timeout=180, out_path=final)


def test_pdf_pages_match_pptx(converted, build_pptx):
    """PPTX 与 PDF 页数对应。"""
    from pptx import Presentation

    expected = len(Presentation(str(build_pptx)).slides._sldIdLst)
    assert converted.pages == expected == 3


def test_chinese_renders_no_missing_glyphs(converted):
    """中文样例真实渲染无缺字（文本层提取验证）。"""
    text = extract_pdf_text(converted.pdf_path, max_pages=7)
    assert "标题一" in text and "标题二" in text and "表格页" in text


def test_hash_and_size(converted):
    assert len(converted.file_hash) == 64
    assert converted.byte_size > 1000
    assert converted.duration_seconds < 180
    assert converted.pdf_path.name == "final.pdf"


def test_thumbnails_from_same_pdf(converted, tmp_path):
    """缩略图来自同一版本 PDF；页数一致。"""
    thumbs = render_thumbnails(converted.pdf_path, tmp_path / "thumbs")
    assert len(thumbs) == converted.pages
    assert all(p.stat().st_size > 0 for p in thumbs)


def test_missing_input_rejected(tmp_path):
    with pytest.raises(ConversionError):
        convert_to_pdf(tmp_path / "nope.pptx", tmp_path)


def test_timeout_kills_process_group(tmp_path, monkeypatch):
    """超时：终止进程组且不留下假成功 PDF。"""
    fake = tmp_path / "fake-soffice.sh"
    fake.write_text("#!/bin/sh\nsleep 300\n")
    fake.chmod(0o755)
    monkeypatch.setattr("nero_ppt.rendering.libreoffice._soffice_binary", lambda: str(fake))
    pptx = tmp_path / "in.pptx"
    pptx.write_bytes(b"x")
    work = tmp_path / "work"
    t0 = time.monotonic()
    with pytest.raises(ConversionError) as exc_info:
        convert_to_pdf(pptx, work, timeout=3)
    assert "超时" in str(exc_info.value)
    assert time.monotonic() - t0 < 30
    # 无 PDF 产物
    assert not list(work.rglob("*.pdf"))


def test_work_dir_isolated_profile(converted, tmp_path, build_pptx):
    """每次转换使用独立用户配置目录（并发安全的基础）。"""
    work = tmp_path / "w2"
    convert_to_pdf(build_pptx, work / "t", timeout=180, out_path=work / "o.pdf")
    assert (work / "t" / ".lo-profile").exists()
    assert (work / "o.pdf").exists()
    shutil.rmtree(work, ignore_errors=True)
