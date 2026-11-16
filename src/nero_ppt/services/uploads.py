"""受限上传校验：流式大小限制、文件签名、ZIP 解压安全。

- 允许类型按 job 资料与模板分开
- 签名校验（magic bytes）防伪装扩展名
- ZIP：条目数、解压总大小、单文件大小、路径逃逸检查；宏不执行
"""
from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath

MATERIAL_EXTS = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 ".txt": "text/plain", ".md": "text/markdown", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
TEMPLATE_EXTS = {".pptx": MATERIAL_EXTS[".pptx"],
                 ".potx": "application/vnd.openxmlformats-officedocument.presentationml.template"}

ZIP_MAGIC = b"PK\x03\x04"
PDF_MAGIC = b"%PDF-"

MAX_ZIP_ENTRIES = 2000
MAX_ZIP_UNCOMPRESSED = 500 * 1024 * 1024  # 500MB
MAX_ZIP_MEMBER = 200 * 1024 * 1024


class UploadError(ValueError):
    pass


def sniff_signature(data: bytes, ext: str, filename: str) -> None:
    """签名与扩展名一致性；文本类做 UTF-8 检查。"""
    head = data[:16]
    if ext == ".pdf":
        if not head.startswith(PDF_MAGIC):
            raise UploadError("文件签名与 .pdf 不符（疑似伪装文件）")
    elif ext in (".docx", ".pptx", ".potx"):
        if not head.startswith(ZIP_MAGIC):
            raise UploadError(f"文件签名与 {ext} 不符（OOXML 应为 ZIP 容器）")
    elif ext in (".txt", ".md"):
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise UploadError("文本文件不是有效 UTF-8 编码") from exc
    else:
        raise UploadError(f"不支持的类型 {ext}")


def check_zip_safety(data: bytes) -> None:
    """ZIP 资源限制与路径检查（OOXML/压缩包通用）。"""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise UploadError(f"ZIP 条目数 {len(infos)} 超限 {MAX_ZIP_ENTRIES}")
            total = 0
            for info in infos:
                name = PurePosixPath(info.filename)
                if name.is_absolute() or ".." in name.parts:
                    raise UploadError(f"ZIP 内路径逃逸：{info.filename}")
                if info.file_size > MAX_ZIP_MEMBER:
                    raise UploadError(f"ZIP 单文件 {info.filename} 过大")
                total += info.file_size
                if total > MAX_ZIP_UNCOMPRESSED:
                    raise UploadError("ZIP 解压总大小超限（防解压炸弹）")
    except zipfile.BadZipFile as exc:
        raise UploadError("损坏的 ZIP 容器") from exc


def validate_upload(filename: str, data: bytes, *,
                    allowed_exts: dict[str, str], max_bytes: int) -> str:
    """校验完整内容（调用方流式读入并强制 max_bytes）；返回规范化扩展名。"""
    ext = Path(filename).suffix.lower()
    if ext not in allowed_exts:
        raise UploadError(f"不支持的文件类型 {ext or '(无扩展名)'}；支持 {'/'.join(sorted(allowed_exts))}")
    if len(data) == 0:
        raise UploadError("空文件")
    if len(data) > max_bytes:
        raise UploadError(f"文件 {len(data)} 字节超过单文件上限 {max_bytes}")
    sniff_signature(data, ext, filename)
    if ext in (".docx", ".pptx", ".potx"):
        check_zip_safety(data)
    return ext
