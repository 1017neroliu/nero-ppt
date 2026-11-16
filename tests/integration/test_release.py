"""S14 门槛 E 集成测试：整稿组装、发布清单、发布事务与失败回退。"""
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nero_ppt.app import create_app
from nero_ppt.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "assets" / "templates" / "builtin-classic.pptx"
FIX = ROOT / "tests" / "fixtures"

SPECS = [{
    "slide_id": "cover-1", "headline": "季度经营汇报", "visual_intent": "title",
    "speaker_notes": {"script": "封面讲稿", "transition": "看指标"},
}, {
    "slide_id": "bullets-1", "headline": "核心指标", "visual_intent": "bullets",
    "body_blocks": [{"kind": "bullets", "items": ["收入 4,820 万元", "毛利率 38.2%"]}],
    "speaker_notes": {"script": "指标讲稿"},
}, {
    "slide_id": "chart-1", "headline": "季度收入趋势（万元）", "visual_intent": "chart_bar",
    "body_blocks": [{"kind": "chart", "categories": ["Q1", "Q2", "Q3"],
                     "series": [{"name": "收入", "values": [3950, 4288, 4820]}]}],
    "speaker_notes": {"script": "图表讲稿"},
}, {
    "slide_id": "summary-1", "headline": "总结", "visual_intent": "summary",
    "body_blocks": [{"kind": "bullets", "items": ["四季度冲刺 5,500 万元"]}],
    "speaker_notes": {"script": "总结讲稿", "transition": "结束"},
}]


@pytest.fixture()
def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "nero.db",
                        auth_mode="demo", session_secret="s" * 32, model_api_key="", model_name="")
    application = create_app(settings)
    ctx = application.state.ctx
    (ctx.settings.data_dir / "templates").mkdir(exist_ok=True)
    import shutil

    shutil.copyfile(TEMPLATE, ctx.settings.data_dir / "templates" / "builtin-classic.pptx")
    return TestClient(application), application.state.ctx


def _push_to_final(client, ctx) -> str:
    """构造到 final/awaiting_review 的任务（渲染 sample → 批准 → final/running → assemble）。"""
    r = client.post("/api/jobs", json={"title": "整稿任务", "scene_id": "general"},
                    headers={"origin": "http://testserver"})
    job_id = r.json()["job_id"]
    client.post(f"/api/jobs/{job_id}/documents",
                files={"file": ("a.txt", io.BytesIO("收入 4,820 万元".encode()), "text/plain")},
                headers={"origin": "http://testserver"})
    rev = client.post(f"/api/jobs/{job_id}/start", json={"expected_revision": 1},
                      headers={"origin": "http://testserver"}).json()["revision"]
    from nero_ppt.workflow.runner import Worker

    w = Worker(ctx)
    w.run_once()  # parse
    ctx.conn.execute("UPDATE steps SET status='cancelled' WHERE job_id=? AND kind='outline'", (job_id,))
    specs_rev = ctx.records.next_revision(job_id)
    ctx.records.save_revision(job_id, "specs", {"revision": specs_rev, "specs": SPECS},
                              input_revision=rev)
    # sample 渲染
    ctx.jobs.update_state(job_id, ctx.jobs.get(job_id)["job_revision"],
                          phase="sample", status="running")
    ctx.steps.enqueue(job_id, "render_batch", input_revision=ctx.jobs.get(job_id)["job_revision"],
                      scope_key="sample", payload={"scope_key": "sample"})
    w.run_once()
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["status"] == "awaiting_review", view["error"]
    # 批准样张 → 直接整稿（规格一次全量生成，样张即全稿预览）
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "sample", "decision": "approve",
                          "expected_revision": view["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["phase"] == "final" and view["status"] == "running"
    # 组装
    ctx.steps.enqueue(job_id, "assemble", input_revision=view["revision"])
    w.run_once()
    view = client.get(f"/api/jobs/{job_id}").json()
    assert view["phase"] == "final" and view["status"] == "awaiting_review", view["error"]
    return job_id


def test_full_assembly_and_publish(client):
    """T14：PPTX/PDF/讲稿同版本同序；发布事务原子完成。"""
    client_app, ctx = client
    client = client_app
    job_id = _push_to_final(client, ctx)
    view = client.get(f"/api/jobs/{job_id}").json()
    # 三份核心产物已登记
    kinds = {a["kind"] for a in view["artifacts"]}
    assert {"pptx", "pdf", "notes", "thumbnail"} <= kinds
    # 下载三份
    by_kind = {a["kind"]: a["id"] for a in view["artifacts"]}
    pptx = client.get(f"/api/jobs/{job_id}/artifacts/{by_kind['pptx']}")
    pdf = client.get(f"/api/jobs/{job_id}/artifacts/{by_kind['pdf']}")
    notes = client.get(f"/api/jobs/{job_id}/artifacts/{by_kind['notes']}")
    assert pptx.status_code == 200 and pdf.status_code == 200 and notes.status_code == 200
    # Notes 讲稿：页序与内容
    md = notes.text
    assert md.count("## 第 ") == 4
    assert "封面讲稿" in md and "总结讲稿" in md
    # PPTX Notes 可读回
    from pptx import Presentation
    from io import BytesIO

    prs = Presentation(BytesIO(pptx.content))
    assert "封面讲稿" in prs.slides[0].notes_slide.notes_text_frame.text
    # PDF 页数 4
    from nero_ppt.rendering.libreoffice import pdf_page_count
    assert pdf_page_count(BytesIO(pdf.content)) == 4
    # 最终批准 → 发布
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "final", "decision": "approve",
                          "expected_revision": view["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200 and r.json().get("published") is True
    job = ctx.jobs.get(job_id)
    assert job["status"] == "completed"
    # releases 表有 manifest
    row = ctx.conn.execute("SELECT manifest_json FROM releases WHERE job_id=?", (job_id,)).fetchone()
    manifest = json.loads(row["manifest_json"])
    assert len(manifest["slides"]) == 4
    kinds = {f["kind"] for f in manifest["files"]}
    assert {"pptx", "pdf", "notes"} <= kinds
    # 重复批准 → 幂等/409（状态已 completed）
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "final", "decision": "approve",
                          "expected_revision": manifest["job_revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code in (409, 200)


def test_publish_failure_no_half_release(client):
    """产物缺失 → 发布失败 → 状态回 awaiting_review，不产生 completed/半发布（T15）。"""
    client_app, ctx = client
    client = client_app
    job_id = _push_to_final(client, ctx)
    view = client.get(f"/api/jobs/{job_id}").json()
    # 破坏一个产物文件
    pdf_entry = next(a for a in view["artifacts"] if a["kind"] == "pdf")
    ctx.files.resolve(pdf_entry["relative_path"]).unlink()
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "final", "decision": "approve",
                          "expected_revision": view["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "publish_failed"
    job = ctx.jobs.get(job_id)
    assert job["status"] == "awaiting_review"  # 未 completed
    assert ctx.conn.execute("SELECT 1 FROM releases WHERE job_id=?", (job_id,)).fetchone() is None


def test_tampered_artifact_rejected(client):
    """产物被篡改（哈希不匹配）→ 拒绝发布。"""
    client_app, ctx = client
    client = client_app
    job_id = _push_to_final(client, ctx)
    view = client.get(f"/api/jobs/{job_id}").json()
    pptx_entry = next(a for a in view["artifacts"] if a["kind"] == "pptx")
    ctx.files.resolve(pptx_entry["relative_path"]).write_bytes(b"tampered")
    r = client.post(f"/api/jobs/{job_id}/reviews",
                    json={"target_kind": "final", "decision": "approve",
                          "expected_revision": view["revision"]},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 409
    assert "哈希" in r.json()["detail"]["error"]["message"]
    assert ctx.jobs.get(job_id)["status"] == "awaiting_review"
