#!/usr/bin/env python3
"""生成测试素材：示例资料（DOCX/PDF/TXT/MD/PPTX）、手工 SlideSpec、外部风格测试稿。

运行：python3 scripts/build_fixtures.py  （在 nero-ppt 目录执行）
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"


def build_docx(path: Path) -> None:
    import docx

    d = docx.Document()
    d.add_heading("2026 年第三季度经营汇报", level=1)
    d.add_paragraph("汇报范围：2026 年 7 月 1 日至 9 月 30 日。")
    d.add_heading("一、核心指标", level=2)
    d.add_paragraph("本季度营业收入 4,820 万元，环比增长 12.4%；毛利率 38.2%，较上季度下降 1.1 个百分点。")
    d.add_paragraph("新增客户 126 家，累计客户数 1,043 家。客户续约率 91.5%。")
    d.add_heading("二、工作进展", level=2)
    d.add_paragraph("9 月 12 日完成 3.0 版本发布，线上故障率下降至 0.3%。")
    d.add_paragraph("华东区部署项目 8 个，其中 6 个已验收。")
    d.add_heading("三、问题与原因", level=2)
    d.add_paragraph("毛利率下降原因：服务器成本上升 15%，以及三季度促销折让增加。")
    table = d.add_table(rows=3, cols=3)
    table.style = "Table Grid"
    cells = [["指标", "Q2", "Q3"], ["营业收入（万元）", "4,288", "4,820"], ["毛利率", "39.3%", "38.2%"]]
    for i, row in enumerate(cells):
        for j, v in enumerate(row):
            table.cell(i, j).text = v
    d.add_heading("四、后续计划", level=2)
    d.add_paragraph("四季度目标：营业收入 5,500 万元，毛利率恢复至 40%。负责人：销售部与交付部。")
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(path)


def build_txt(path: Path) -> None:
    text = """产品调研纪要（2026-10）

客户 A（制造业）：希望报表能按周导出，当前只能按月。愿意为导出功能付费约 2 万元/年。
客户 B（零售）：反馈移动端打开速度慢，平均 4.8 秒；期望 2 秒以内。
客户 C：提出数据大屏需求，预算 15 万元，希望 2027 年 3 月前交付。

竞品动态：竞品 X 在 9 月发布了自然语言查询功能，定价上浮 10%。
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_md(path: Path) -> None:
    text = """# 技术方案讨论记录

## 背景
现有部署架构为单机部署，日均请求 12 万次，峰值 CPU 78%。

## 方案对比
| 方案 | 成本 | 周期 | 风险 |
| --- | --- | --- | --- |
| 纵向扩容 | 8 万/年 | 2 周 | 瓶颈仍在 DB |
| 读写分离 | 12 万/年 | 4 周 | 需要改造访问层 |
| 引入缓存 | 5 万/年 | 3 周 | 一致性需评估 |

## 结论
优先读写分离，预计 2026 年 12 月完成；缓存作为二期。
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_source_pptx(path: Path) -> None:
    from pptx import Presentation
    from pptx.util import Emu, Pt

    prs = Presentation()
    prs.slide_width = Emu(12192000)
    prs.slide_height = Emu(6858000)
    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = "交付中心季度复盘"
    s1.placeholders[1].text = "内部资料 · 2026-10"
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "项目交付数据"
    body = s2.placeholders[1].text_frame
    body.text = "本季度交付项目 24 个，按时交付率 87.5%"
    p = body.add_paragraph()
    p.text = "延期项目 3 个，均为需求变更导致"
    p.font.size = Pt(18)
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(path)


def build_external_style(path: Path) -> None:
    """外部风格测试稿：无 nero.* 命名、不同字体、含超链接，模拟客户模板。"""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Pt

    prs = Presentation()
    prs.slide_width = Emu(12192000)
    prs.slide_height = Emu(6858000)
    # 封面
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "客户模板封面"
    s.placeholders[1].text = "客户名称 · 2026"
    # 正文（标题+正文占位符）
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "客户模板正文页"
    tf = s.placeholders[1].text_frame
    tf.text = "客户模板要点一"
    p = tf.add_paragraph()
    p.text = "客户模板要点二"
    p.font.size = Pt(16)
    # 带超链接的文本框页
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "链接页"
    box = s.shapes.add_textbox(Emu(2000000), Emu(2500000), Emu(6000000), Emu(600000))
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "参考链接"
    run.hyperlink.address = "https://example.com/report"
    run.font.size = Pt(14)
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(path)


def build_handmade_spec(path: Path) -> None:
    """人工审核过的 SlideSpec 集合（S03 Builder 输入）。"""
    from nero_ppt.contracts import SlideSpec

    specs = [
        SlideSpec(
            slide_id="cover-1",
            purpose="封面",
            headline="2026 年第三季度经营汇报",
            visual_intent="title",
            speaker_notes={"script": "各位领导好，下面汇报三季度经营情况。", "transition": "先看核心指标"},
        ),
        SlideSpec(
            slide_id="bullets-1",
            purpose="核心指标",
            headline="核心经营指标",
            body_blocks=[{"kind": "bullets", "items": [
                "营业收入 4,820 万元，环比增长 12.4%",
                "毛利率 38.2%，较上季度下降 1.1 个百分点",
                "新增客户 126 家，续约率 91.5%",
            ]}],
            visual_intent="bullets",
            source_refs=[{"source_id": "doc-1", "quote": "本季度营业收入 4,820 万元"}],
            must_keep_fact_ids=["f-rev", "f-margin"],
            speaker_notes={"script": "收入环比增长 12.4%，毛利率受成本影响小幅下降。",
                           "source_explanations": ["4,820 万元来自 Q3 经营汇报第一章"]},
        ),
        SlideSpec(
            slide_id="table-1",
            purpose="指标对比",
            headline="季度指标对比",
            body_blocks=[{"kind": "table", "headers": ["指标", "Q2", "Q3"],
                          "rows": [["营业收入（万元）", "4,288", "4,820"], ["毛利率", "39.3%", "38.2%"]]}],
            visual_intent="table",
            speaker_notes={"script": "表格显示收入与毛利率的季度变化。"},
        ),
        SlideSpec(
            slide_id="chart-1",
            purpose="收入趋势",
            headline="季度收入趋势（万元）",
            body_blocks=[{"kind": "chart", "chart_type": "bar",
                          "categories": ["Q1", "Q2", "Q3", "Q4目标"],
                          "series": [{"name": "营业收入", "values": [3950, 4288, 4820, 5500]}]}],
            visual_intent="chart_bar",
            speaker_notes={"script": "收入连续三个季度增长，四季度目标 5,500 万元。"},
        ),
        SlideSpec(
            slide_id="two-col-1",
            purpose="问题与行动",
            headline="问题与后续行动",
            body_blocks=[
                {"kind": "bullets", "label": "问题", "items": ["服务器成本上升 15%", "促销折让增加"]},
                {"kind": "bullets", "label": "行动", "items": ["四季度收入目标 5,500 万元", "毛利率恢复至 40%"]},
            ],
            visual_intent="two_column",
            speaker_notes={"script": "成本问题已定位，四季度双线改善。"},
        ),
        SlideSpec(
            slide_id="process-1",
            purpose="交付流程",
            headline="四季度交付流程",
            body_blocks=[{"kind": "process", "steps": ["需求冻结", "批量交付", "验收测试", "复盘归档"]}],
            visual_intent="process",
            speaker_notes={"script": "交付按四步流程推进。"},
        ),
        SlideSpec(
            slide_id="summary-1",
            purpose="总结",
            headline="总结",
            body_blocks=[{"kind": "bullets", "items": ["收入连续增长，成本压力可控", "四季度冲刺 5,500 万元"]}],
            visual_intent="summary",
            speaker_notes={"script": "以上就是本次汇报，请领导指正。", "transition": "结束"},
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    from nero_ppt.contracts import envelope

    data = [envelope(s) for s in specs]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def docx_to_pdf(src: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = out_dir / ".lo-profile"
    subprocess.run(
        ["soffice", "--headless", f"-env:UserInstallation=file://{profile_dir}",
         "--convert-to", "pdf", "--outdir", str(out_dir), str(src)],
        check=True, timeout=180,
    )
    pdf = out_dir / (src.stem + ".pdf")
    if not pdf.exists():
        raise RuntimeError("PDF 转换失败")
    return pdf


def main() -> int:
    build_docx(FIX / "sample-report.docx")
    build_txt(FIX / "sample-notes.txt")
    build_md(FIX / "sample-tech.md")
    build_source_pptx(FIX / "sample-deck.pptx")
    build_external_style(FIX / "external-style.pptx")
    build_handmade_spec(FIX / "handmade-spec.json")
    pdf = docx_to_pdf(FIX / "sample-report.docx", FIX)
    print(f"fixtures written under {FIX} (pdf: {pdf.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
