"""按任务解析模型 provider：任务绑定模型 → 默认档回退。"""
from __future__ import annotations

from ..providers.base import OpenAIProvider
from ..services.context import WebContext
from ..services.models import load_catalog


def resolve_job_provider(ctx: WebContext, model_profile_id: str | None) -> OpenAIProvider:
    """任务绑定模型不可用时回退默认档；两者都不可用报"模型未配置"（显式，不伪造）。"""
    catalog = load_catalog(ctx.settings.data_dir, ctx.settings)
    entry = catalog.get(model_profile_id)
    if entry is None or not entry.available:
        entry = catalog.get(None)  # 回退默认档
    if entry is None or not entry.available:
        if catalog.source == "none":
            raise RuntimeError(
                "模型未配置：需在 data/config/models.json 配置模型目录，或设置 NERO_MODEL_API_KEY/NERO_MODEL_NAME 环境变量")
        reason = entry.unavailable_reason if entry else "目录为空"
        raise RuntimeError(f"模型未配置：{reason}")
    return catalog.build_provider(entry.id)


def check_job_model_available(ctx: "WebContext", model_profile_id: str | None) -> None:
    """启动任务前的结构校验：目录有模型条目时，所选/默认模型必须可用（密钥已配置）。

    目录为空（未配置任何模型）时放行——确定性渲染链路仍可体验，
    模型步骤会在执行时显式失败。
    """
    catalog = load_catalog(ctx.settings.data_dir, ctx.settings)
    if not catalog.entries:
        return
    entry = catalog.get(model_profile_id)
    if entry is None or not entry.available:
        entry = catalog.get(None)
    if entry is None or not entry.available:
        reason = entry.unavailable_reason if entry else "目录中无可用模型"
        raise RuntimeError(f"模型不可用：{reason}；请先在页面对所选模型执行可用性测试")
