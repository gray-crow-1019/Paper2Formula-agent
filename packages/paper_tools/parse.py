"""Bounded geometric grouping of numbered text-equation fragments, without OCR."""
from __future__ import annotations

from pathlib import Path
import re

import fitz

from .common import anchor, open_pdf, page_metadata
from .errors import PaperToolError

NUMBER = re.compile(r"#?\s*(?:\(|（)\s*(\d+(?:[.\-]\d+)*[a-z]?)\s*(?:\)|）)\s*$", re.I)
ONLY_NUMBER = re.compile(r"^\s*#?\s*(?:\(|（)\s*\d+(?:[.\-]\d+)*[a-z]?\s*(?:\)|）)\s*$", re.I)
PROSE_WORDS = re.compile(
    r"\b(?:equations?|figures?|tables?|where|which|represents?|denotes?|according|"
    r"using|shown|shows?|presents?|defined|definitions?|respectively|therefore|"
    r"consequently|because|this|these|those|the|are|is|and|from|with|between)\b", re.I)


def compact_math_fragment(text: str) -> bool:
    """Permit isolated denominators/operators, but exclude adjacent body prose."""
    text = text.strip()
    if not text or len(text) > 300 or PROSE_WORDS.search(text):
        return False
    if len(re.findall(r"[\u4e00-\u9fff]", text)) > 2:
        return False
    if re.search(r"[A-Za-z][.!?。；;:]\s*$", text):
        return False
    return len(re.findall(r"[A-Za-z]{3,}", text)) <= 5


def is_math(text: str) -> bool:
    if not re.search(r"[A-Za-z\u0370-\u03ff]", text):
        return False
    if not re.search(r"[=≈≃≡∝≤≥∑∫+*/^−×÷]", text):
        return False
    if re.match(r"^(?:eq(?:uation)?|fig(?:ure)?|table|see|using|from|where|let)\b", text, re.I):
        return False
    return len(text) < 1000 and compact_math_fragment(text)


def midpoint(line: dict) -> float:
    return (line["bbox"][1] + line["bbox"][3]) / 2


def fragments_touch(left: dict, right: dict) -> bool:
    a, b = left["bbox"], right["bbox"]
    horizontal_gap = max(0, a[0] - b[2], b[0] - a[2])
    vertical_gap = max(0, a[1] - b[3], b[1] - a[3])
    size = min(left["font_size"], right["font_size"])
    return horizontal_gap <= min(48, max(24, size * 3)) and vertical_gap <= min(14, max(8, size * 1.2))


def group_numbered_fragments(lines: list[dict]) -> list[dict]:
    """Form local connected components around a real terminal equation number.

    A component must contain letters and a mathematical operator. Thus an
    isolated '(1)' or a numbered paragraph never becomes an equation. Each source
    line stays separate: the union rectangle is only candidate/crop metadata.
    """
    groups = []
    occupied = set()
    markers = {}
    source_groups = {}
    for index, line in enumerate(lines):
        matched = NUMBER.search(line["text"])
        prefix = line["text"][:matched.start()].strip() if matched else line["text"]
        if matched and (not prefix or compact_math_fragment(prefix)):
            markers[index] = line
        if compact_math_fragment(prefix) or index in markers:
            source_groups.setdefault(line.get("text_block", index), []).append(index)
    # Keep compact original PDF text blocks together: a numerator and denominator
    # in the same source block must not be assigned to different equation rows.
    # A block containing two equation labels has no single row and is partitioned
    # per line instead. Body text never participates in these ownership groups.
    ownership = {}
    for indexes in source_groups.values():
        rectangle = fitz.Rect(lines[indexes[0]]["bbox"])
        for index in indexes[1:]:
            rectangle |= fitz.Rect(lines[index]["bbox"])
        contained_markers = [index for index in indexes if index in markers]
        relation_rows = [midpoint(lines[index]) for index in indexes
                         if re.search(r"[=≈≃≡∝≤≥]", lines[index]["text"])]
        multiple_rows = len(relation_rows) > 1 and max(relation_rows) - min(relation_rows) > max(
            8, min(lines[index]["font_size"] for index in indexes) * .7)
        if len(contained_markers) > 1 or multiple_rows or rectangle.height > 60:
            continue
        center = (rectangle.y0 + rectangle.y1) / 2
        for index in indexes:
            ownership[index] = (center, contained_markers[0] if contained_markers else None)

    def belongs_to_marker(index, marker_index):
        if index == marker_index:
            return True
        line = lines[index]
        center, forced_owner = ownership.get(index, (midpoint(line), None))
        marker = markers[marker_index]
        if forced_owner is not None:
            return forced_owner == marker_index
        # Compare labels in the same horizontal region only. Labels in another
        # column do not compete for the formula's fragments.
        compatible = [other_index for other_index, other in markers.items()
                      if abs(other["bbox"][2] - marker["bbox"][2]) <=
                      max(32, min(72, max(marker["font_size"], other["font_size"]) * 4))]
        owner = min(compatible, key=lambda other_index: (abs(center - midpoint(markers[other_index])), other_index))
        return owner == marker_index

    for marker_index, marker in enumerate(lines):
        number = NUMBER.search(marker["text"])
        if number is None or marker_index not in markers or marker_index in occupied:
            continue
        prefix = marker["text"][:number.start()].strip()
        if prefix and not compact_math_fragment(prefix):
            continue
        center = midpoint(marker)
        radius = min(28, max(20, marker["font_size"] * 1.6))
        local = []
        for index, line in enumerate(lines):
            if index in occupied or abs(midpoint(line) - center) > radius:
                continue
            if not belongs_to_marker(index, marker_index):
                continue
            if index != marker_index and NUMBER.search(line["text"]):
                continue
            text = prefix if index == marker_index else line["text"]
            if not compact_math_fragment(text):
                continue
            if line["bbox"][0] > marker["bbox"][2] + 2:
                continue
            local.append(index)
        # Bound the graph; dense table/page regions remain unrecognized.
        if not local or len(local) > 64:
            continue
        components = []
        remaining = set(local)
        while remaining:
            component = {remaining.pop()}
            frontier = list(component)
            while frontier:
                current = frontier.pop()
                adjacent = {index for index in remaining if fragments_touch(lines[current], lines[index])}
                remaining -= adjacent
                component |= adjacent
                frontier.extend(adjacent)
            components.append(component)
        choices = []
        for component in components:
            if prefix and marker_index not in component:
                continue
            ordered = sorted(component, key=lambda index: (lines[index]["bbox"][0], lines[index]["bbox"][1]))
            text = " ".join(prefix if index == marker_index else lines[index]["text"] for index in ordered)
            if not is_math(text):
                continue
            rectangle = fitz.Rect(lines[ordered[0]]["bbox"])
            for index in ordered[1:]:
                rectangle |= fitz.Rect(lines[index]["bbox"])
            if rectangle.x0 >= marker["bbox"][2] or rectangle.height > 90:
                continue
            distance = abs((rectangle.y0 + rectangle.y1) / 2 - center)
            gap = max(0, marker["bbox"][0] - rectangle.x1)
            choices.append((distance, gap, ordered, text, rectangle))
        if not choices:
            continue
        _, _, ordered, text, rectangle = min(choices, key=lambda choice: (choice[0], choice[1]))
        indexes = sorted(set(ordered) | {marker_index})
        rectangle |= fitz.Rect(marker["bbox"])
        occupied.update(indexes)
        groups.append({"indexes": indexes, "text": text, "original_number": number.group(1),
                       "bbox": list(rectangle), "layout_grouped": len(indexes) > 1,
                       "grouping_reasons": ["按公式编号附近的紧凑数学文本和页面几何位置组合；保留每个原始文本行作为独立证据。"]})
    return sorted(groups, key=lambda group: (group["bbox"][1], group["bbox"][0]))


def extract_document_blocks(pdf_path: Path, rendition_id: str, document_version: int = 1) -> dict:
    blocks, anchors, candidates, pages, sections = [], [], [], [], []
    section_id = "SEC-01"
    sections.append({"id": section_id, "title": "Document", "page": 1})
    with open_pdf(pdf_path) as document:
        for page in document:
            metadata = page_metadata(page)
            pages.append(metadata)
            lines = []
            visible = fitz.Rect(0, 0, metadata["page_width"], metadata["page_height"])
            for text_block in page.get_text("dict", sort=True)["blocks"]:
                if text_block.get("type") != 0:
                    continue
                for line in text_block.get("lines", []):
                    text = "".join(span["text"] for span in line["spans"]).strip()
                    rectangle = fitz.Rect(line["bbox"]) & visible
                    if text and not rectangle.is_empty:
                        spans = [{key: span[key] for key in ("text", "size", "font", "bbox", "origin", "flags")}
                                 for span in line["spans"]]
                        lines.append({"text": text, "bbox": list(rectangle), "spans": spans,
                                      "text_block": text_block.get("number", len(lines)),
                                      "font_size": max(span["size"] for span in line["spans"])})
            page_blocks = []
            for index, line in enumerate(lines):
                if len(anchors) >= 50000:
                    raise PaperToolError("PARSE_LIMIT", "文档文本块超过首版解析限制。")
                text = line["text"]
                if re.match(r"^\d+(?:\.\d+)*[.\s]+[A-Z\u4e00-\u9fff]", text) and len(text) < 100 and not is_math(text):
                    section_id = f"SEC-{len(sections) + 1:02d}"
                    sections.append({"id": section_id, "title": text, "page": page.number + 1})
                anchor_id = f"ANC-{page.number + 1:04d}-{index + 1:04d}"
                source = anchor(metadata, anchor_id, line["bbox"], text, rendition_id, document_version)
                anchors.append(source)
                block = {"id": f"BLK-{len(blocks) + 1:05d}", "page": page.number + 1,
                         "text": text, "kind": "text", "section_id": section_id,
                         "reading_order": len(blocks), "anchor_ids": [anchor_id]}
                blocks.append(block)
                page_blocks.append(block)
            for group in group_numbered_fragments(lines):
                if len(candidates) >= 1000:
                    raise PaperToolError("PARSE_LIMIT", "公式候选超过首版数量限制。")
                indexes = group.pop("indexes")
                participating = [page_blocks[index] for index in indexes]
                for block in participating:
                    block["kind"] = "equation"
                candidates.append({"id": f"CAND-{len(candidates) + 1:03d}", **group,
                                   "page": page.number + 1,
                                   "anchor_ids": [block["anchor_ids"][0] for block in participating],
                                   "section_id": participating[0]["section_id"],
                                   "reading_order": min(block["reading_order"] for block in participating),
                                   "layout": [{"text": lines[index]["text"], "bbox": lines[index]["bbox"],
                                               "anchor_id": page_blocks[index]["anchor_ids"][0],
                                               "spans": lines[index]["spans"]} for index in indexes]})
    return {"parser": f"pymupdf-geometric-text-v2-{fitz.VersionBind}", "rendition_id": rendition_id,
            "document_version": document_version, "blocks": blocks, "anchors": anchors,
            "candidates": candidates, "pages": pages, "sections": sections,
            "limitations": ["仅检测带编号的文本公式；几何组合是候选，不代表分式或上下标转写正确；不进行 OCR 或跨页组合。"]}
