"""TXT/MD 解析：UTF-8 行范围分块；编码错误显式报告。"""
from __future__ import annotations

from ..contracts import ParsedContent, SourceBlock
from .base import ParserError, _new_document, split_long_block

CHUNK_LINES = 30  # 每块行数


class TextParser:
    def parse(self, data: bytes, display_name: str) -> ParsedContent:
        doc = _new_document(data, display_name)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            # 尝试 GBK 常见中文编码
            try:
                text = data.decode("gbk")
                doc.parse_error = "文件为 GBK 编码，已自动转码解析；建议提供 UTF-8"
            except UnicodeDecodeError as exc2:
                raise ParserError(f"文本编码无法识别（非 UTF-8/GBK）：{exc2}") from exc
        lines = text.splitlines()
        blocks: list[SourceBlock] = []
        for start in range(0, max(len(lines), 1), CHUNK_LINES):
            chunk_lines = lines[start:start + CHUNK_LINES]
            chunk_text = "\n".join(chunk_lines)
            if not chunk_text.strip():
                continue
            kind = "heading" if display_name.lower().endswith(".md") and chunk_text.lstrip().startswith("#") else "paragraph"
            for i, part in enumerate(split_long_block(chunk_text)):
                blocks.append(SourceBlock(
                    block_id=f"{doc.source_id}-l{start}b{i}",
                    source_id=doc.source_id,
                    kind=kind,  # type: ignore[arg-type]
                    text=part,
                    locator={"start_line": start + 1, "end_line": start + len(chunk_lines), "part": i},
                ))
        doc.parse_status = "ok"
        return ParsedContent(document=doc, blocks=blocks)
