"""资料与内容协议：SourceDocument、SourceBlock、Fact。

locator 采用格式对应结构：
- PDF：{"page": int}
- DOCX：{"paragraph_index": int} 或 {"table_index": int, "row": int, "col": int}
- PPTX：{"slide_id": int, "shape_id": int|None}
- TXT/MD：{"start_line": int, "end_line": int}
不能制造 DOCX 页码等伪定位。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictInt, StrictStr

from .common import NeroModel

ParseStatus = Literal["pending", "ok", "unsupported", "needs_input", "failed"]
BlockKind = Literal["paragraph", "heading", "table", "list", "notes", "chart_data"]
ExtractionMode = Literal["native", "hybrid"]


class SourceDocument(NeroModel):
    """一份上传资料（或模板之外的输入文档）。"""

    source_id: StrictStr
    display_name: StrictStr
    file_hash: StrictStr
    file_ext: StrictStr
    parse_status: ParseStatus = "pending"
    parse_error: StrictStr = ""
    byte_size: StrictInt = 0


class SourceBlock(NeroModel):
    """解析后的最小语义单元，保留原文，可追溯。"""

    block_id: StrictStr
    source_id: StrictStr
    kind: BlockKind
    text: StrictStr = Field(default="", max_length=20000)
    table: list[list[StrictStr]] = Field(default_factory=list)
    locator: dict[str, Any] = Field(default_factory=dict)
    extraction: ExtractionMode = "native"


class FactVerification(NeroModel):
    verification_status: Literal["unverified", "confirmed", "conflict", "missing"]
    note: StrictStr = ""


class Fact(NeroModel):
    """确定性提取或模型整理的事实。与模型推断分开，推断记录 supports/supporting_text。"""

    fact_id: StrictStr
    source_block_ids: list[StrictStr] = Field(min_length=1)
    raw_evidence: StrictStr
    normalized_value: StrictStr = ""
    unit: StrictStr = ""
    time: StrictStr = ""
    topic: StrictStr = ""
    verification: FactVerification = Field(
        default_factory=lambda: FactVerification(verification_status="unverified")
    )


class ModelInference(NeroModel):
    """模型推断：声明它依据哪些事实/原文，推断本身不是事实。"""

    inference_id: StrictStr
    statement: StrictStr
    supports: list[StrictStr] = Field(default_factory=list)
    supporting_text: StrictStr = ""


class ParsedContent(NeroModel):
    """一次解析任务的产物。"""

    document: SourceDocument
    blocks: list[SourceBlock] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    inference: list[ModelInference] = Field(default_factory=list)


__all__ = [
    "BlockKind",
    "ExtractionMode",
    "Fact",
    "FactVerification",
    "ModelInference",
    "ParseStatus",
    "ParsedContent",
    "SourceBlock",
    "SourceDocument",
]
