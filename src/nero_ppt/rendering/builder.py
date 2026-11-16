"""确定性 PPTX Builder：RenderPlan + SlideSpec → 完整 PPTX（含 Notes）。

两阶段构建：
1. 包构建：按计划顺序克隆模板源页（共享图片/版式，克隆图表及其数据），重建页面序列。
2. 内容编辑：重新打开后按 object_id 定位形状，执行受控编辑并写入讲稿。

不使用整页截图或自由重画；输出始终基于原模板包。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from pptx import Presentation

from ..contracts import (
    ChartBlock,
    ImageRef,
    RenderPlan,
    SlideSpec,
    TableBlock,
)
from ..templates_engine import ooxml
from .notes import notes_from_spec, write_notes


@dataclass
class BuildResult:
    output_path: str
    slide_count: int
    warnings: list[str] = field(default_factory=list)


class BuildError(RuntimeError):
    pass


def _shape_by_id(slide, object_id: int):
    for shape in slide.shapes:
        if shape.shape_id == object_id:
            return shape
    return None


def build_deck(
    template_path: str | Path,
    plans: list[RenderPlan],
    specs: list[SlideSpec],
    out_path: str | Path,
    resource_map: dict[str, str] | None = None,
    tmp_dir: str | Path | None = None,
) -> BuildResult:
    """从模板包与渲染计划构建 PPTX。resource_map: resource_id → 图片文件路径。"""
    if not plans:
        raise BuildError("渲染计划为空")
    if len(plans) != len(specs):
        raise BuildError("plans 与 specs 数量不一致")
    resource_map = resource_map or {}
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tmp_dir) if tmp_dir else out_path.parent
    tmp_dir.mkdir(parents=True, exist_ok=True)
    stage1 = tmp_dir / (out_path.stem + ".stage1.pptx")

    warnings: list[str] = []

    # ---- 阶段 1：包构建（克隆页面） ----
    prs = Presentation(str(template_path))
    slide_by_id = {s.slide_id: s for s in prs.slides}
    new_slides = []
    for i, plan in enumerate(plans):
        src = slide_by_id.get(plan.source_slide_id)
        if src is None:
            raise BuildError(f"计划 {i} 的源页面 {plan.source_slide_id} 不在模板中")
        new_slides.append(ooxml.clone_slide(prs, src))
    ooxml.keep_only_slides(prs, new_slides)
    prs.save(str(stage1))

    # ---- 阶段 2：内容编辑 ----
    prs2 = Presentation(str(stage1))
    for idx, (plan, spec) in enumerate(zip(plans, specs)):
        slide = prs2.slides[idx]
        _apply_plan(slide, plan, spec, resource_map, warnings)
        write_notes(slide, plan)
    prs2.save(str(out_path))
    stage1.unlink(missing_ok=True)
    return BuildResult(output_path=str(out_path), slide_count=len(plans), warnings=warnings)


def _apply_plan(slide, plan: RenderPlan, spec: SlideSpec, resource_map: dict[str, str],
                warnings: list[str]) -> None:
    for binding in plan.slot_bindings:
        shape = _shape_by_id(slide, binding.object_id)
        if shape is None:
            raise BuildError(f"页面 {plan.slide_id} 找不到对象 {binding.object_id}（槽位 {binding.slot_id}）")
        action = binding.edit_action
        try:
            if action == "set_text":
                ooxml.set_text_keep_format(shape.text_frame, binding.constant_text)
            elif action == "set_bullets":
                ooxml.set_bullets_keep_format(
                    shape.text_frame, binding.text_lines or ([binding.constant_text] if binding.constant_text else []),
                    binding.line_levels or None)
            elif action == "set_table":
                block = spec.body_blocks[binding.block_index]
                if not isinstance(block, TableBlock):
                    raise BuildError(f"block_index {binding.block_index} 不是表格块")
                info = ooxml.set_table_data(shape, block.headers, block.rows)
                if info.get("adapted"):
                    warnings.append(f"页面 {plan.slide_id}：数据列数与模板表格不同，已合并/补空适配（数据未删减）")
                if block.note:
                    warnings.append(f"页面 {plan.slide_id} 表格注释写入讲稿")
            elif action == "set_chart_data":
                block = spec.body_blocks[binding.block_index]
                if not isinstance(block, ChartBlock):
                    raise BuildError(f"block_index {binding.block_index} 不是图表块")
                ooxml.set_chart_data(
                    shape.chart, block.categories,
                    [(s.name, s.values) for s in block.series])
            elif action == "replace_image":
                block_or_res = binding.resource_id
                path = resource_map.get(block_or_res)
                if not path or not Path(path).exists():
                    raise BuildError(f"资源 {block_or_res} 无对应文件")
                ooxml.replace_image(slide, shape, path)
            elif action == "none":
                pass
            else:
                raise BuildError(f"未知编辑动作 {action}")
        except BuildError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"编辑槽位 {binding.slot_id} 失败：{exc}") from exc


def build_from_specs(
    template_path: str | Path,
    profile,  # TemplateProfile
    specs: list[SlideSpec],
    out_path: str | Path,
    resource_map: dict[str, str] | None = None,
    tmp_dir: str | Path | None = None,
) -> tuple[BuildResult, list[RenderPlan]]:
    """完整链路：SlideSpec → RenderPlan（绑定）→ PPTX。返回构建结果与计划。"""
    from ..templates_engine.binding import plan_slide

    plans: list[RenderPlan] = []
    effective: list[SlideSpec] = []
    all_warnings: list[str] = []
    for i, spec in enumerate(specs):
        plan, warns, eff_spec = plan_slide(spec, profile, i)
        # 用 spec 的讲稿覆盖 plan.notes_text，保证一致性
        plan.notes_text = notes_from_spec(spec)
        plans.append(plan)
        effective.append(eff_spec)
        all_warnings.extend(f"[{spec.slide_id}] {w}" for w in warns)
    result = build_deck(template_path, plans, effective, out_path, resource_map, tmp_dir)
    result.warnings = all_warnings + result.warnings
    return result, plans
