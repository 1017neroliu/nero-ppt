"""逐页讲稿：Notes 写入与 Markdown 导出。"""
from __future__ import annotations

from ..contracts import SlideSpec


def format_notes(script: str, transition: str = "",
                 source_explanations: list[str] | None = None,
                 target_seconds: int | None = None) -> str:
    parts = [script]
    if transition:
        parts.append(f"【转场】{transition}")
    for e in source_explanations or []:
        parts.append(f"【数据解释】{e}")
    if target_seconds:
        parts.append(f"【建议时长】{target_seconds} 秒")
    return "\n".join(parts)


def write_notes(slide, plan) -> None:
    """把 RenderPlan.notes_text 写入页面备注（python-pptx 自动创建 notes part）。"""
    slide.notes_slide.notes_text_frame.text = plan.notes_text


def export_notes_markdown(specs: list[SlideSpec], title: str = "演示讲稿") -> str:
    """导出 Markdown 讲稿：与页面顺序一致。"""
    out = [f"# {title}", ""]
    for i, s in enumerate(specs, start=1):
        out.append(f"## 第 {i} 页：{s.headline}")
        out.append("")
        n = s.speaker_notes
        out.append(n.script)
        out.append("")
        if n.transition:
            out.append(f"- 转场：{n.transition}")
        for e in n.source_explanations:
            out.append(f"- 数据解释：{e}")
        out.append(f"- 建议时长：{s.target_seconds} 秒")
        out.append("")
    return "\n".join(out)


def notes_from_spec(spec: SlideSpec) -> str:
    return format_notes(
        spec.speaker_notes.script,
        spec.speaker_notes.transition,
        spec.speaker_notes.source_explanations,
        spec.target_seconds,
    )
