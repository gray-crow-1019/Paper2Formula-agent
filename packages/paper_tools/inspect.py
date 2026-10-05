"""Validate real file contents before invoking parsers or a converter."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

from .common import DOCX_MIME, PDF_MIME, open_pdf, page_metadata, sha256
from .errors import PaperToolError


def validate_docx(path: Path, settings) -> dict:
    max_expanded = min(250 * 1024 * 1024, int(settings.max_upload_mb * 1024 * 1024 * 10))
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = {item.filename for item in members}
            if len(members) > 10000 or len(names) != len(members):
                raise PaperToolError("INVALID_FILE", "DOCX 包含过多或重复的压缩条目。")
            if not {"[Content_Types].xml", "word/document.xml", "_rels/.rels"}.issubset(names):
                raise PaperToolError("INVALID_FILE", "ZIP 文件不包含完整的 DOCX OOXML 结构。")
            if sum(item.file_size for item in members) > max_expanded:
                raise PaperToolError("INVALID_FILE", "DOCX 解压后超出安全大小限制。")
            for item in members:
                parts = PurePosixPath(item.filename).parts
                if item.filename.startswith(("/", "\\")) or ".." in parts or "\\" in item.filename:
                    raise PaperToolError("INVALID_FILE", "DOCX 压缩包包含不安全路径。")
                if item.flag_bits & 1:
                    raise PaperToolError("ENCRYPTED_DOCUMENT", "首版不支持加密 DOCX。")
                if item.file_size > max_expanded or item.file_size > max(1024 * 1024, item.compress_size * 200):
                    raise PaperToolError("INVALID_FILE", "DOCX 压缩比异常，已拒绝解析。")
                if re.search(r"(?:vbaProject|activeX|embeddings)", item.filename, re.I):
                    raise PaperToolError("INVALID_FILE", "首版拒绝带宏、ActiveX 或嵌入对象的 Word 文件。")
                if item.filename.endswith((".xml", ".rels")):
                    raw = archive.read(item)
                    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                        raise PaperToolError("INVALID_FILE", "DOCX XML 包含不安全的实体声明。")
                    root = ET.fromstring(raw)
                    if item.filename.endswith(".rels"):
                        for relationship in root:
                            if relationship.attrib.get("TargetMode", "").lower() == "external":
                                raise PaperToolError("INVALID_FILE", "首版拒绝含外部关系的 DOCX，请移除外链后上传。")
                            target = relationship.attrib.get("Target", "")
                            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                                raise PaperToolError("INVALID_FILE", "DOCX 关系引用了外部资源。")
                            base = item.filename.rsplit("/_rels/", 1)[0] if "/_rels/" in item.filename else ""
                            resolved = posixpath.normpath(posixpath.join(base, target))
                            if target.startswith(("/", "\\")) or "\\" in target or resolved == ".." or resolved.startswith("../"):
                                raise PaperToolError("INVALID_FILE", "DOCX 关系引用超出文档包的路径。")
                    if item.filename == "[Content_Types].xml":
                        content_types = [el.attrib.get("ContentType", "") for el in root]
                        if any("macroEnabled" in value or "vba" in value.lower() for value in content_types):
                            raise PaperToolError("INVALID_FILE", "首版不支持包含宏的 Word 文件。")
                        if not any(value == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
                                   for value in content_types):
                            raise PaperToolError("INVALID_FILE", "OOXML 文件不是标准 DOCX 文档。")
                    if item.filename == "word/document.xml" and not root.tag.endswith("}document"):
                        raise PaperToolError("INVALID_FILE", "DOCX 主文档结构无效。")
    except PaperToolError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, RuntimeError, OSError, ValueError) as exc:
        raise PaperToolError("INVALID_FILE", "DOCX 文件损坏或 OOXML 结构无效。") from exc
    return {"mime": DOCX_MIME, "page_count": None, "pages": [], "scan": None,
            "encrypted": False, "sha256": sha256(path), "sections": None}


def inspect_document(path: Path, mime: str, settings) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise PaperToolError("INVALID_FILE", "上传文件为空或不存在。")
    if path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
        raise PaperToolError("FILE_TOO_LARGE", "上传文件超过配置的大小限制。")
    with path.open("rb") as stream:
        signature = stream.read(1024)
    if signature.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise PaperToolError("INVALID_FILE", "不支持旧版 DOC 或加密的 OOXML 文件。")
    if mime == DOCX_MIME:
        if not signature.startswith(b"PK"):
            raise PaperToolError("INVALID_FILE", "文件内容与 DOCX 类型不一致。")
        return validate_docx(path, settings)
    if mime != PDF_MIME or b"%PDF-" not in signature:
        raise PaperToolError("INVALID_FILE", "首版仅支持真实的 PDF 或 DOCX 文件。")
    with open_pdf(path) as doc:
        if not doc.page_count:
            raise PaperToolError("INVALID_FILE", "PDF 没有可读取的页面。")
        if doc.page_count > settings.max_pages:
            raise PaperToolError("PAGE_LIMIT", f"文档超过 {settings.max_pages} 页限制。")
        pages = []
        total_chars = 0
        scanned_pages = []
        for page in doc:
            chars = len(re.sub(r"\s+", "", page.get_text("text")))
            total_chars += chars
            # A figure page with a text caption is permitted. Pages containing only
            # raster images require OCR and are rejected rather than silently lost.
            if chars < settings.min_text_chars and page.get_images():
                scanned_pages.append(page.number + 1)
            pages.append({**page_metadata(page), "text_chars": chars})
        if scanned_pages or total_chars < settings.min_text_chars:
            raise PaperToolError("UNSUPPORTED_SCAN", "首版不支持扫描件或无文本 PDF；请上传含可选中文本的文档。")
        return {"mime": PDF_MIME, "page_count": doc.page_count, "pages": pages,
                "scan": False, "encrypted": False, "sha256": sha256(path), "sections": None}
