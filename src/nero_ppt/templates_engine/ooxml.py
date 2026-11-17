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
from pptx.util import Emu, Pt

CT_CHART = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"

# 字符宽度估算系数（相对字号 pt → EMU）：全角按字号等宽，半角取保守近似。
# 估算偏保守（宁可提前缩字），不等于 Office 排版。
EMU_PER_PT = 12700
_FULLWIDTH_FACTOR = 1.0
_HALFWIDTH_FACTOR = 0.62
_DEFAULT_FONT_PT = 18.0
_BODY_INSET_DEFAULT_EMU = 91440  # a:bodyPr lIns/rIns 默认值


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


# ---------------------------------------------------------------- 字号继承与宽度自适应


def _def_rpr_sz(lvl1pPr) -> float:
    """从 lvl1pPr 里取 defRPr@sz（pt）。无则返回 0。"""
    if lvl1pPr is None:
        return 0.0
    d = lvl1pPr.find(qn("a:defRPr"))
    if d is None:
        return 0.0
    sz = d.get("sz")
    return float(sz) / 100.0 if sz else 0.0


def _sp_lststyle_sz(shape) -> float:
    """形状自身 txBody/lstStyle 的 lvl1 defRPr@sz（pt）。"""
    if not getattr(shape, "has_text_frame", False):
        return 0.0
    lst = shape.text_frame._txBody.find(qn("a:lstStyle"))
    if lst is None:
        return 0.0
    return _def_rpr_sz(lst.find(qn("a:lvl1pPr")))


def _master_txstyle_sz(master, placeholder_type: str) -> float:
    """母版 p:txStyles 里对应样式链的 lvl1 defRPr@sz（pt）。"""
    ts = master._element.find(qn("p:txStyles"))
    if ts is None:
        return 0.0
    style_tag = {"title": "p:titleStyle", "body": "p:bodyStyle"}.get(
        placeholder_type, "p:otherStyle")
    style = ts.find(qn(style_tag))
    if style is None:
        return 0.0
    return _def_rpr_sz(style.find(qn("a:lvl1pPr")))


def _placeholder_sz_chain(shape) -> float:
    """占位符继承链解析：layout 同位占位符 → master 占位符 → 母版 txStyles。"""
    if not getattr(shape, "is_placeholder", False):
        return 0.0
    ph_fmt = shape.placeholder_format
    ph_type = str(ph_fmt.type) if ph_fmt.type is not None else ""
    ph_idx = ph_fmt.idx

    def match_ph(container):
        for cand in container.placeholders:
            cf = cand.placeholder_format
            if cf.idx == ph_idx or (cf.type is not None and str(cf.type) == ph_type and ph_type):
                return cand
        return None

    # slide → layout
    part = shape.part
    layout = getattr(part, "slide_layout", None)
    if layout is not None:
        cand = match_ph(layout)
        if cand is not None:
            sz = _sp_lststyle_sz(cand)
            if sz:
                return sz
            # layout 占位符 → master 占位符
            cand2 = match_ph(layout.slide_master)
            if cand2 is not None:
                sz = _sp_lststyle_sz(cand2)
                if sz:
                    return sz
        sz = _master_txstyle_sz(layout.slide_master, "title" if "TITLE" in ph_type else "body")
        if sz:
            return sz
    return 0.0


def effective_font_pt(shape, default_pt: float = _DEFAULT_FONT_PT) -> float:
    """解析形状文本的渲染生效字号（pt）：run 显式 → sp lstStyle → 版式/母版占位符 →
    母版 txStyles → default。无 run 时返回 0.0（调用方用默认值）。"""
    if not getattr(shape, "has_text_frame", False):
        return 0.0
    for p in shape.text_frame.paragraphs:
        for r in p.runs:
            if r.font.size is not None:
                return r.font.size.pt
        # 段落属性 defRPr（a:pPr/a:defRPr@sz）
        pPr = p._p.find(qn("a:pPr"))
        if pPr is not None:
            sz = _def_rpr_sz(pPr)
            if sz:
                return sz
    sz = _sp_lststyle_sz(shape)
    if sz:
        return sz
    sz = _placeholder_sz_chain(shape)
    if sz:
        return sz
    return 0.0 if default_pt is None else default_pt


def est_text_width_emu(text: str, font_pt: float) -> int:
    """估算单行文本宽度（EMU）：全角 1.0×字号，半角 0.62×字号（保守近似）。"""
    if not text:
        return 0
    width_pt = 0.0
    for ch in text:
        factor = _HALFWIDTH_FACTOR if ord(ch) < 0x2E80 else _FULLWIDTH_FACTOR
        width_pt += font_pt * factor
    return int(width_pt * EMU_PER_PT)


def body_available_width_emu(shape) -> int:
    """文本框可用宽度：形状宽减去 bodyPr 左右内边距。"""
    w = int(shape.width or 0)
    if w <= 0:
        return 0
    bodyPr = shape.text_frame._txBody.find(qn("a:bodyPr"))
    l_ins = int(bodyPr.get("lIns", _BODY_INSET_DEFAULT_EMU)) if bodyPr is not None else _BODY_INSET_DEFAULT_EMU
    r_ins = int(bodyPr.get("rIns", _BODY_INSET_DEFAULT_EMU)) if bodyPr is not None else _BODY_INSET_DEFAULT_EMU
    return w - l_ins - r_ins


def apply_font_fit(shape, text: str, min_font_pt: float = 12.0) -> dict | None:
    """标题单行宽度自适应：文本按生效字号估算超出可用宽度时，缩小 run 显式字号至
    恰好单行放下的最大整数 pt（下限 min_font_pt）。

    返回 None（放得下/无文本/无尺寸信息）或 {from_pt, to_pt, overflow}；
    overflow=True 表示缩到下限仍可能折行（调用方应记录警告）。
    """
    if not text or not getattr(shape, "has_text_frame", False):
        return None
    avail = body_available_width_emu(shape)
    if avail <= 0:
        return None
    from_pt = effective_font_pt(shape)
    if from_pt <= 0:
        return None
    est = est_text_width_emu(text, from_pt)
    if est <= avail:
        return None
    scale = avail / est
    new_pt = max(min_font_pt, float(int(from_pt * scale)))
    # 下限保护后仍放不下 → 记录溢出（不静默删减文本）
    overflow = est_text_width_emu(text, new_pt) > avail
    for p in shape.text_frame.paragraphs:
        for r in p.runs:
            r.font.size = Pt(new_pt)
    return {"from_pt": from_pt, "to_pt": new_pt, "overflow": overflow}


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


def fit_table(graphic_frame, slide_height_emu: int,
              min_font_pt: float = 10.0, default_font_pt: float = 14.0) -> dict | None:
    """表格垂直自适应：实际高度（Σ行高）超出形状/页面可用高度时，
    压缩行高至均分可用空间；文字在压缩后装不下时同步缩小单元格字号（下限 min_font_pt）。

    数据行数由规格决定（不删减数据），与标题 fit 同理：宁缩排不溢出。
    返回 None（不溢出/无表格）或 info；overflow=True 表示缩到下限仍可能超出。
    """
    from math import ceil

    tbl = graphic_frame.table
    rows = list(tbl.rows)
    n = len(rows)
    if n == 0:
        return None
    top = int(graphic_frame.top or 0)
    shape_h = int(graphic_frame.height or 0)
    # 形状内高度 vs 页面剩余高度（底部留 0.05in），取小者
    avail = min(shape_h, max(slide_height_emu - top - 45720, 1))
    if avail <= 0:
        return None
    orig_total = sum(int(r.height) for r in rows)
    if orig_total <= avail:
        return None
    # 单元格可用文字宽（减去左右内边距约 0.1in×2）
    col_w = [max(int(c.width) - 182880, 1) for c in tbl.columns]
    texts = [[c.text for c in r.cells] for r in rows]
    sz = None
    for r in rows:
        for c in r.cells:
            for p in c.text_frame.paragraphs:
                for run in p.runs:
                    if run.font.size is not None:
                        sz = run.font.size.pt
                        break
            if sz:
                break
        if sz:
            break
    sz = sz or default_font_pt

    def per_row_need(font_pt: float) -> list[int]:
        out = []
        for row_texts in texts:
            lines = 1
            for t, w in zip(row_texts, col_w):
                if not t:
                    continue
                lines = max(lines, ceil(est_text_width_emu(t, font_pt) / w))
            out.append(int(lines * font_pt * 1.30 * EMU_PER_PT))
        return out

    h_each = avail // n
    needs = per_row_need(sz)
    changed_font = False
    while max(needs) > h_each and sz > min_font_pt:
        sz = max(min_font_pt, sz - 1.0)
        needs = per_row_need(sz)
        changed_font = True
    if changed_font:
        for r in rows:
            for c in r.cells:
                for p in c.text_frame.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(sz)
    overflow = max(needs) > h_each
    for row in rows:
        row.height = Emu(min(int(row.height), h_each))
    return {"rows": n, "avail_in": round(avail / 914400, 2),
            "font_pt": sz, "changed_font": changed_font, "overflow": overflow}


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
