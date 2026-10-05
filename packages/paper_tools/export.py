"""Generate three local artifacts from one frozen result; caller publishes them."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import fitz

from .common import open_pdf, sha256
from .errors import PaperToolError
from .validate import validate_evidence


def display(value) -> str:
    return "未知" if value is None else str(value)


def escape_cell(value) -> str:
    return display(value).replace("|", r"\|").replace("\n", " ")


def source_links(anchor_ids: list[str], anchor_map: dict) -> str:
    links = []
    for identifier in anchor_ids:
        source = anchor_map[identifier]
        coordinates = ", ".join(str(value) for value in source["bbox"])
        links.append(f"[{identifier} · 第 {source['page']} 页](annotated.pdf#page={source['page']})（bbox: {coordinates}）")
    return "；".join(links) or "无原文证据"


def markdown_report(result: dict) -> str:
    anchor_map = {source["id"]: source for source in result["anchors"]}
    lines = ["# Paper2Formula 分析报告", "", f"- 文档：`{result['document_id']}`",
             f"- 规范 PDF：`{result['rendition_id']}`", f"- 结果版本：{result['result_version']}",
             f"- 状态：{result['status']}", "", "本报告仅记录可追溯证据。未知或保留未知的项目不代表校验通过。", ""]
    if not result.get("equations"):
        lines.extend(["未检测到带编号的文本公式。首版不支持 OCR 或复杂排版识别。", ""])
    for equation in result.get("equations", []):
        lines.extend([f"## {equation['id']} · 原公式编号 {display(equation.get('original_number'))}", "",
                      f"状态：{equation['status']}", "", "```latex", equation.get("latex") or "", "```", "",
                      source_links(equation["anchor_ids"], anchor_map), "",
                      f"解释：{display(equation.get('explanation'))}（来源：{equation.get('explanation_provenance', 'unknown')}）", ""])
        if equation.get("status") == "excluded":
            lines.extend(["该候选已由复核决定排除，保留原始记录用于审计。", ""])
            continue
        recognition = equation.get("recognition", {})
        if recognition.get("reasons"):
            lines.extend(["识别说明：" + "；".join(recognition["reasons"]), ""])
        lines.extend(["| 编号 | 符号 | 定义 | 来源 | 状态 |", "|---|---|---|---|---|"])
        for symbol in equation.get("symbols", []):
            if symbol.get("status") == "excluded":
                continue
            lines.append("| " + " | ".join(escape_cell(value) for value in
                          (symbol["id"], symbol["name"], symbol.get("meaning"), symbol.get("provenance"), symbol["status"])) + " |")
        lines.append("")
        for symbol in equation.get("symbols", []):
            if symbol.get("status") == "excluded":
                continue
            lines.extend([f"{symbol['id']} 定义证据：{source_links(symbol.get('evidence_anchor_ids', []), anchor_map)}", ""])
            for value in symbol.get("values", []):
                lines.extend([f"- {value['id']} 原始数值：{display(value.get('raw_value'))}；规范数值：{display(value.get('normalized_value'))}；"
                              f"原始单位：{display(value.get('raw_unit'))}；规范单位：{display(value.get('normalized_unit'))}；来源：{value.get('provenance', 'unknown')}",
                              f"  证据：{source_links(value.get('evidence_anchor_ids', []), anchor_map)}"])
            lines.append("")
        for check, value in equation.get("validation", {}).items():
            lines.append(f"- {check}：{value['status']} — {'；'.join(value.get('reasons', []))}")
        lines.append("")
    lines.extend(["## 复核记录", ""])
    for review in result.get("review_items", []):
        decision = review.get("decision")
        action = decision.get("action") if isinstance(decision, dict) else decision
        lines.extend([f"- {review['id']} / {review.get('equation_id') or '文档'} / {review['kind']} / {review['status']}"
                      f" / 决定：{display(action)}：{'；'.join(review.get('reasons', []))}",
                      f"  {source_links(review.get('anchor_ids', []), anchor_map)}"])
    if not result.get("review_items"):
        lines.append("无复核项。")
    lines.extend(["", "## 公式关系", ""])
    for relation in result.get("relations", []):
        lines.extend([f"- {relation['id']}：{relation['from_equation_id']} → {relation['to_equation_id']} / {relation['relation_type']}",
                      f"  {source_links(relation['evidence_anchor_ids'], anchor_map)}"])
    if not result.get("relations"):
        lines.append("未找到足以建立公式关系的明确原文引用。")
    return "\n".join(lines) + "\n"


def create_annotated_pdf(result: dict, pdf_path: Path, destination: Path) -> int:
    anchors = {source["id"]: source for source in result["anchors"]}
    count = 0
    with open_pdf(pdf_path) as document:
        def annotate(identifier, references, content, kind):
            nonlocal count
            for reference in references:
                source = anchors[reference]
                if source["page"] < 1 or source["page"] > document.page_count:
                    raise PaperToolError("SOURCE_MISMATCH", "批注来源页码超出 PDF。")
                page = document[source["page"] - 1]
                rectangle = fitz.Rect(source["bbox"])
                if kind == "equation":
                    annotation = page.add_rect_annot(rectangle)
                    annotation.set_colors(stroke=(.12, .37, .92))
                    annotation.set_border(width=1.1)
                elif kind == "symbol":
                    annotation = page.add_underline_annot(rectangle)
                    annotation.set_colors(stroke=(1, .48, .08))
                elif kind == "parameter":
                    annotation = page.add_highlight_annot(rectangle)
                    annotation.set_colors(stroke=(.15, .8, .32))
                    annotation.set_opacity(.22)
                else:
                    annotation = page.add_rect_annot(rectangle)
                    annotation.set_colors(stroke=(.84, .22, .2))
                    annotation.set_border(width=1, dashes=[3, 2])
                annotation.set_info(title=identifier, subject="Paper2Formula", content=f"{identifier}\n{content}"[:12000])
                annotation.update()
                count += 1
        for equation in result.get("equations", []):
            if equation.get("status") == "excluded":
                continue
            annotate(equation["id"], equation["anchor_ids"],
                     f"原编号：{display(equation.get('original_number'))}\nLaTeX 候选：{equation.get('latex', '')}\n状态：{equation['status']}", "equation")
            for symbol in equation.get("symbols", []):
                if symbol.get("status") == "excluded":
                    continue
                annotate(symbol["id"], symbol.get("evidence_anchor_ids", []),
                         f"{symbol['name']}：{display(symbol.get('meaning'))}\n来源：{symbol.get('provenance', 'unknown')}", "symbol")
                for value in symbol.get("values", []):
                    annotate(value["id"], value.get("evidence_anchor_ids", []),
                             f"{symbol['name']} = {display(value.get('raw_value'))} {display(value.get('raw_unit'))}\n来源：{value.get('provenance', 'unknown')}", "parameter")
        for review in result.get("review_items", []):
            annotate(review["id"], review.get("anchor_ids", []),
                     f"{review['kind']} / {review['status']}\n" + "；".join(review.get("reasons", [])), "review")
        document.save(destination, garbage=3, deflate=True)
    with open_pdf(destination) as verified:
        if not verified.page_count:
            raise PaperToolError("EXPORT_FAILED", "批注 PDF 导出为空。", True)
    return count


def export_result(result: dict, pdf_path: Path, output_dir: Path) -> dict:
    validate_evidence(result)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {"markdown": output_dir / "report.md", "json": output_dir / "result.json",
             "annotated_pdf": output_dir / "annotated.pdf"}
    if any(path.resolve() == pdf_path.resolve() for path in paths.values()):
        raise PaperToolError("SOURCE_MISMATCH", "导出路径不能覆盖规范 PDF。")
    try:
        paths["markdown"].write_text(markdown_report(result), encoding="utf-8")
        count = create_annotated_pdf(result, pdf_path, paths["annotated_pdf"])
        exported = deepcopy(result)
        for equation in exported.get("equations", []):
            if equation.get("evidence_image"):
                equation["evidence_image"] = "evidence/" + Path(equation["evidence_image"]).name
        # Artifacts are finalized/published by the workflow. A JSON artifact cannot
        # contain its own hash; the authoritative complete manifest is returned
        # by the API after all files have succeeded.
        exported["manifest"] = {kind: {"filename": path.name} for kind, path in paths.items()}
        paths["json"].write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8")
        json.loads(paths["json"].read_text(encoding="utf-8"))
        return {"artifacts": {kind: {"path": str(path), "filename": path.name,
                                    "sha256": sha256(path), "size": path.stat().st_size,
                                    "mime": {"markdown": "text/markdown", "json": "application/json",
                                             "annotated_pdf": "application/pdf"}[kind]}
                              for kind, path in paths.items()}, "annotation_count": count}
    except PaperToolError:
        raise
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        raise PaperToolError("EXPORT_FAILED", "成果导出失败，尚未发布下载文件。", True) from exc
