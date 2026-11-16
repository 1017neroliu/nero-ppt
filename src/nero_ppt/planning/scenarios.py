"""场景配置加载与 general 通用场景。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCENARIOS_DIR = Path(__file__).resolve().parents[3] / "config" / "scenarios"

GENERAL_SCENE = {
    "id": "general",
    "version": 1,
    "name": "通用/自定义",
    "required_inputs": ["audience", "objective", "duration_minutes"],
    "sections": ["背景与目标", "主要内容", "关键数据", "结论与下一步"],
    "quality_rules": [
        "每个结论有资料依据或标注待补充",
        "数字保留原始口径与单位",
        "行动项明确负责人或标记待补充"
    ],
    "notes_requirements": ["逐页口述正文", "关键数据解释", "转场", "预计讲述时长"],
    "missing_evidence_policy": "flag_and_request_input",
}


def load_scenarios(base_dir: Path | None = None) -> list[dict[str, Any]]:
    """加载全部场景配置；JSON 校验失败的场景跳过并记录（不静默吞掉格式错误）。"""
    base = base_dir or SCENARIOS_DIR
    scenes: list[dict[str, Any]] = []
    if base.exists():
        for f in sorted(base.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if "id" not in data or "version" not in data:
                    raise ValueError(f"场景缺少 id/version: {f.name}")
                scenes.append(data)
            except (json.JSONDecodeError, ValueError):
                continue
    ids = {s["id"] for s in scenes}
    if "general" not in ids:
        scenes.append(GENERAL_SCENE)
    return scenes


def get_scene(scene_id: str, scene_version: int | None = None,
              base_dir: Path | None = None) -> dict[str, Any]:
    for s in load_scenarios(base_dir):
        if s["id"] == scene_id and (scene_version is None or s.get("version") == scene_version):
            return s
    raise KeyError(f"场景不存在：{scene_id} v{scene_version}")
