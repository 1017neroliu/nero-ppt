"""需求摘要（Brief）与大纲（Outline）协议。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr

from .common import NeroModel, SourceRef, VisualIntent
from .sources import Fact

SceneId = StrictStr


class Brief(NeroModel):
    """任务需求摘要：场景字段缺失先尝试资料提取，再交用户确认。"""

    topic: StrictStr
    audience: StrictStr = ""
    objective: StrictStr = ""
    scene_id: SceneId = "general"
    scene_version: StrictInt = 1
    language: StrictStr = "zh-CN"
    page_range_min: StrictInt = Field(default=5, ge=1)
    page_range_max: StrictInt = Field(default=15, ge=1)
    duration_minutes: StrictInt = Field(default=10, ge=1)
    must_include: list[StrictStr] = Field(default_factory=list)
    forbidden: list[StrictStr] = Field(default_factory=list)
    extra_requirements: StrictStr = ""
    # 资料缺口：生成前需要用户确认或补充
    gaps: list[StrictStr] = Field(default_factory=list)


class OutlinePage(NeroModel):
    """大纲中的页面：目的、结论、证据、候选版式。页码不在此处——顺序即页序。"""

    slide_id: StrictStr
    title: StrictStr = Field(min_length=1, max_length=200)
    purpose: StrictStr = ""
    key_message: StrictStr = ""
    evidence: list[SourceRef] = Field(default_factory=list)
    visual_intents: list[VisualIntent] = Field(default_factory=list)
    is_appendix: StrictBool = False
    target_seconds: StrictInt = Field(default=45, ge=5, le=600)
    fact_ids: list[StrictStr] = Field(default_factory=list)


class Outline(NeroModel):
    """按序页面集合；outline_revision 由存储层管理，不在协议内。"""

    brief: Brief
    pages: list[OutlinePage] = Field(min_length=1)
    notes: StrictStr = ""


class ConflictInfo(NeroModel):
    """同一口径冲突时的显式保留，不自动选边。"""

    fact_ids: list[StrictStr] = Field(min_length=2)
    description: StrictStr


__all__ = ["Brief", "ConflictInfo", "Outline", "OutlinePage"]
