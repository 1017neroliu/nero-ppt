"""SlideSpec 协议：模型产出的语义页面规格。

body_blocks 使用有标签联合类型；禁止 x/y/w/h 坐标和可执行代码——
协议层通过 extra="forbid" 和字段白名单拒绝坐标字段。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import Field, StrictBool, StrictInt, StrictStr

from .common import Density, NeroModel, SourceRef, VisualIntent


class TextBullets(NeroModel):
    """文本要点：每个要点一条，支持两级缩进。"""

    kind: Literal["bullets"] = "bullets"
    label: StrictStr = ""
    items: list[StrictStr] = Field(min_length=1, max_length=30)
    items_sub: list[StrictStr] = Field(default_factory=list)


class TableBlock(NeroModel):
    """表格：首行是表头。"""

    kind: Literal["table"] = "table"
    label: StrictStr = ""
    headers: list[StrictStr] = Field(min_length=1, max_length=12)
    rows: list[list[StrictStr]] = Field(min_length=1, max_length=60)
    note: StrictStr = ""


class DataSeries(NeroModel):
    """数据序列：供图表绑定。"""

    name: StrictStr
    values: list[StrictInt | float]


class ChartBlock(NeroModel):
    """图表：类别 + 数据序列（原生柱状/折线/饼图）。"""

    kind: Literal["chart"] = "chart"
    label: StrictStr = ""
    chart_type: Literal["bar", "line", "pie"] = "bar"
    categories: list[StrictStr] = Field(min_length=1, max_length=40)
    series: list[DataSeries] = Field(min_length=1, max_length=6)
    note: StrictStr = ""


class ImageRef(NeroModel):
    """图片引用：引用已上传/已存在的资源 ID，不接受任意路径。"""

    kind: Literal["image"] = "image"
    label: StrictStr = ""
    resource_id: StrictStr
    caption: StrictStr = ""


class ProcessStepBlock(NeroModel):
    """流程：步骤序列（步骤名 + 说明）。"""

    kind: Literal["process"] = "process"
    label: StrictStr = ""
    steps: list[StrictStr] = Field(min_length=2, max_length=8)
    note: StrictStr = ""


BodyBlock = Annotated[
    Union[TextBullets, TableBlock, ChartBlock, ImageRef, ProcessStepBlock],
    Field(discriminator="kind"),
]


class SpeakerNotes(NeroModel):
    """逐页讲稿：口述正文 + 转场 + 数据解释。target_seconds 放页面一级。"""

    script: StrictStr = Field(min_length=1, max_length=8000)
    transition: StrictStr = ""
    source_explanations: list[StrictStr] = Field(default_factory=list)


class SlideSpec(NeroModel):
    """单页内容规格：语义层，不含任何坐标。"""

    slide_id: StrictStr
    purpose: StrictStr = ""
    headline: StrictStr = Field(min_length=1, max_length=200)
    body_blocks: list[BodyBlock] = Field(default_factory=list, max_length=8)
    visual_intent: VisualIntent = "bullets"
    density: Density = "normal"
    source_refs: list[SourceRef] = Field(default_factory=list)
    must_keep_fact_ids: list[StrictStr] = Field(default_factory=list)
    speaker_notes: SpeakerNotes
    target_seconds: StrictInt = Field(default=45, ge=5, le=600)
    is_appendix: StrictBool = False
    # 大纲回指：由程序填充，模型不填
    outline_page_index: StrictInt = Field(default=0, ge=0)


__all__ = [
    "BodyBlock",
    "ChartBlock",
    "DataSeries",
    "ImageRef",
    "ProcessStepBlock",
    "SlideSpec",
    "SpeakerNotes",
    "TableBlock",
    "TextBullets",
]
