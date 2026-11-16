"""PPTX 资料解析：页内文本、表格、备注（作为资料而非模板）。"""
from __future__ import annotations

import io

from ..contracts import ParsedContent, SourceBlock
from .base import ParserError, _new_document, split_long_block


class PptxParser:
    def parse(self, data: bytes, display_name: str) -> ParsedContent:
        doc = _new_document(data, display_name)
        try:
            from pptx import Presentation

            prs = Presentation(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"PPTX 打开失败：{exc}") from exc

        blocks: list[SourceBlock] = []
        for slide in prs.slides:
            slide_id = slide.slide_id
            page_no = len(blocks)

            def walk(shapes):
                for shape in shapes:
                    if shape.shape_type == 6:  # GROUP
                        walk(shape.shapes)
                        continue
                    if getattr(shape, "has_table", False):
                        rows = [[c.text.strip() for c in row.cells] for row in shape.table.rows]
                        if any(any(r) for r in rows):
                            blocks.append(SourceBlock(
                                block_id=f"{doc.source_id}-s{slide_id}sh{shape.shape_id}",
                                source_id=doc.source_id,
                                kind="table",
                                text="",
                                table=rows,
                                locator={"slide_id": slide_id, "shape_id": shape.shape_id},
                            ))
                        continue
                    if shape.has_text_frame and shape.text_frame.text.strip():
                        for i, part in enumerate(split_long_block(shape.text_frame.text.strip())):
                            blocks.append(SourceBlock(
                                block_id=f"{doc.source_id}-s{slide_id}sh{shape.shape_id}b{i}",
                                source_id=doc.source_id,
                                kind="paragraph",
                                text=part,
                                locator={"slide_id": slide_id, "shape_id": shape.shape_id, "part": i},
                            ))

            walk(slide.shapes)
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                blocks.append(SourceBlock(
                    block_id=f"{doc.source_id}-s{slide_id}notes",
                    source_id=doc.source_id,
                    kind="notes",
                    text=slide.notes_slide.notes_text_frame.text.strip(),
                    locator={"slide_id": slide_id},
                ))
        doc.parse_status = "ok"
        return ParsedContent(document=doc, blocks=blocks)
