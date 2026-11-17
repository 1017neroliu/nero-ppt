"""任务状态机：phase（阶段）与 status（状态）两层。

转换表与任务书第 5 节一致；pending 的用户批准不得视为超时自动通过。
"""
from __future__ import annotations

from dataclasses import dataclass

PHASES = ("intake", "outline", "sample", "batch", "final")
STATUSES = ("ready", "running", "awaiting_review", "needs_input", "failed",
            "interrupted", "cancelled", "completed")

TERMINAL_STATUSES = {"cancelled", "completed"}


@dataclass(frozen=True)
class State:
    phase: str
    status: str

    def __post_init__(self):
        if self.phase not in PHASES or self.status not in STATUSES:
            raise ValueError(f"非法状态 {self.phase}/{self.status}")

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_running(self) -> bool:
        return self.status == "running"


# 事件 → (是否要求当前 running、目标状态)
# 目标状态中 phase 用 "same" 表示保持当前 phase
TRANSITIONS: dict[str, tuple[str, str]] = {
    "start": ("same", "running"),                      # intake/ready → intake/running
    "parse_done": ("outline", "running"),              # intake/running → outline/running
    "parse_needs_input": ("intake", "needs_input"),
    "outline_done": ("same", "awaiting_review"),       # outline/running → outline/awaiting_review
    "approve_outline": ("sample", "running"),          # outline/awaiting_review → sample/running
    "sample_done": ("same", "awaiting_review"),        # sample/running → sample/awaiting_review
    "approve_sample": ("final", "running"),  # 规格一次全量生成，样张即全稿预览，批准后直接整稿
    "batch_done": ("same", "awaiting_review"),         # batch/running → batch/awaiting_review
    "approve_batch_more": ("batch", "running"),        # 仍有未完成批次
    "approve_batch_final": ("final", "running"),       # 全部批次完成 → final/running
    "final_done": ("same", "awaiting_review"),         # final/running → final/awaiting_review
    "approve_final": ("same", "completed"),            # final/awaiting_review → final/completed
    # 阶段回退（reopen_<phase>）不走本表：transition() 内通用校验
    # （仅待审核/失败/已发布可回退、不可指向未到达阶段），见 transition()
    "cancel": ("same", "cancelled"),
    "fail": ("same", "failed"),
    "retry": ("same", "running"),                      # failed/interrupted → 原 phase/running
    "resume_review": ("same", "awaiting_review"),      # interrupted 恢复到等待审核（最近检查点已完成时）
}


class TransitionError(ValueError):
    pass


def transition(current: State, event: str) -> State:
    """校验并计算目标状态。不修改存储。"""
    if current.is_terminal and not (event == "retry" or event.startswith("reopen_")):
        raise TransitionError(f"终态 {current.phase}/{current.status} 不能执行 {event}")
    if event.startswith("reopen_"):
        # 阶段切换（向导式导航）：非运行中状态均可切换（含 intake 的待启动/待补充、
        # 中断态）；目标是否已到达由服务层水印校验。
        target = event[len("reopen_"):]
        if target not in PHASES:
            raise TransitionError(f"未知回退目标 {target}")
        if current.is_running:
            raise TransitionError(
                f"状态 {current.phase}/{current.status} 正在执行，完成后才能切换步骤")
        return State(target, "ready" if target == "intake" else "awaiting_review")
    if event == "retry":
        if current.status not in ("failed", "interrupted"):
            raise TransitionError(f"仅 failed/interrupted 可重试，当前 {current.status}")
        return State(current.phase, "running")
    if event == "cancel":
        if current.is_terminal:
            raise TransitionError("终态不能取消")
        return State(current.phase, "cancelled")
    if event == "fail":
        if not current.is_running:
            raise TransitionError(f"仅 running 可失败，当前 {current.status}")
        return State(current.phase, "failed")
    target_phase, target_status = TRANSITIONS[event]
    # 前置校验：事件与当前阶段/状态匹配
    _precheck(current, event)
    phase = current.phase if target_phase == "same" else target_phase
    return State(phase, target_status)


def _precheck(current: State, event: str) -> None:
    rules = {
        "start": (State("intake", "ready"),),
        "parse_done": (State("intake", "running"),),
        "parse_needs_input": (State("intake", "running"),),
        "outline_done": (State("outline", "running"),),
        "approve_outline": (State("outline", "awaiting_review"),),
        "sample_done": (State("sample", "running"),),
        "approve_sample": (State("sample", "awaiting_review"),),
        "batch_done": (State("batch", "running"),),
        "approve_batch_more": (State("batch", "awaiting_review"),),
        "approve_batch_final": (State("batch", "awaiting_review"),),
        "final_done": (State("final", "running"),),
        "approve_final": (State("final", "awaiting_review"),),
        # reopen_* 事件由 transition() 内通用分支校验，不在本表
    }
    if event in rules and current not in rules[event]:
        raise TransitionError(
            f"事件 {event} 要求状态 {[f'{s.phase}/{s.status}' for s in rules[event]]}，当前 {current.phase}/{current.status}")
