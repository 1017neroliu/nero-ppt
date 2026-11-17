"""RenderPlan 协议：程序生成的受控编辑计划。

只由程序生成；模型不调用底层 XML 操作，不产生坐标。
坐标全部来自模板槽位 bounds_emu 或经验收的内置布局规则。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictInt, StrictStr

from .common import NeroModel

EditAction = Literal[
    "set_text", "set_bullets", "set_table", "set_chart_data",
    "replace_image", "none",
]
BindingSource = Literal["spec", "outline", "constant", "resource"]


class SlotBinding(NeroModel):
    """槽位绑定：把内容块绑定到模板槽位。"""

    slot_id: StrictStr
    object_id: StrictInt
    source: BindingSource
    # spec 引用：slide_spec 的 body_blocks 索引；constant 直接文本；resource 资源 ID
    block_index: StrictInt | None = None
    constant_text: StrictStr = ""
    # 多行文本（bullets）：每行一条；line_levels 对应缩进级别（0 主/1 子）
    text_lines: list[StrictStr] = Field(default_factory=list)
    line_levels: list[StrictInt] = Field(default_factory=list)
    resource_id: StrictStr = ""
    edit_action: EditAction = "set_text"
    # 标题单行宽度自适应：渲染写入文本后按生效字号估算宽度，超出则缩小 run 字号
    fit_font: bool = False
    # 自适应字号下限（来自模板槽位 min_font_pt，0 时渲染层用默认 12pt）
    fit_min_font_pt: int = 0


class RenderPlan(NeroModel):
    """单页渲染计划。"""

    slide_id: StrictStr
    revision: StrictInt = 1
    template_version: StrictInt = 1
    layout_id: StrictStr
    # 版式来源页：复制该页作为新页的基础
    source_slide_id: StrictInt | None = None
    master_index: StrictInt | None = None
    layout_index: StrictInt | None = None
    slot_bindings: list[SlotBinding] = Field(default_factory=list)
    notes_text: StrictStr = ""
    resources: list[StrictStr] = Field(default_factory=list)


__all__ = ["EditAction", "RenderPlan", "SlotBinding"]
