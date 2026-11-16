"""规则 QA 与有界修复（门槛 B）。

检查结果严格区分 pass / fail / not_checked；文字容量预检只给风险提示；
自动修复只允许已验收槽位内调整，最多 N 轮（默认 2），重复无改善即停止。
"""
from .engine import run_qa
from .repair import repair_slide, run_repair_loop

__all__ = ["repair_slide", "run_qa", "run_repair_loop"]
