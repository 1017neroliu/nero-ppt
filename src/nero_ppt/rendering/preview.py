"""预览：Poppler 缩略图与页面图片。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .libreoffice import ConversionError


def render_thumbnails(pdf_path: str | Path, out_dir: str | Path,
                      dpi: int = 80, prefix: str = "page") -> list[Path]:
    """pdftoppm 输出每页 PNG；返回按页序的文件列表。"""
    if not shutil.which("pdftoppm"):
        raise ConversionError("未找到 pdftoppm，无法生成缩略图")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(out / prefix)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise ConversionError(f"pdftoppm 失败：{proc.stderr[:300]}")
    files = sorted(out.glob(f"{prefix}-*.png"))
    if not files:
        raise ConversionError("pdftoppm 未产出缩略图")
    return files
