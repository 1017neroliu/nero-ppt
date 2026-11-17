"""模板检查器：提取 PPTX/POTX 包的结构信息。

输出 TemplateInspection 中间结构（非持久化协议），供 profile.py 构建档案与
binding/builder 层使用。每个对象可通过 shape_id 定位。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn

from .ooxml import effective_font_pt

SMARTART_URI = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
CHART_URI = "http://schemas.openxmlformats.org/drawingml/2006/chart"
OLE_URI = "urn:schemas-microsoft-com:vml"  # OLE 通过 graphicFrame 的 v:shape 识别


@dataclass
class RunInfo:
    text: str
    font_name: str = ""
    font_ea: str = ""
    size_pt: float = 0.0
    bold: bool = False
    color_rgb: str = ""


@dataclass
class ParagraphInfo:
    runs: list[RunInfo] = field(default_factory=list)
    level: int = 0


@dataclass
class InspectShape:
    shape_id: int
    name: str
    shape_type: str
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    is_placeholder: bool = False
    placeholder_type: str = ""
    paragraphs: list[ParagraphInfo] = field(default_factory=list)
    has_table: bool = False
    table_rows: int = 0
    table_cols: int = 0
    has_chart: bool = False
    chart_type: str = ""
    has_image: bool = False
    image_ext: str = ""
    is_group: bool = False
    children: list["InspectShape"] = field(default_factory=list)
    unsupported_kind: str = ""  # smartart / ole / 其他不可编辑类型
    # 占位符继承生效字号（layout/master 链解析，pt；0 表示未知）。
    # run 无显式字号时预筛预算必须用它，否则按 14pt 默认会严重虚高（如母版标题 36pt）。
    effective_font_pt: float = 0.0


@dataclass
class InspectSlide:
    slide_id: int
    index: int
    layout_name: str
    master_index: int
    shapes: list[InspectShape] = field(default_factory=list)
    notes_text: str = ""
    image_parts: list[str] = field(default_factory=list)  # image part names


@dataclass
class TemplateInspection:
    path: str
    file_hash: str
    ext: str
    width_emu: int
    height_emu: int
    slides: list[InspectSlide] = field(default_factory=list)
    masters: list[dict] = field(default_factory=list)  # {"layouts": [name...]}
    fonts: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    used_fonts_ea: list[str] = field(default_factory=list)


def _run_info(run) -> RunInfo:
    info = RunInfo(text=run.text or "")
    font = run.font
    if font.name:
        info.font_name = font.name
    rPr = run._r.find(qn("a:rPr"))
    if rPr is not None:
        ea = rPr.find(qn("a:ea"))
        if ea is not None:
            info.font_ea = ea.get("typeface", "")
    if font.size is not None:
        info.size_pt = font.size.pt
    if font.bold is not None:
        info.bold = font.bold
    try:
        if font.color and font.color.rgb is not None:
            info.color_rgb = str(font.color.rgb)
    except Exception:  # noqa: BLE001
        pass
    return info


def _paragraph_info(p) -> ParagraphInfo:
    return ParagraphInfo(runs=[_run_info(r) for r in p.runs], level=p.level or 0)


def _graphic_frame_kind(shape) -> tuple[str, str]:
    """识别 graphicFrame 内部类型：table / chart / smartart / ole。"""
    el = shape._element
    graphic = el.find(qn("p:graphicFrame") + "/" + qn("a:graphic")) if el.tag == qn("p:graphicFrame") else None
    if graphic is None:
        graphic = el.find(qn("a:graphic"))
    if graphic is None:
        return "", ""
    data = graphic.find(qn("a:graphicData"))
    if data is None:
        return "", ""
    uri = data.get("uri", "")
    if data.find(qn("a:tbl")) is not None:
        return "table", ""
    if uri == CHART_URI or data.find(qn("c:chart")) is not None:
        return "chart", ""
    if uri == SMARTART_URI:
        return "unsupported", "smartart"
    return "", ""


def _inspect_shape(shape, external_links: list[str], image_parts: list[str]) -> InspectShape:
    st = shape.shape_type
    kind = str(st) if st is not None else "UNKNOWN"
    info = InspectShape(
        shape_id=shape.shape_id,
        name=shape.name or "",
        shape_type=kind,
        x=int(shape.left or 0), y=int(shape.top or 0),
        w=int(shape.width or 0), h=int(shape.height or 0),
    )
    if shape.is_placeholder:
        info.is_placeholder = True
        info.placeholder_type = str(shape.placeholder_format.type)

    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        info.is_group = True
        info.children = [_inspect_shape(c, external_links, image_parts) for c in shape.shapes]
        return info

    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
        info.has_image = True
        try:
            info.image_ext = shape.image.ext
            image_parts.append(shape.image.filename or f"image.{info.image_ext}")
        except Exception:  # noqa: BLE001
            pass
        return info

    if shape.has_text_frame:
        try:
            info.effective_font_pt = effective_font_pt(shape, default_pt=0.0)
        except Exception:  # noqa: BLE001
            info.effective_font_pt = 0.0
        info.paragraphs = [_paragraph_info(p) for p in shape.text_frame.paragraphs]
        # 超链接
        for p in info.paragraphs:
            for r in p.runs:
                try:
                    hl = r._r.find(qn("a:rPr") + "/" + qn("a:hlinkClick")) if r._r.find(qn("a:rPr")) is not None else None
                except Exception:  # noqa: BLE001
                    hl = None
        try:
            for hl in shape._element.findall(".//" + qn("a:hlinkClick")):
                rid = hl.get(qn("r:id"))
                if rid:
                    from pptx.opc.constants import RELATIONSHIP_TYPE as RT

                    rel = shape.part.rels.get(rid)
                    if rel is not None and rel.reltype == RT.HYPERLINK:
                        external_links.append(rel.target_ref)
        except Exception:  # noqa: BLE001
            pass
        return info

    if getattr(shape, "has_chart", False):
        info.has_chart = True
        try:
            info.chart_type = str(shape.chart.chart_type)
        except Exception:  # noqa: BLE001
            info.chart_type = "unknown"
        return info

    if getattr(shape, "has_table", False):
        info.has_table = True
        info.table_rows = len(shape.table.rows)
        info.table_cols = len(shape.table.columns)
        return info

    # 兜底：graphicFrame 特殊类型
    gkind, unsupported = _graphic_frame_kind(shape)
    if gkind == "table":
        info.has_table = True
        info.table_rows = len(shape.table.rows)
        info.table_cols = len(shape.table.columns)
    elif gkind == "chart":
        info.has_chart = True
    elif unsupported:
        info.unsupported_kind = unsupported
    return info


def inspect_template(path: str | Path) -> TemplateInspection:
    p = Path(path)
    data = p.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    ext = p.suffix.lower()
    if ext not in (".pptx", ".potx"):
        raise ValueError(f"不支持的模板扩展名: {ext}（仅支持 .pptx/.potx）")

    prs = Presentation(str(p))
    inspection = TemplateInspection(
        path=str(p),
        file_hash=sha,
        ext=ext,
        width_emu=int(prs.slide_width),
        height_emu=int(prs.slide_height),
    )

    for master in prs.slide_masters:
        inspection.masters.append({"layouts": [l.name for l in master.slide_layouts]})

    fonts: set[str] = set()
    ea_fonts: set[str] = set()
    for idx, slide in enumerate(prs.slides):
        links: list[str] = []
        parts: list[str] = []
        islide = InspectSlide(slide_id=slide.slide_id, index=idx, layout_name=slide.slide_layout.name,
                              master_index=0)
        for shape in slide.shapes:
            ishape = _inspect_shape(shape, links, parts)
            islide.shapes.append(ishape)
            for para in ishape.paragraphs:
                for r in para.runs:
                    if r.font_name:
                        fonts.add(r.font_name)
                    if r.font_ea:
                        ea_fonts.add(r.font_ea)
            if ishape.unsupported_kind:
                pass
        if slide.has_notes_slide:
            islide.notes_text = slide.notes_slide.notes_text_frame.text or ""
        islide.image_parts = parts
        inspection.slides.append(islide)
        inspection.external_links.extend(links)

    inspection.fonts = sorted(fonts)
    inspection.used_fonts_ea = sorted(ea_fonts)
    return inspection
