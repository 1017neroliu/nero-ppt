"""Worker：单实例串行执行步骤；数据库领取，不持锁执行；支持取消与恢复。"""
from __future__ import annotations

import json
import logging
import socket
import time
import uuid

from ..services.context import WebContext
from . import states
from .handlers import HANDLERS, StepContext

log = logging.getLogger("nero.worker")


class Worker:
    def __init__(self, ctx: WebContext, poll_interval: float = 2.0):
        self.ctx = ctx
        self.poll_interval = poll_interval
        self.worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

    def recover(self) -> int:
        """启动恢复：标记残留 running（非本 Worker 租约）为 interrupted。"""
        n = self.ctx.steps.mark_interrupted_running(exclude_owner=self.worker_id)
        if n:
            log.warning("恢复：将 %d 个残留 running 步骤标记 interrupted", n)
        return n

    def run_forever(self) -> None:
        self.recover()
        while True:
            worked = self.run_once()
            if not worked:
                time.sleep(self.poll_interval)

    def run_once(self) -> bool:
        """领取并处理一个步骤；返回是否领取到任务。"""
        row = self.ctx.jobs.claim_next_step_atomic(self.worker_id)
        if row is None:
            return False
        self.execute(row)
        return True

    def execute(self, step_row) -> None:
        step_id = step_row["id"]
        job_id = step_row["job_id"]
        kind = step_row["kind"]
        handler = HANDLERS.get(kind)
        try:
            payload = json.loads(step_row["payload_json"]) if step_row["payload_json"] else {}
            checkpoint = json.loads(step_row["checkpoint_json"]) if step_row["checkpoint_json"] else {}
            job = self.ctx.jobs.get(job_id)
            if job is None:
                self.ctx.steps.fail(step_id, "任务不存在")
                return
            if job["status"] == "cancelled":
                # 取消后在途结果失去发布资格
                self.ctx.steps.fail(step_id, "任务已取消，步骤作废")
                return
            if handler is None:
                self.ctx.steps.fail(step_id, f"未知步骤类型 {kind}")
                self.ctx.jobs.update_state(job_id, job["job_revision"], status="failed",
                                           error=f"未知步骤类型 {kind}")
                return
            step = StepContext(ctx=self.ctx, job_id=job_id, step_id=step_id,
                               input_revision=step_row["input_revision"],
                               payload=payload, job_row=job, checkpoint=checkpoint)
            result = handler(step)
            if result.get("cancelled"):
                self.ctx.steps.fail(step_id, "任务已取消，步骤作废")
                return
            self.ctx.steps.finish(step_id, result)
            log.info("步骤完成 job=%s kind=%s", job_id, kind)
        except Exception as exc:  # noqa: BLE001
            log.exception("步骤失败 job=%s kind=%s", job_id, kind)
            self.ctx.steps.fail(step_id, f"{type(exc).__name__}: {exc}")
            try:
                job = self.ctx.jobs.get(job_id)
                if job is not None and job["status"] == "running":
                    cur = states.State(job["phase"], job["status"])
                    new = states.transition(cur, "fail")
                    self.ctx.jobs.update_state(job_id, job["job_revision"],
                                               phase=new.phase, status=new.status,
                                               error=f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                log.exception("标记任务失败状态时出错 job=%s", job_id)
