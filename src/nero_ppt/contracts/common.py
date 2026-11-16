"""通用协议约定。

所有持久化 JSON 顶层带 schema_version；未知字段默认拒绝（extra="forbid"）。
坐标单位统一整数 EMU；单位转换仅发生在程序边界，协议层只接受 EMU 整数。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

SCHEMA_VERSION = 1

# 密度意图枚举：模型表达期望的信息密度，不替代容量检查
Density = Literal["sparse", "normal", "dense"]
# 视觉意图枚举：有限集合，决定候选版式
VisualIntent = Literal[
    "title",
    "section",
    "bullets",
    "two_column",
    "table",
    "chart_bar",
    "chart_line",
    "chart_pie",
    "image",
    "image_text",
    "process",
    "quote",
    "summary",
    "appendix",
]

Severity = Literal["info", "warning", "error"]
CheckStatus = Literal["pass", "fail", "not_checked"]


class NeroModel(BaseModel):
    """协议基类：严格模式，拒绝未知字段。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        validate_assignment=True,
        str_strip_whitespace=False,
    )


class Envelope(NeroModel):
    """持久化 JSON 的顶层信封。"""

    schema_version: StrictInt = SCHEMA_VERSION


def envelope(data: NeroModel) -> dict[str, Any]:
    """带 schema_version 的可持久化表示。"""
    return {"schema_version": SCHEMA_VERSION, **data.model_dump(mode="json")}


def from_envelope(data: dict[str, Any], model: type[NeroModel]) -> NeroModel:
    """从信封解析：校验 schema_version 后剥离，再严格验证模型。"""
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version 缺失或不支持: {data.get('schema_version') if isinstance(data, dict) else data}")
    payload = {k: v for k, v in data.items() if k != "schema_version"}
    return model.model_validate(payload)


# ---------------------------------------------------------------- 错误结构


class ErrorDetail(NeroModel):
    code: StrictStr
    message: StrictStr
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: StrictStr = ""


# ---------------------------------------------------------------- 来源引用


class SourceRef(NeroModel):
    """页面/块对来源文档的引用。locator 是格式对应结构，不制造不存在的定位。"""

    source_id: StrictStr
    block_id: StrictStr = ""
    locator: dict[str, Any] = Field(default_factory=dict)
    quote: StrictStr = Field(default="", max_length=2000)


class ReviewComment(NeroModel):
    """审核意见。"""

    comment: StrictStr
    author: StrictStr = ""


__all__ = [
    "SCHEMA_VERSION",
    "CheckStatus",
    "Density",
    "Envelope",
    "ErrorDetail",
    "NeroModel",
    "ReviewComment",
    "Severity",
    "SourceRef",
    "StrictBool",
    "StrictInt",
    "StrictStr",
    "VisualIntent",
    "envelope",
]
