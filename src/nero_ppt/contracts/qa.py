"""QAReport 协议：检查结果严格区分 pass / fail / not_checked。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictInt, StrictStr

from .common import CheckStatus, NeroModel, Severity

CheckId = StrictStr


class QACheck(NeroModel):
    check_id: CheckId
    check_name: StrictStr
    object_id: StrictInt = 0
    slot_id: StrictStr = ""
    status: CheckStatus
    severity: Severity = "error"
    observed: StrictStr = ""
    expected: StrictStr = ""
    suggested_action: StrictStr = ""


class RepairRound(NeroModel):
    """修复记录：轮次、动作、结果。上限由配置控制。"""

    round_index: StrictInt
    actions: list[StrictStr] = Field(default_factory=list)
    result: Literal["improved", "no_change", "worse", "stopped"] = "improved"


class QAReport(NeroModel):
    slide_id: StrictStr
    revision: StrictInt = 1
    artifact_hash: StrictStr = ""  # 被检查产物的哈希，保证记录对应实际文件
    checks: list[QACheck] = Field(default_factory=list)
    repair_rounds: list[RepairRound] = Field(default_factory=list)
    visual_checked: bool = False

    def failed(self) -> list[QACheck]:
        return [c for c in self.checks if c.status == "fail"]

    def blocking(self) -> list[QACheck]:
        """阻塞性失败：error 级 fail。"""
        return [c for c in self.failed() if c.severity == "error"]

    def has_not_checked(self) -> bool:
        return any(c.status == "not_checked" for c in self.checks)


__all__ = ["CheckStatus", "QACheck", "QAReport", "RepairRound"]
