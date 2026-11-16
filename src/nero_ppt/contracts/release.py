"""ReleaseManifest：发布清单。最终文件只从该清单解析。"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictInt, StrictStr

from .common import NeroModel


class ManifestEntry(NeroModel):
    """一个产物条目：相对 data 目录的路径 + 哈希 + 类型。"""

    artifact_id: StrictStr
    kind: Literal["pptx", "pdf", "notes", "preview", "thumbnail", "generic"] = "generic"
    relative_path: StrictStr
    file_hash: StrictStr
    byte_size: StrictInt = 0


class SlideReleaseRef(NeroModel):
    """按顺序的页面修订引用。"""

    page_index: StrictInt  # 0-based 输出顺序
    slide_id: StrictStr
    slide_revision: StrictInt


class ReleaseManifest(NeroModel):
    release_id: StrictStr
    job_id: StrictStr
    job_revision: StrictInt
    template_version: StrictInt
    slides: list[SlideReleaseRef] = Field(min_length=1)
    files: list[ManifestEntry] = Field(min_length=3)  # pptx / pdf / notes 至少三份
