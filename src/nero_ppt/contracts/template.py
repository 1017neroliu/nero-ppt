"""模板档案协议：TemplateProfile、Layout、Slot。

预算（文字/行数/字号下限）是预筛规则，不能替代真实渲染检查。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr

from .common import NeroModel, VisualIntent

EmuInt = StrictInt

SlotKind = Literal["text", "bullets", "table", "chart", "image", "process"]
CropStrategy = Literal["cover", "contain", "fixed"]
StyleStrategy = Literal["preserve_first_run", "plain", "theme"]


class Slot(NeroModel):
    """可编辑槽位：绑定到具体对象 ID，带内容预算。"""

    slot_id: StrictStr
    object_id: StrictInt  # python-pptx shape_id / OOXML sp id
    kind: SlotKind
    bounds_emu: tuple[EmuInt, EmuInt, EmuInt, EmuInt]  # x, y, w, h（来自模板，只读）
    text_budget_chars: StrictInt = 0
    line_budget: StrictInt = 0
    max_font_pt: StrictInt = 0
    min_font_pt: StrictInt = 0
    required: StrictBool = True
    crop_strategy: CropStrategy = "cover"
    style_strategy: StyleStrategy = "preserve_first_run"
    allowed_kinds: list[SlotKind] = Field(default_factory=list)


class FixedObject(NeroModel):
    """固定对象（背景、Logo、装饰）：只保留，不编辑。"""

    object_id: StrictInt
    kind: StrictStr
    name: StrictStr = ""
    protected: StrictBool = True


class Layout(NeroModel):
    """版式：一个可复用的模板页（来自 layouts/master 或已适配内容页）。"""

    layout_id: StrictStr
    purpose: StrictStr  # 例如 cover / section / bullets / two_column / table / chart / summary
    source_page_kind: Literal["layout", "master", "slide"] = "slide"
    # 源页面引用：layout 用 master+layout 索引；slide 用 slide id
    master_index: StrictInt | None = None
    layout_index: StrictInt | None = None
    source_slide_id: StrictInt | None = None
    slots: list[Slot] = Field(default_factory=list)
    fixed_objects: list[FixedObject] = Field(default_factory=list)
    supported_intents: list[VisualIntent] = Field(default_factory=list)


class FontReport(NeroModel):
    name: StrictStr
    embedded: StrictBool = False
    system_available: StrictBool = True


class CompatibilityIssue(NeroModel):
    """不支持/受限对象的显式报告，不允许静默。"""

    object_id: StrictInt = 0
    object_kind: StrictStr
    severity: Literal["unsupported", "limited", "info"]
    message: StrictStr


class TemplateProfile(NeroModel):
    """模板能力档案：能回指具体模板文件哈希。"""

    template_id: StrictStr
    version: StrictInt = 1
    file_hash: StrictStr
    file_ext: Literal[".pptx", ".potx"]
    slide_width_emu: EmuInt
    slide_height_emu: EmuInt
    layouts: list[Layout] = Field(min_length=1)
    fonts: list[FontReport] = Field(default_factory=list)
    compatibility: list[CompatibilityIssue] = Field(default_factory=list)
    source_file: StrictStr = ""
