"""S09 门槛 C 集成测试：状态机、Worker 领取/恢复/取消、幂等与确定性渲染链路。"""
import json
from pathlib import Path

import pytest

from nero_ppt.contracts import SlideSpec, TemplateProfile, from_envelope
from nero_ppt.db import connection as dbconn
from nero_ppt.repositories import ConflictError
from nero_ppt.services.context import build_context
from nero_ppt.settings import Settings
from nero_ppt.workflow import State, TransitionError, Worker, transition
from nero_ppt.workflow.states import TERMINAL_STATUSES

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "assets" / "templates" / "builtin-classic.pptx"
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture()
def ctx(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "nero.db",
                        auth_mode="demo", session_secret="s" * 32,
                        model_api_key="", model_name="")
    ctx = build_context(settings)
    ctx.conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1','owner','x','now')")
    return ctx


def test_transition_table_complete():
    """任务书第 5 节转换表逐行验证。"""
    assert transition(State("intake", "ready"), "start") == State("intake", "running")
    assert transition(State("intake", "running"), "parse_done") == State("outline", "running")
    assert transition(State("intake", "running"), "parse_needs_input") == State("intake", "needs_input")
    assert transition(State("outline", "running"), "outline_done") == State("outline", "awaiting_review")
    assert transition(State("outline", "awaiting_review"), "approve_outline") == State("sample", "running")
    assert transition(State("sample", "running"), "sample_done") == State("sample", "awaiting_review")
    assert transition(State("sample", "awaiting_review"), "approve_sample") == State("final", "running")
    assert transition(State("batch", "running"), "batch_done") == State("batch", "awaiting_review")
    assert transition(State("batch", "awaiting_review"), "approve_batch_final") == State("final", "running")
    assert transition(State("final", "running"), "final_done") == State("final", "awaiting_review")
    assert transition(State("final", "awaiting_review"), "approve_final") == State("final", "completed")
    # 阶段回退：后续阶段回到大纲修改（含已发布终态）
    for src in (State("sample", "awaiting_review"), State("sample", "failed"),
                State("batch", "awaiting_review"), State("batch", "failed"),
                State("final", "awaiting_review"), State("final", "failed"),
                State("final", "completed")):
        assert transition(src, "reopen_outline") == State("outline", "awaiting_review")
    # 阶段切换（向导导航）：非运行中状态均可切换；目标是否已到达由服务层水印校验，
    # 同阶段重复切换由服务层拦（transition 层不做阶段相等检查）
    assert transition(State("outline", "awaiting_review"), "reopen_outline") == State("outline", "awaiting_review")
    assert transition(State("intake", "ready"), "reopen_sample") == State("sample", "awaiting_review")
    assert transition(State("intake", "needs_input"), "reopen_final") == State("final", "awaiting_review")
    assert transition(State("sample", "interrupted"), "reopen_outline") == State("outline", "awaiting_review")
    with pytest.raises(TransitionError):
        transition(State("final", "running"), "reopen_outline")  # 生成中不可切换
    with pytest.raises(TransitionError):
        transition(State("intake", "running"), "reopen_sample")  # 解析中不可切换
    # 异常路径
    assert transition(State("batch", "running"), "cancel") == State("batch", "cancelled")
    assert transition(State("final", "running"), "fail") == State("final", "failed")
    assert transition(State("batch", "failed"), "retry") == State("batch", "running")
    assert transition(State("intake", "interrupted"), "retry") == State("intake", "running")
    # 非法转换
    with pytest.raises(TransitionError):
        transition(State("intake", "ready"), "approve_outline")  # 未到大纲审核
    with pytest.raises(TransitionError):
        transition(State("final", "completed"), "cancel")  # 终态
    with pytest.raises(TransitionError):
        transition(State("intake", "ready"), "retry")  # 非 failed 不可重试


def _job_with_material(ctx) -> str:
    job_id = ctx.jobs.create("u1", "季度汇报", "business-review", 1)
    data = (FIX / "sample-notes.txt").read_bytes()
    path, sha, size = ctx.files.write_stream(job_id, "inputs", "sample-notes.txt", iter([data]))
    ctx.records.add_document(job_id, "sample-notes.txt", "material", ".txt", sha, size,
                             str(path.relative_to(ctx.settings.data_dir)))
    return job_id


def test_parse_step_full_flow_and_model_gate(ctx):
    """parse 成功 → outline/running；outline 因模型未配置显式失败（不伪造）。"""
    job_id = _job_with_material(ctx)
    rev = ctx.jobs.get(job_id)["job_revision"]
    ctx.jobs.update_state(job_id, rev, status="running")
    step_id = ctx.steps.enqueue(job_id, "parse", input_revision=ctx.jobs.get(job_id)["job_revision"])
    w = Worker(ctx)
    assert w.run_once() is True  # parse
    job = ctx.jobs.get(job_id)
    assert job["phase"] == "outline" and job["status"] == "running"
    # parse 产物与事实已登记
    docs = ctx.records.documents_for_job(job_id, kind="material")
    assert docs[0]["parse_status"] == "ok"
    parsed_rel = f"jobs/{job_id}/parsed/{docs[0]['id']}.json"
    assert ctx.files.resolve(parsed_rel).exists()
    # outline 步骤已入队（幂等入队，重复点击只入队一次）
    s1 = ctx.steps.enqueue(job_id, "outline", input_revision=job["job_revision"])
    s2 = ctx.steps.enqueue(job_id, "outline", input_revision=job["job_revision"])
    assert s1 == s2
    assert w.run_once() is True  # outline → 失败（模型未配置）
    step = ctx.steps.get(s1)
    assert step["status"] == "failed" and "模型未配置" in step["error"]
    job = ctx.jobs.get(job_id)
    assert job["status"] == "failed" and "模型未配置" in job["error"]
    # retry：failed → running 重新入队（仍失败，但可恢复路径存在）
    new_rev = ctx.jobs.update_state(job_id, job["job_revision"], status="running")
    ctx.steps.requeue(s1)
    row = ctx.jobs.claim_next_step_atomic("w2")
    assert row is not None and row["id"] == s1


def test_cancel_prevents_publishing(ctx):
    """取消后：pending 步骤作废、在途文件不成为当前产物。"""
    job_id = _job_with_material(ctx)
    rev = ctx.jobs.update_state(job_id, 1, status="running")
    ctx.steps.enqueue(job_id, "parse", input_revision=rev)
    new_rev = ctx.jobs.update_state(job_id, rev, status="cancelled")
    row = ctx.jobs.claim_next_step_atomic("w1")
    assert row is None  # cancelled 任务不再领取
    # 已领取后取消：execute 时检查
    rev2 = ctx.jobs.update_state(job_id, new_rev, status="running")
    ctx.steps.enqueue(job_id, "parse", input_revision=rev2)
    w = Worker(ctx)
    row = ctx.jobs.claim_next_step_atomic("w1")
    ctx.jobs.update_state(job_id, rev2, status="cancelled")
    w.execute(row)
    step = ctx.steps.get(row["id"])
    assert "已取消" in step["error"]


def test_worker_recovery_from_kill(ctx):
    """Worker 被终止后从最近检查点恢复：running → interrupted → retry。"""
    job_id = _job_with_material(ctx)
    rev = ctx.jobs.update_state(job_id, 1, status="running")
    step_id = ctx.steps.enqueue(job_id, "parse", input_revision=rev)
    # 模拟旧进程死亡：running 状态 + 过期租约
    ctx.conn.execute(
        "UPDATE steps SET status='running', lease_owner='dead-worker',"
        " lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (step_id,))
    w = Worker(ctx)
    assert w.recover() == 1
    assert ctx.steps.get(step_id)["status"] == "interrupted"
    # 恢复：requeue 后新 Worker 重新领取同一 job
    ctx.steps.requeue(step_id)
    assert w.run_once() is True
    assert ctx.steps.get(step_id)["status"] == "done"


def test_second_worker_does_not_reset_running(ctx):
    """第二个 Worker 启动不能重置正在执行的步骤（租约未过期）。"""
    job_id = _job_with_material(ctx)
    rev = ctx.jobs.update_state(job_id, 1, status="running")
    ctx.steps.enqueue(job_id, "parse", input_revision=rev)
    w1 = Worker(ctx)
    row = ctx.jobs.claim_next_step_atomic(w1.worker_id)  # 领取但不执行（模拟执行中）
    assert row is not None
    assert ctx.steps.get(row["id"])["status"] == "running"
    w2 = Worker(ctx)
    n = w2.recover()  # 新 Worker 恢复：running 且租约未过期的不动
    assert n == 0
    assert ctx.steps.get(row["id"])["status"] == "running"


def test_deterministic_render_batch_end_to_end(ctx):
    """模板驱动确定性链路（无模型）：render_batch → PPTX+Notes+PDF+缩略图 → awaiting_review。"""
    # 1) 上传模板并建档
    job_id = ctx.jobs.create("u1", "手工规格渲染", "general", 1)
    tpl_bytes = TEMPLATE.read_bytes()
    tpl_path, tpl_sha, tpl_size = ctx.files.write_stream(
        job_id, "inputs", "builtin-classic.pptx", iter([tpl_bytes]))
    tpl_rel = str(tpl_path.relative_to(ctx.settings.data_dir))
    ctx.records.add_document(job_id, "builtin-classic.pptx", "template", ".pptx", tpl_sha, tpl_size, tpl_rel)
    from nero_ppt.templates_engine import build_profile, inspect_template

    profile = build_profile(inspect_template(TEMPLATE), "builtin-classic")
    profile_id = ctx.records.save_profile("builtin-classic", 1, profile.file_hash,
                                          json.loads(profile.model_dump_json()), job_id=job_id)
    ctx.jobs.update_state(job_id, 1, template_profile_id=profile_id)
    # 模板文件复制到 render_service 约定路径
    import shutil

    conv = ctx.settings.data_dir / f"jobs/{job_id}/inputs/template1{profile.file_hash[:8]}.pptx"
    conv.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATE, conv)
    # 复制内置模板到共享 templates 目录兜底
    (ctx.settings.data_dir / "templates").mkdir(exist_ok=True)
    shutil.copyfile(TEMPLATE, ctx.settings.data_dir / "templates" / "builtin-classic.pptx")

    # 2) 手工 SlideSpec（S11 将由模型生成；此处验证确定性链路）
    specs = [
        from_envelope({
            "schema_version": 1,
            "slide_id": "cover-1", "purpose": "封面", "headline": "链路验证封面",
            "visual_intent": "title",
            "speaker_notes": {"script": "封面讲稿", "transition": "下一页"},
        }, SlideSpec),
        from_envelope({
            "schema_version": 1,
            "slide_id": "chart-1", "headline": "季度收入趋势（万元）",
            "visual_intent": "chart_bar",
            "body_blocks": [{"kind": "chart", "categories": ["Q1", "Q2", "Q3"],
                             "series": [{"name": "收入", "values": [3950, 4288, 4820]}]}],
            "speaker_notes": {"script": "图表讲稿"},
        }, SlideSpec),
        from_envelope({
            "schema_version": 1,
            "slide_id": "sum-1", "headline": "总结",
            "visual_intent": "summary",
            "body_blocks": [{"kind": "bullets", "items": ["链路正常", "数据齐全"]}],
            "speaker_notes": {"script": "总结讲稿"},
        }, SlideSpec),
    ]
    rev = ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"],
                                phase="sample", status="running")
    step_id = ctx.steps.enqueue(job_id, "render_batch", input_revision=rev,
                                scope_key="sample",
                                payload={"specs_inline": [s.model_dump() for s in specs],
                                         "profile_id": profile_id})
    # 3) Worker 执行
    w = Worker(ctx)
    assert w.run_once() is True
    step = ctx.steps.get(step_id)
    assert step["status"] == "done", step["error"]
    result = json.loads(step["result_json"])
    assert result["still_blocking"] is False, result["qa_blocking"]
    # 4) 产物校验
    job = ctx.jobs.get(job_id)
    assert job["phase"] == "sample" and job["status"] == "awaiting_review"
    pptx = ctx.files.resolve(result["pptx"])
    pdf = ctx.files.resolve(result["pdf"])
    assert pptx.exists() and pptx.stat().st_size > 0
    assert pdf.exists() and pdf.stat().st_size > 0
    from nero_ppt.rendering.libreoffice import pdf_page_count

    assert pdf_page_count(pdf) == 3
    arts = ctx.records.artifacts_for_revision(job_id, result["revision"])
    kinds = {a["kind"] for a in arts}
    assert {"pptx", "pdf", "thumbnail"} <= kinds
    # Notes 写入验证
    from pptx import Presentation

    prs = Presentation(str(pptx))
    assert "封面讲稿" in prs.slides[0].notes_slide.notes_text_frame.text
    # 5) 步骤重试不重复发布（同输入重跑 → artifacts 幂等）
    ctx.steps.requeue(step_id)
    row = ctx.jobs.claim_next_step_atomic("w2")
    w.execute(row)
    arts2 = ctx.records.artifacts_for_revision(job_id, result["revision"])
    assert len(arts2) == len(arts)  # 唯一约束保证不重复登记


def test_needs_input_flow(ctx):
    """扫描件任务：parse → intake/needs_input（不生成空内容）。"""
    from PIL import Image
    import io

    job_id = ctx.jobs.create("u1", "扫描件任务", "general", 1)
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, format="PDF")
    path, sha, size = ctx.files.write_stream(job_id, "inputs", "scan.pdf", iter([buf.getvalue()]))
    ctx.records.add_document(job_id, "scan.pdf", "material", ".pdf", sha, size,
                             str(path.relative_to(ctx.settings.data_dir)))
    rev = ctx.jobs.update_state(job_id, 1, status="running")
    ctx.steps.enqueue(job_id, "parse", input_revision=rev)
    Worker(ctx).run_once()
    job = ctx.jobs.get(job_id)
    assert job["phase"] == "intake" and job["status"] == "needs_input"
    assert "扫描" in (job["error"] or "")


def test_outline_uses_job_bound_model_not_env(ctx, monkeypatch):
    """回归：大纲生成必须用任务绑定的模型（页面配置），而非仅环境变量。

    复现用户场景：models.json 配了可用模型、无 NERO_MODEL_API_KEY 环境变量，
    旧实现报"模型未配置：大纲生成需要 NERO_MODEL_API_KEY"。
    """
    import json as _json

    # 用户场景：models.json 一个可用模型，环境变量未配置
    cfg_dir = ctx.settings.data_dir / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "models.json").write_text(_json.dumps({
        "default": "m1",
        "models": [{"id": "m1", "name": "用户模型", "model_name": "gpt-test",
                    "base_url": "https://llmops.example/v1", "api_key": "sk-user"}],
    }, ensure_ascii=False), encoding="utf-8")
    import nero_ppt.services.models as M

    M.load_catalog._cache = None

    # 模拟真实调用返回合法大纲
    from nero_ppt.providers import base as pb

    def fake_generate(self, system, user, *, max_tokens=4000, json_mode=True):
        return {"brief": {"topic": "季度汇报", "gaps": []},
                "pages": [{"slide_id": "p1", "title": "封面", "purpose": "封面"},
                          {"slide_id": "p2", "title": "指标", "purpose": "指标",
                           "visual_intents": ["bullets"]}]}, pb.Usage()

    monkeypatch.setattr(pb.OpenAIProvider, "generate_json", fake_generate)
    assert not ctx.settings.model_available  # 环境变量确实未配置

    job_id = _job_with_material(ctx)
    # 绑定模型（页面创建任务时选择）
    ctx.conn.execute("UPDATE jobs SET model_profile_id='m1' WHERE id=?", (job_id,))
    ctx.jobs.update_state(job_id, 1, status="running")
    ctx.steps.enqueue(job_id, "parse", input_revision=2)
    w = Worker(ctx)
    assert w.run_once()  # parse
    job = ctx.jobs.get(job_id)
    assert job["phase"] == "outline" and job["status"] == "running"
    assert w.run_once()  # outline：应用任务绑定的模型
    step = [s for s in ctx.steps.list_for_job(job_id) if s["kind"] == "outline"][0]
    assert step["status"] == "done", step["error"]
    result = _json.loads(step["result_json"])
    assert result["provider"] == "openai" and result["outline_revision"] >= 1
    job = ctx.jobs.get(job_id)
    assert job["status"] == "awaiting_review"  # 大纲生成成功待审核


def test_full_flow_outline_to_sample_with_fake_provider(ctx, monkeypatch):
    """端到端回归：大纲批准 → generate_specs（FakeProvider）→ render_batch → 样张可审核。

    复现用户场景：修复前批准大纲直接渲染，缺 SlideSpec 生成环节报
    "任务没有页面规格快照，无法渲染"。
    """
    import json as _json

    cfg_dir = ctx.settings.data_dir / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "models.json").write_text(_json.dumps({
        "default": "m1",
        "models": [{"id": "m1", "name": "用户模型", "model_name": "gpt-test",
                    "base_url": "https://x/v1", "api_key": "sk"}],
    }), encoding="utf-8")
    import nero_ppt.services.models as M

    M.load_catalog._cache = None

    from nero_ppt.providers import base as pb

    def fake_generate(self, system, user, *, max_tokens=4000, json_mode=True):
        if "SlideSpec" in user:
            return {"headline": "生成的页面标题",
                    "body_blocks": [{"kind": "bullets", "items": ["要点甲", "要点乙"]}],
                    "visual_intent": "bullets",
                    "speaker_notes": {"script": "生成的讲稿"}}, pb.Usage()
        return {"brief": {"topic": "季度汇报", "gaps": []},
                "pages": [{"slide_id": "p1", "title": "封面", "purpose": "封面"},
                          {"slide_id": "p2", "title": "进展", "purpose": "进展",
                           "visual_intents": ["bullets"]}]}, pb.Usage()

    monkeypatch.setattr(pb.OpenAIProvider, "generate_json", fake_generate)

    # 准备：模板就位 + 任务绑定模型 + 资料上传
    import shutil

    (ctx.settings.data_dir / "templates").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "assets" / "templates" / "builtin-classic.pptx",
                    ctx.settings.data_dir / "templates" / "builtin-classic.pptx")
    job_id = _job_with_material(ctx)
    ctx.conn.execute("UPDATE jobs SET model_profile_id='m1' WHERE id=?", (job_id,))
    # 绑定内置模板档案（页面 start 会自动完成；此处直接构造）
    from nero_ppt.templates_engine import build_profile as _bp, inspect_template as _it

    _prof = _bp(_it(ROOT / "assets" / "templates" / "builtin-classic.pptx"), "builtin-classic")
    _pid = ctx.records.save_profile("builtin-classic", 1, _prof.file_hash,
                                    _json.loads(_prof.model_dump_json()))
    ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"],
                          template_profile_id=_pid)
    rev = ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"], status="running")
    ctx.steps.enqueue(job_id, "parse", input_revision=rev)
    w = Worker(ctx)
    assert w.run_once()  # parse
    assert w.run_once()  # outline（FakeProvider）
    job = ctx.jobs.get(job_id)
    assert job["status"] == "awaiting_review", step["error"] if (step := ctx.steps.get(
        [s for s in ctx.steps.list_for_job(job_id) if s["kind"] == "outline"][0]["id"])) else ""

    # 批准大纲 → sample/running + generate_specs 入队
    from nero_ppt.services.jobs_service import JobsService

    svc = JobsService(ctx)
    r = svc.review("u1", job_id, "outline", "approve", "", job["job_revision"])
    assert ctx.jobs.get(job_id)["phase"] == "sample"
    # generate_specs → 自动入队 render_batch → 渲染完成
    assert w.run_once()  # generate_specs
    spec_step = [s for s in ctx.steps.list_for_job(job_id) if s["kind"] == "generate_specs"][0]
    assert spec_step["status"] == "done", spec_step["error"]
    assert w.run_once()  # render_batch
    render_step = [s for s in ctx.steps.list_for_job(job_id) if s["kind"] == "render_batch"][0]
    assert render_step["status"] == "done", render_step["error"]
    job = ctx.jobs.get(job_id)
    assert job["phase"] == "sample" and job["status"] == "awaiting_review"
    # 样张产物齐全
    result = _json.loads(render_step["result_json"])
    assert ctx.files.resolve(result["pptx"]).exists()
    assert ctx.files.resolve(result["pdf"]).exists()
    from nero_ppt.rendering.libreoffice import pdf_page_count

    assert pdf_page_count(ctx.files.resolve(result["pdf"])) == 2
    print("端到端链路验证通过：大纲 → 规格 → 样张")


def test_render_batch_self_heals_missing_specs(ctx):
    """旧任务自愈：断环修复前入队的渲染步骤发现无规格 → 自动转交规格生成。"""
    import shutil

    (ctx.settings.data_dir / "templates").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "assets" / "templates" / "builtin-classic.pptx",
                    ctx.settings.data_dir / "templates" / "builtin-classic.pptx")
    job_id = _job_with_material(ctx)
    rev = ctx.jobs.update_state(job_id, 1, status="running")
    # 旧任务状态：直接入队渲染（无 specs 快照）
    ctx.steps.enqueue(job_id, "render_batch", input_revision=rev,
                      scope_key="sample", payload={"scope_key": "sample"})
    w = Worker(ctx)
    assert w.run_once()  # parse → outline 入队
    ctx.conn.execute("UPDATE steps SET status='cancelled' WHERE job_id=? AND kind='outline'", (job_id,))
    # 重跑渲染步骤（模拟旧任务卡死的场景：手动 requeue 渲染步骤）
    rstep = [s for s in ctx.steps.list_for_job(job_id) if s["kind"] == "render_batch"][0]
    # 让 outline 先跑完？不：直接把任务推进到 sample/running 并重试渲染步骤
    ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"],
                          phase="sample", status="running")
    ctx.steps.requeue(rstep["id"])
    assert w.run_once()  # render_batch → 发现无规格 → 转交 generate_specs
    rstep2 = ctx.steps.get(rstep["id"])
    assert rstep2["status"] == "done"  # 转交完成（非失败卡死）
    import json as _json

    result = _json.loads(rstep2["result_json"])
    assert result.get("handed_off") is True
    gen_step_id = result["generate_specs_step"]
    gen = ctx.steps.get(gen_step_id)
    assert gen["kind"] == "generate_specs" and gen["status"] in ("pending", "running", "done")
