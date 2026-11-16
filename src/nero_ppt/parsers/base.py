"""解析器基类：统一接口与确定性事实提取。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..contracts import Fact, FactVerification, ParsedContent, SourceBlock, SourceDocument

# 确定性事实提取：数字+常见单位；日期/季度
_NUM_UNIT_RE = re.compile(
    r"(\d[\d,，]*(?:\.\d+)?)\s*(%|个百分点|万元|亿元|万亿元|万|亿|元|个|家|人|次|分钟|小时|天|月|年|kg|km|GB|TB|MB|秒)")
_DATE_RE = re.compile(
    r"(\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?|\d{4}-\d{1,2}-\d{1,2}|\d{4}年|Q[1-4](?:\s*\d{4})?)")
_TOPIC_RE = re.compile(r"^(#{1,3}\s*|(?:一|二|三|四|五|六|七|八|九|十)、)(?P<title>[^，。#\n]{2,20})")

MAX_BLOCK_CHARS = 4000  # 超长块切分阈值


class ParserError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _new_document(data: bytes, display_name: str) -> SourceDocument:
    return SourceDocument(
        source_id=f"src-{secrets_token(6)}",
        display_name=display_name,
        file_hash=_sha(data),
        file_ext=Path(display_name).suffix.lower(),
        byte_size=len(data),
    )


def secrets_token(n: int) -> str:
    import secrets

    return secrets.token_hex(n)


def split_long_block(text: str, max_chars: int = MAX_BLOCK_CHARS) -> list[str]:
    """超长块按句切分，保留全部原文（不截断丢失）。"""
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[。！？!?\n])", text)
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        if len(buf) + len(s) > max_chars and buf:
            chunks.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        chunks.append(buf)
    return chunks


def extract_facts(blocks: list[SourceBlock]) -> list[Fact]:
    """确定性提取数字/单位/日期事实；全部初始 unverified。"""
    facts: list[Fact] = []
    seen: set[tuple] = set()
    for block in blocks:
        text = block.text or ""
        if not text:
            continue
        context = _topic_before(blocks, block)
        for m in _NUM_UNIT_RE.finditer(text):
            value, unit = m.group(1), m.group(2)
            start = max(0, m.start() - 18)
            evidence = text[start:m.end() + 6].replace("\n", " ").strip()
            key = (value, unit, block.block_id)
            if key in seen:
                continue
            seen.add(key)
            facts.append(Fact(
                fact_id=f"f-{secrets_token(5)}",
                source_block_ids=[block.block_id],
                raw_evidence=evidence,
                normalized_value=value.replace("，", ""),
                unit=unit,
                time=context.get("time", ""),
                topic=context.get("topic", ""),
                verification=FactVerification(verification_status="unverified"),
            ))
        for m in _DATE_RE.finditer(text):
            evidence = text[max(0, m.start() - 12):m.end() + 10].replace("\n", " ").strip()
            key = ("date", m.group(1), block.block_id)
            if key in seen:
                continue
            seen.add(key)
            facts.append(Fact(
                fact_id=f"f-{secrets_token(5)}",
                source_block_ids=[block.block_id],
                raw_evidence=evidence,
                normalized_value=m.group(1).replace(" ", ""),
                unit="",
                topic=context.get("topic", ""),
                verification=FactVerification(verification_status="unverified"),
            ))
    return facts


def _topic_before(blocks: list[SourceBlock], current: SourceBlock) -> dict:
    """向上找最近的标题块作为主题上下文。"""
    topic = ""
    time_val = ""
    for b in blocks:
        if b.block_id == current.block_id:
            break
        if b.kind == "heading":
            m = _TOPIC_RE.match(b.text or "")
            topic = (m.group("title") if m else (b.text or ""))[:20]
        m = _DATE_RE.search(b.text or "")
        if m and not time_val:
            time_val = m.group(1).replace(" ", "")
    return {"topic": topic, "time": time_val}


def build_parser(ext: str):
    ext = ext.lower()
    if ext == ".pdf":
        from .pdf import PdfParser

        return PdfParser()
    if ext == ".docx":
        from .docx import DocxParser

        return DocxParser()
    if ext == ".pptx":
        from .pptx import PptxParser

        return PptxParser()
    if ext in (".txt", ".md"):
        from .text import TextParser

        return TextParser()
    raise ParserError(f"不支持的解析类型 {ext}")


def parse_document(data: bytes, display_name: str) -> ParsedContent:
    """统一入口：解析 + 事实提取。编码/格式问题抛 ParserError。"""
    ext = Path(display_name).suffix.lower()
    parser = build_parser(ext)
    doc = parser.parse(data, display_name)
    doc.facts = extract_facts(doc.blocks)
    return doc
