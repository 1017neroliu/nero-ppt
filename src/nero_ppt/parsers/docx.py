"""DOCX 解析：python-docx 段落与表格；locator 用段落/表格索引（不制造页码）。"""
from __future__ import annotations

import io

from ..contracts import ParsedContent, SourceBlock
from .base import ParserError, _new_document, split_long_block


class DocxParser:
    def parse(self, data: bytes, display_name: str) -> ParsedContent:
        doc = _new_document(data, display_name)
        try:
            import docx

            d = docx.Document(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"DOCX 打开失败：{exc}") from exc

        blocks: list[SourceBlock] = []
        para_idx = 0
        for para in d.paragraphs:
            text = para.text.strip()
            if not text:
                para_idx += 1
                continue
            kind = "heading" if para.style is not None and para.style.name.startswith("Heading") else "paragraph"
            for i, chunk in enumerate(split_long_block(text)):
                blocks.append(SourceBlock(
                    block_id=f"{doc.source_id}-para{para_idx}b{i}",
                    source_id=doc.source_id,
                    kind=kind,  # type: ignore[arg-type]
                    text=chunk,
                    locator={"paragraph_index": para_idx, "part": i},
                ))
            para_idx += 1

        for t_idx, table in enumerate(d.tables):
            for r_idx, row in enumerate(table.rows):
                cells = [c.text.strip() for c in row.cells]
                if not any(cells):
                    continue
                blocks.append(SourceBlock(
                    block_id=f"{doc.source_id}-tbl{t_idx}r{r_idx}",
                    source_id=doc.source_id,
                    kind="table",
                    text="",
                    table=[cells],
                    locator={"table_index": t_idx, "row": r_idx},
                ))
        doc.parse_status = "ok"
        return ParsedContent(document=doc, blocks=blocks)
