"""模板引擎：检查器与档案。"""
from .inspect import TemplateInspection, inspect_template
from .profile import build_profile

__all__ = ["TemplateInspection", "build_profile", "inspect_template"]
