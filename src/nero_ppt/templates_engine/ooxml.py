"""受控 OOXML 操作：页面克隆、资源关系处理、保留格式的文本/表格/图片编辑。

规则：
- 共享不可变资源（图片、版式、母版、主题、媒体）；克隆会变化的资源（图表 part
  及其内嵌数据工作簿、页面 XML），避免一页图表数据修改污染另一页。
- 文本编辑保留段落/Run 级格式，禁止整框 shape.text 粗暴替换。
- notesSlide 关系在克隆时丢弃，由上层重新生成讲稿。
"""
from __future__ import annotations

import copy

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.package import Part, XmlPart
from pptx.oxml.ns import qn
from pptx.util import Emu

CT_CHART = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"


# ---------------------------------------------------------------- 页面克隆


def clone_slide(prs: Presentation, source_slide) -> object:
    """克隆一页（含图片等共享关系；图表 part 连同内嵌数据一起克隆）。

    返回新 slide 对象（python-pptx 包装）。新页追加到 sldIdLst 末尾。
    """
    src_part = source_slide.part
    package = src_part.package
    partname = package.next_partname("/ppt/slides/slide%d.xml")
    new_el = copy.deepcopy(src_part._element)
    new_part = XmlPart(partname, src_part.content_type, package, new_el)

    for rId, rel in list(src_part.rels.items()):
        reltype = rel.reltype
        if reltype == RT.NOTES_SLIDE:
            continue  # 新页的讲稿由上层重新生成
        if rel.is_external:
            new_part.relate_to(rel.target_ref, reltype, is_external=True)
            continue
        target_part = rel.target_part
        if reltype == RT.CHART:
            cloned = _clone_chart_part(package, target_part, new_part)
            new_rid = new_part.relate_to(cloned, RT.CHART)
            _remap_rid_in_slide(new_el, rId, new_rid)
        else:
            # slideLayout / image / media / theme 等共享不可变资源
            new_part.relate_to(target_part, reltype)

    # 注册到演示文稿
    pres_part = prs.part
    slide_rid = pres_part.relate_to(new_part, RT.SLIDE)
    sldIdLst = pres_part._element.sldIdLst
    from pptx.oxml.ns import nsmap

    sldId = etree.SubElement(sldIdLst, qn("p:sldId"))
    max_id = max((int(s.get("id", 0)) for s in sldIdLst.findall(qn("p:sldId"))), default=255)
    sldId.set("id", str(max_id + 1))
    sldId.set(qn("r:id"), slide_rid)

    # 重新装配 python-pptx Slide 包装
    from pptx.slide import Slide

    return Slide(new_el, new_part)


def _clone_chart_part(package, chart_part, new_slide_part):
    """克隆图表 part 与内嵌 xlsx；样式/颜色 part 共享。"""
    partname = package.next_partname("/ppt/charts/chart%d.xml")
    new_chart = XmlPart(partname, chart_part.content_type, package, copy.deepcopy(chart_part._element))
    for rId, rel in list(chart_part.rels.items()):
        if rel.is_external:
            new_chart.relate_to(rel.target_ref, rel.reltype, is_external=True)
            continue
        target = rel.target_part
        if rel.reltype == RT.PACKAGE:
            # 内嵌数据工作簿：必须克隆，否则 replace_data 污染其他图表（T02）
            emb_name = package.next_partname("/ppt/embeddings/embed%d.xlsx")
            new_emb = Part(emb_name, target.content_type, package, blob=target.blob)
            new_chart.relate_to(new_emb, RT.PACKAGE)
        else:
            new_chart.relate_to(target, rel.reltype)
    return new_chart


def _remap_rid_in_slide(slide_el, old_rid: str, new_rid: str) -> None:
    """把 slide XML 内引用 old_rid 的属性改为 new_rid。"""
    for el in slide_el.iter():
        for attr in (qn("r:id"), qn("r:embed"), qn("r:link")):
            if el.get(attr) == old_rid:
                el.set(attr, new_rid)


def keep_only_slides(prs: Presentation, slides: list) -> None:
    """重建 sldIdLst：仅保留给定新页（按顺序）。原模板页成为孤儿 part（合法，不引用）。"""
    pres_part = prs.part
    sldIdLst = pres_part._element.sldIdLst
    for sldId in list(sldIdLst.findall(qn("p:sldId"))):
        rId = sldId.get(qn("r:id"))
        if rId and rId in pres_part.rels:
            pres_part.drop_rel(rId)
        sldIdLst.remove(sldId)
    for slide in slides:
        rId = pres_part.relate_to(slide.part, RT.SLIDE)
        max_id = max((int(s.get("id", 0)) for s in sldIdLst.findall(qn("p:sldId"))), default=255)
        sldId = etree.SubElement(sldIdLst, qn("p:sldId"))
        sldId.set("id", str(max_id + 1))
        sldId.set(qn("r:id"), rId)


# ---------------------------------------------------------------- 文本编辑


def _first_run_paragraph(tf):
    for p in tf.paragraphs:
        if p.runs:
            return p
    return tf.paragraphs[0] if tf.paragraphs else None


def set_text_keep_format(tf, text: str) -> None:
    """单段文本替换：保留首段首 run 格式；删除其余段落。"""
    if not tf.paragraphs:
        raise ValueError("文本框无段落")
    first_p = _first_run_paragraph(tf)
    # 删除除 first_p 之外的所有段落
    for p in list(tf.paragraphs):
        if p._p is not first_p._p:
            p._p.getparent().remove(p._p)
    runs = first_p.runs
    if runs:
        runs[0].text = text
        for r in runs[1:]:
            r._r.getparent().remove(r._r)
    else:
        run = first_p.add_run()
        run.text = text


def set_bullets_keep_format(tf, lines: list[str], levels: list[int] | None = None) -> None:
    """多段落替换：以第一个带 run 的段落为样式原型，逐条复制；levels 控制缩进级别。"""
    if not tf.paragraphs:
        raise ValueError("文本框无段落")
    proto = None
    for p in tf.paragraphs:
        if p.runs:
            proto = copy.deepcopy(p._p)
            break
    if proto is None:
        proto = copy.deepcopy(tf.paragraphs[0]._p)

    txBody = tf._txBody
    for p in list(tf.paragraphs):
        p._p.getparent().remove(p._p)

    levels = levels or [0] * len(lines)
    if len(levels) < len(lines):
        levels = levels + [0] * (len(lines) - len(levels))
    for line, level in zip(lines, levels):
        newp = copy.deepcopy(proto)
        txBody.append(newp)
        _fill_paragraph_text(newp, line, level_delta=level)


def _fill_paragraph_text(p_el, text: str, level_delta: int = 0) -> None:
    runs = p_el.findall(qn("a:r"))
    if runs:
        t = runs[0].find(qn("a:t"))
        if t is None:
            t = etree.SubElement(runs[0], qn("a:t"))
        t.text = text
        for r in runs[1:]:
            p_el.remove(r)
    else:
        r = etree.SubElement(p_el, qn("a:r"))
        t = etree.SubElement(r, qn("a:t"))
        t.text = text
    if level_delta:
        pPr = p_el.find(qn("a:pPr"))
        if pPr is None:
            pPr = etree.Element(qn("a:pPr"))
            p_el.insert(0, pPr)
        pPr.set("lvl", str(int(pPr.get("lvl", "0")) + level_delta))


# ---------------------------------------------------------------- 表格


def set_table_data(graphic_frame, headers: list[str], rows: list[list[str]]) -> dict:
    """写入表格数据。模板列结构不增删：数据列多时合并进末列、少时补空列；
    行数自动增删（复制最后一行样式）。返回 info 带 adapted 标记。"""
    table = graphic_frame.table
    ncols = len(table.columns)
    adapted = False
    if ncols != len(headers):
        adapted = True
        if len(headers) > ncols:
            # 多余数据列合并进末列（不丢数据）
            headers = headers[:ncols - 1] + ["；".join(headers[ncols - 1:])]
            rows = [r[:ncols - 1] + ["；".join(r[ncols - 1:])] if len(r) > ncols else list(r)
                    for r in rows]
        else:
            headers = headers + [""] * (ncols - len(headers))
            rows = [list(r) + [""] * (ncols - len(r)) for r in rows]
    tbl = graphic_frame._element.graphic.graphicData.tbl
    trs = tbl.findall(qn("a:tr"))
    needed = len(rows) + 1  # 含表头
    # 删除多余行
    while len(trs) > needed:
        tbl.remove(trs.pop())
    # 不足则复制末行
    while len(trs) < needed:
        new_tr = copy.deepcopy(trs[-1])
        tbl.append(new_tr)
        trs.append(new_tr)
    # 写表头
    _write_row(trs[0], headers)
    for i, row in enumerate(rows, start=1):
        _write_row(trs[i], row)
    return {"rows": needed, "cols": ncols, "adapted": adapted}


def _write_row(tr_el, values: list[str]) -> None:
    tcs = tr_el.findall(qn("a:tc"))
    for j, tc in enumerate(tcs):
        if j >= len(values):
            break
        txBody = tc.find(qn("a:txBody"))
        if txBody is None:
            continue
        ps = txBody.findall(qn("a:p"))
        # 保留第一个带 run 的段落样式，删除其余段落
        proto = None
        for p in ps:
            if p.findall(qn("a:r")):
                proto = copy.deepcopy(p)
                break
        if proto is None:
            proto = copy.deepcopy(ps[0]) if ps else etree.Element(qn("a:p"))
        for p in ps:
            txBody.remove(p)
        txBody.append(proto)
        _fill_paragraph_text(proto, values[j])


# ---------------------------------------------------------------- 图表


def set_chart_data(chart, categories: list[str], series: list[tuple[str, list[float]]]) -> None:
    """原生图表数据替换：python-pptx replace_data 会重建 chart XML 与内嵌 xlsx。"""
    data = CategoryChartData()
    data.categories = categories
    for name, values in series:
        data.add_series(name, values)
    chart.replace_data(data)


# ---------------------------------------------------------------- 图片


def replace_image(slide, pic_shape, image_path: str) -> str:
    """替换图片：新增 image part 并改 blip 引用；原图片资源不受影响。"""
    image_part, rId = slide.part.get_or_add_image_part(image_path)
    blip_fill = pic_shape._element.find(qn("p:blipFill"))
    if blip_fill is None:
        raise ValueError(f"形状 {pic_shape.shape_id} 无 blipFill，不能替换图片")
    blip = blip_fill.find(qn("a:blip"))
    if blip is None:
        raise ValueError("blipFill 无 a:blip")
    blip.set(qn("r:embed"), rId)
    return rId


# ---------------------------------------------------------------- POTX 规范化


def normalize_potx_to_pptx(src_path: str, out_path: str) -> None:
    """POTX → PPTX 包类型规范化：改写 [Content_Types].xml 的主部件类型。"""
    import re
    import shutil
    import zipfile

    shutil.copyfile(src_path, out_path)
    with zipfile.ZipFile(out_path, "r") as zin:
        names = zin.namelist()
        items = {n: zin.read(n) for n in names}
    ct = items["[Content_Types].xml"].decode("utf-8")
    ct = ct.replace(
        "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    )
    items["[Content_Types].xml"] = ct.encode("utf-8")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            zout.writestr(n, items[n])
