"""PDF 解析：pypdf 逐页文本；扫描件（无文本层）→ needs_input。"""
from __future__ import annotations

import io

from ..contracts import ParsedContent, SourceBlock
from .base import ParserError, _new_document, split_long_block

MIN_TEXT_CHARS_PER_PAGE = 8  # 每页至少的文本字符（低于则疑似扫描件）


class PdfParser:
    def parse(self, data: bytes, display_name: str) -> ParsedContent:
        doc = _new_document(data, display_name)
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"PDF 打开失败：{exc}") from exc
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise ParserError("PDF 已加密，无法解析") from exc

        blocks: list[SourceBlock] = []
        pages_with_text = 0
        for page_no, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception:  # noqa: BLE001
                text = ""
            if len(text) >= MIN_TEXT_CHARS_PER_PAGE:
                pages_with_text += 1
            for i, chunk in enumerate(split_long_block(text)):
                blocks.append(SourceBlock(
                    block_id=f"{doc.source_id}-p{page_no}b{i}",
                    source_id=doc.source_id,
                    kind="paragraph",
                    text=chunk,
                    locator={"page": page_no, "part": i},
                ))
        total_pages = len(reader.pages)
        if total_pages == 0 or pages_with_text == 0:
            doc.parse_status = "needs_input"
            doc.parse_error = "未检测到可提取文本（疑似扫描件），请提供可解析文本或联系管理员"
        else:
            doc.parse_status = "ok"
        return ParsedContent(document=doc, blocks=blocks)
