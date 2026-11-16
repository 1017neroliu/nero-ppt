"""局部修改协议：ChangePlan（先解释范围，确认后才执行）。"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr

from .common import NeroModel

ChangeScope = Literal["page", "batch", "global"]
ChangeAction = Literal["regenerate", "restyle", "reorder", "split", "merge", "delete"]


class ChangeItem(NeroModel):
    """一个修改动作。"""

    page_ids: list[StrictStr] = Field(default_factory=list)
    action: ChangeAction
    instruction: StrictStr = ""


class ChangePlan(NeroModel):
    """变更计划：执行前必须用户确认跨页或全局影响。"""

    change_id: StrictStr = ""
    scope: ChangeScope
    items: list[ChangeItem] = Field(min_length=1)
    affects_outline: StrictBool = False      # 页数/顺序/标题变化
    affects_template: StrictBool = False     # 触发样张重新审核
    affects_timing: StrictBool = False       # 讲述时长变化
    explanation: StrictStr = ""
    target_revision: StrictInt = 0           # 计划基于的大纲/规格修订


__all__ = ["ChangeAction", "ChangeItem", "ChangePlan", "ChangeScope"]
