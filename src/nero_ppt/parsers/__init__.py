"""解析器：统一接口与 PDF/DOCX/TXT/MD/PPTX 适配器。"""
from .base import ParserError, build_parser, extract_facts, parse_document
from .docx import DocxParser
from .pdf import PdfParser
from .pptx import PptxParser
from .text import TextParser

__all__ = ["DocxParser", "ParserError", "PdfParser", "PptxParser", "TextParser", "build_parser", "extract_facts", "parse_document"]
