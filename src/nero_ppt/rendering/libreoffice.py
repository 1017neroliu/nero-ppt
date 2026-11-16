"""LibreOffice 子进程封装：PPTX → PDF。

- 参数数组调用，独立临时目录与独立用户配置（避免并发 profile 冲突）
- 超时终止整个进程组，不留下假成功 PDF
- 临时目录内转换，成功后由调用方原子移动
"""
from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class ConversionError(RuntimeError):
    pass


@dataclass
class ConversionResult:
    pdf_path: Path
    pages: int
    file_hash: str
    byte_size: int
    duration_seconds: float


def _soffice_binary() -> str:
    path = shutil.which("soffice")
    if not path:
        raise ConversionError("未找到 soffice 可执行文件")
    return path


def run_soffice(args: list[str], timeout: float, env_profile_dir: Path) -> subprocess.CompletedProcess:
    """运行 soffice，独立 profile，超时杀进程组。"""
    cmd = [_soffice_binary(), "--headless", "--norestore", "--nolockcheck",
           f"-env:UserInstallation=file://{env_profile_dir.resolve()}"] + args
    env = os.environ.copy()
    env.setdefault("HOME", str(env_profile_dir.parent))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, env=env,
    )
    import time

    start = time.monotonic()
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # 终止整个进程组
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=10)
        raise ConversionError(f"LibreOffice 转换超时（{timeout}s），已终止进程组") from exc
    duration = time.monotonic() - start
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err), duration


def convert_to_pdf(pptx_path: str | Path, work_dir: str | Path,
                   timeout: float = 180.0, out_path: str | Path | None = None) -> ConversionResult:
    """在 work_dir 内独立转换；成功后原子移动到 out_path（若提供）。"""
    src = Path(pptx_path)
    if not src.exists() or src.stat().st_size == 0:
        raise ConversionError(f"输入不存在或为空：{src}")
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    profile_dir = work / ".lo-profile"
    out_dir = work / "pdf-out"
    out_dir.mkdir(exist_ok=True)

    # 输入复制到工作目录，避免 soffice 写源文件所在目录
    local_src = work / src.name
    shutil.copyfile(src, local_src)

    import time

    t0 = time.monotonic()
    proc, _ = run_soffice(
        ["--convert-to", "pdf", "--outdir", str(out_dir), str(local_src)],
        timeout=timeout, env_profile_dir=profile_dir,
    )
    pdf = out_dir / (local_src.stem + ".pdf")
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace")[:500]
        raise ConversionError(f"LibreOffice 退出码 {proc.returncode}：{err}")
    if not pdf.exists() or pdf.stat().st_size == 0:
        raise ConversionError("LibreOffice 未产出有效 PDF")
    pages = pdf_page_count(pdf)
    if pages <= 0:
        raise ConversionError("PDF 页数校验失败")
    data = pdf.read_bytes()
    pages_count, file_hash, byte_size = pages, hashlib.sha256(data).hexdigest(), len(data)
    duration = time.monotonic() - t0
    if out_path is not None:
        final = Path(out_path)
        final.parent.mkdir(parents=True, exist_ok=True)
        # 原子移动：同文件系统 rename；跨盘时先拷贝临时文件再 rename
        tmp_final = final.with_suffix(final.suffix + ".tmp")
        shutil.copyfile(pdf, tmp_final)
        os.replace(tmp_final, final)
        result_path = final
        shutil.rmtree(out_dir, ignore_errors=True)
    else:
        result_path = pdf
    local_src.unlink(missing_ok=True)
    return ConversionResult(
        pdf_path=result_path,
        pages=pages_count,
        file_hash=file_hash,
        byte_size=byte_size,
        duration_seconds=duration,
    )


def pdf_page_count(pdf_path: str | Path | "io.BytesIO") -> int:
    """pdfinfo 页数；内存流或 poppler 不可用时退回 pypdf。"""
    if isinstance(pdf_path, (str, Path)) and Path(str(pdf_path)).exists() and shutil.which("pdfinfo"):
        proc = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True, timeout=30)
        for line in proc.stdout.splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":", 1)[1].strip())
        raise ConversionError("pdfinfo 输出无页数")
    from pypdf import PdfReader
    import io

    stream = pdf_path if hasattr(pdf_path, "read") else io.BytesIO(Path(str(pdf_path)).read_bytes())
    return len(PdfReader(stream).pages)


def extract_pdf_text(pdf_path: str | Path, max_pages: int = 5) -> str:
    """pdftotext 提取（渲染验证用）。"""
    if not shutil.which("pdftotext"):
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages])
    proc = subprocess.run(
        ["pdftotext", "-l", str(max_pages), str(pdf_path), "-"],
        capture_output=True, text=True, timeout=60,
    )
    return proc.stdout
