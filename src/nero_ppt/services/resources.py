"""资源控制：磁盘阈值、临时文件清理（dry-run 支持）。"""
from __future__ import annotations

import shutil
import time
from pathlib import Path


class ResourceLimitError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "disk"):
        super().__init__(message)
        self.kind = kind


def check_disk(ctx, *, for_render: bool = False) -> None:
    """磁盘低于阈值时暂停新上传/新渲染。"""
    free_mb = ctx.files.free_disk_mb()
    threshold = ctx.settings.min_free_disk_mb
    if free_mb < threshold:
        action = "渲染" if for_render else "上传"
        raise ResourceLimitError(
            f"磁盘剩余 {free_mb}MB 低于阈值 {threshold}MB，已暂停新{action}；"
            f"已有任务保持状态，可清理临时文件后重试")


def cleanup_temp(ctx, *, dry_run: bool = False, max_age_hours: float = 24.0) -> dict:
    """清理无活跃引用的临时文件：
    - data/tmp/ 下超过 max_age 的转换工作目录
    - 所有 .part 半成品文件
    - 孤立 staging 文件（*.stage1.pptx）

    用户原文件与最终产物不默认删除。
    """
    data_dir: Path = ctx.settings.data_dir
    now = time.time()
    report = {"freed_bytes": 0, "files": [], "dry_run": dry_run}

    tmp_root = data_dir / "tmp"
    if tmp_root.exists():
        for entry in tmp_root.iterdir():
            try:
                old = now - entry.stat().st_mtime > max_age_hours * 3600
            except OSError:
                continue
            if not old:
                continue
            size = _dir_size(entry) if entry.is_dir() else entry.stat().st_size
            report["files"].append(str(entry.relative_to(data_dir)))
            report["freed_bytes"] += size
            if not dry_run:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)

    for pattern in ("**/*.part", "**/*.stage1.pptx"):
        for p in data_dir.glob(pattern):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            report["files"].append(str(p.relative_to(data_dir)))
            report["freed_bytes"] += size
            if not dry_run:
                p.unlink(missing_ok=True)
    return report


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total
