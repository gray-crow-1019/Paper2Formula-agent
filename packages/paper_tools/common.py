from __future__ import annotations

import hashlib
from pathlib import Path

import fitz

from .errors import PaperToolError

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def open_pdf(path: Path):
    try:
        doc = fitz.open(path)
        if not doc.is_pdf:
            doc.close()
            raise PaperToolError("INVALID_FILE", "文件不是有效的 PDF。")
        if doc.needs_pass or doc.xref_get_key(-1, "Encrypt")[0] != "null":
            doc.close()
            raise PaperToolError("ENCRYPTED_DOCUMENT", "首版不支持加密 PDF，请先移除密码。")
        if doc.is_repaired:
            doc.close()
            raise PaperToolError("INVALID_FILE", "PDF 结构损坏，需要修复后重新上传。")
        return doc
    except PaperToolError:
        raise
    except Exception as exc:
        raise PaperToolError("INVALID_FILE", "无法读取 PDF，文件可能损坏。") from exc


def page_metadata(page) -> dict:
    # PyMuPDF text/annotation coordinates are crop-relative and unrotated.
    crop = page.cropbox
    return {
        "page": page.number + 1,
        "page_width": float(crop.width),
        "page_height": float(crop.height),
        "rotation": int(page.rotation),
        "crop": list(crop),
        "media_box": list(page.mediabox),
        "coordinate_system": "unrotated_crop_top_left_points",
    }


def anchor(metadata: dict, anchor_id: str, bbox, quote: str, rendition_id: str,
           document_version: int = 1) -> dict:
    return {
        **metadata,
        "id": anchor_id,
        "bbox": [round(float(value), 3) for value in bbox],
        "quote": quote,
        "quote_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        "rendition_id": rendition_id,
        "document_version": document_version,
    }
