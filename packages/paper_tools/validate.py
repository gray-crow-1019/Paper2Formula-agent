"""Bounded syntax and dimensional checks. No eval, sympify, or execution."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re

from .errors import PaperToolError
from .recognize import GREEK
from .resolve import extract_symbol_names, normalize_unit

ZERO = (0, 0, 0, 0, 0, 0, 0)
# SI base dimensions: mass, length, time, current, temperature, amount, luminosity.
UNITS = {
    "1": ZERO, "%": ZERO, "rad": ZERO,
    "kg": (1, 0, 0, 0, 0, 0, 0), "g": (1, 0, 0, 0, 0, 0, 0),
    "m": (0, 1, 0, 0, 0, 0, 0), "cm": (0, 1, 0, 0, 0, 0, 0), "mm": (0, 1, 0, 0, 0, 0, 0),
    "s": (0, 0, 1, 0, 0, 0, 0), "ms": (0, 0, 1, 0, 0, 0, 0),
    "A": (0, 0, 0, 1, 0, 0, 0), "K": (0, 0, 0, 0, 1, 0, 0),
    "mol": (0, 0, 0, 0, 0, 1, 0), "cd": (0, 0, 0, 0, 0, 0, 1),
    "N": (1, 1, -2, 0, 0, 0, 0), "J": (1, 2, -2, 0, 0, 0, 0),
    "W": (1, 2, -3, 0, 0, 0, 0), "Pa": (1, -1, -2, 0, 0, 0, 0),
    "Hz": (0, 0, -1, 0, 0, 0, 0), "C": (0, 0, 1, 1, 0, 0, 0),
    "V": (1, 2, -3, -1, 0, 0, 0), "ohm": (1, 2, -3, -2, 0, 0, 0),
}
ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Constant,
                 ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.UAdd, ast.USub, ast.Load)


def finding(status: str, *reasons: str) -> dict:
    return {"status": status, "reasons": list(reasons)}


def parse_expression(text: str):
    if len(text) > 2000:
        raise ValueError("表达式超过语法校验长度限制。")
    tree = ast.parse(text, mode="eval")
    nodes = list(ast.walk(tree))
    if len(nodes) > 300 or any(not isinstance(node, ALLOWED_NODES) for node in nodes):
        raise ValueError("表达式包含当前校验器不支持的结构。")
    for node in nodes:
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float):
                raise ValueError("表达式包含非法常量。")
            if type(node.value) is int and node.value.bit_length() > 512:
                raise ValueError("数值常量超过保守校验范围。")
            if type(node.value) is float and not math.isfinite(node.value):
                raise ValueError("表达式包含非法常量。")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or "__" in node.id):
            raise ValueError("表达式包含非法标识符。")
    return tree.body


def simple_expression(latex: str) -> str | None:
    if not isinstance(latex, str):
        return None
    text = latex
    for character, command in GREEK.items():
        text = re.sub(r"\\" + command + r"\b", character, text)
    text = text.replace(r"\cdot", "*").replace(r"\times", "*").replace("·", "*").replace("×", "*")
    text = text.replace("−", "-").replace("^", "**")
    text = re.sub(r"_\{([A-Za-z0-9]+)\}", r"_\1", text)
    text = re.sub(r"\{([+-]?\d+)\}", r"\1", text)
    if re.search(r"[\\{}\[\]≈≤≥∫∑]", text):
        return None
    return text.strip()


def validate_syntax(latex: str | None) -> dict:
    if not isinstance(latex, str) or not latex.strip():
        return finding("fail", "公式文本为空。")
    if len(latex) > 10000:
        return finding("fail", "公式超过长度限制。")
    stack = []
    pairs = {"}": "{", ")": "(", "]": "["}
    for character in latex:
        if character in "{([":
            stack.append(character)
            if len(stack) > 50:
                return finding("unknown", "公式嵌套超过校验器限制。")
        elif character in pairs:
            if not stack or stack.pop() != pairs[character]:
                return finding("fail", "公式括号不匹配。")
    if stack:
        return finding("fail", "公式括号未闭合。")
    expression = simple_expression(latex)
    if expression is None:
        return finding("unknown", "该数学结构超出保守语法校验范围；括号检查不等同完整 LaTeX 校验。")
    try:
        parts = expression.split("=")
        if len(parts) > 2:
            return finding("unknown", "暂不验证连续等式。")
        for part in parts:
            parse_expression(part.strip())
    except (SyntaxError, ValueError, RecursionError, OverflowError):
        return finding("unknown", "无法按有限算术语法解析；可能含隐式乘法或复杂排版，需要复核。")
    return finding("pass", "有限算术语法检查通过，不代表识别或数学推导正确。")


class DimensionUnknown(Exception):
    pass


class DimensionConflict(Exception):
    pass


def dimensions(node, bindings: dict) -> tuple:
    if isinstance(node, ast.Name):
        if node.id not in bindings:
            raise DimensionUnknown(f"符号 {node.id} 的单位未知。")
        return bindings[node.id]
    if isinstance(node, ast.Constant):
        return ZERO
    if isinstance(node, ast.UnaryOp):
        return dimensions(node.operand, bindings)
    if isinstance(node, ast.BinOp):
        left = dimensions(node.left, bindings)
        if isinstance(node.op, ast.Pow):
            if isinstance(node.right, ast.Constant) and type(node.right.value) is int and abs(node.right.value) <= 12:
                return tuple(value * node.right.value for value in left)
            if isinstance(node.right, ast.UnaryOp) and isinstance(node.right.op, ast.USub) and isinstance(node.right.operand, ast.Constant) and type(node.right.operand.value) is int and abs(node.right.operand.value) <= 12:
                return tuple(-value * node.right.operand.value for value in left)
            raise DimensionUnknown("仅支持有界整数幂的量纲校验。")
        right = dimensions(node.right, bindings)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if left != right:
                raise DimensionConflict("加减项的量纲不一致。")
            return left
        if isinstance(node.op, ast.Mult):
            return tuple(a + b for a, b in zip(left, right))
        if isinstance(node.op, ast.Div):
            return tuple(a - b for a, b in zip(left, right))
    raise DimensionUnknown("数学结构不在量纲校验范围内。")


def unit_dimensions(unit: str) -> tuple:
    unit = normalize_unit(unit)
    if unit in UNITS:
        return UNITS[unit]
    if unit is None or len(unit) > 100:
        raise DimensionUnknown("单位未知或超出长度限制。")
    try:
        return dimensions(parse_expression(unit.replace("^", "**")), UNITS)
    except (ValueError, SyntaxError, RecursionError) as exc:
        raise DimensionUnknown("单位表达式不在支持范围内。") from exc


def validate_units(equation: dict) -> dict:
    expression = simple_expression(equation.get("latex", ""))
    if expression is None or expression.count("=") != 1:
        return finding("unknown", "仅对简单等式和明确 SI 单位进行量纲校验。")
    bindings = {}
    try:
        for symbol in equation.get("symbols", []):
            if symbol.get("status") == "excluded":
                continue
            candidates = set()
            for value in symbol.get("values", []):
                unit = value.get("normalized_unit") or value.get("raw_unit")
                if unit:
                    candidates.add(unit_dimensions(unit))
            for candidate in symbol.get("unit_candidates", []):
                if candidate.get("raw_unit"):
                    candidates.add(unit_dimensions(candidate["raw_unit"]))
            if not candidates:
                raise DimensionUnknown(f"符号 {symbol['name']} 的单位未明示；未将其视为无量纲。")
            if len(candidates) != 1:
                raise DimensionConflict(f"符号 {symbol['name']} 存在不同量纲的单位候选。")
            bindings[symbol["name"]] = next(iter(candidates))
        left, right = [parse_expression(part.strip()) for part in expression.split("=")]
        if dimensions(left, bindings) != dimensions(right, bindings):
            return finding("fail", "等式左右两侧量纲不一致。")
        return finding("pass", "在原文明确单位和有限算术结构范围内，等式量纲一致。")
    except DimensionConflict as exc:
        return finding("fail", str(exc))
    except (DimensionUnknown, SyntaxError, ValueError, RecursionError) as exc:
        return finding("unknown", str(exc) or "无法完成量纲校验。")


def validate_evidence(result: dict):
    anchors = {source["id"]: source for source in result.get("anchors", [])}
    if len(anchors) != len(result.get("anchors", [])):
        raise PaperToolError("SOURCE_MISMATCH", "来源锚点编号重复。")
    for source in anchors.values():
        bbox = source.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4 or any(type(value) not in (int, float) or not math.isfinite(value) for value in bbox):
            raise PaperToolError("SOURCE_MISMATCH", "来源锚点坐标无效。")
        width, height = source.get("page_width", 0), source.get("page_height", 0)
        if not (0 <= bbox[0] < bbox[2] <= width + 1 and 0 <= bbox[1] < bbox[3] <= height + 1):
            raise PaperToolError("SOURCE_MISMATCH", "来源锚点超出页面区域。")
        if source.get("rendition_id") != result.get("rendition_id"):
            raise PaperToolError("SOURCE_MISMATCH", "来源锚点不属于当前规范 PDF 版本。")
    def check_references(references):
        if not isinstance(references, list) or any(item not in anchors for item in references):
            raise PaperToolError("SOURCE_MISMATCH", "结果引用了不存在的来源锚点。")
    for equation in result.get("equations", []):
        check_references(equation.get("anchor_ids", []))
        if not equation.get("anchor_ids"):
            raise PaperToolError("SOURCE_MISMATCH", "公式缺少原文位置。")
        for symbol in equation.get("symbols", []):
            check_references(symbol.get("evidence_anchor_ids", []))
            if symbol.get("meaning") and symbol.get("provenance") == "source_explicit" and not symbol.get("evidence_anchor_ids"):
                raise PaperToolError("SOURCE_MISMATCH", "原文明示的符号定义缺少证据。")
            for value in symbol.get("values", []):
                check_references(value.get("evidence_anchor_ids", []))
                if (value.get("raw_value") is not None or value.get("raw_unit") is not None) and value.get("provenance") == "source_explicit" and not value.get("evidence_anchor_ids"):
                    raise PaperToolError("SOURCE_MISMATCH", "原文明示的参数缺少证据。")
    for relation in result.get("relations", []):
        check_references(relation.get("evidence_anchor_ids", []))
        if not relation.get("evidence_anchor_ids"):
            raise PaperToolError("SOURCE_MISMATCH", "公式关系缺少原文证据。")
    for review in result.get("review_items", []):
        check_references(review.get("anchor_ids", []))


def validate_result(result: dict) -> dict:
    validate_evidence(result)
    previous = result.get("review_items", [])
    identities = {}
    # Historical resolved records follow current findings in the exported list.
    # Prefer the newest display ID, rather than whichever history item is last.
    for item in previous:
        identity = (item.get("equation_id"), item.get("symbol_id"), item["kind"])
        current = identities.get(identity)
        number = int(item["id"].split("-")[-1]) if re.fullmatch(r"CHECK-\d+", item.get("id", "")) else 0
        current_number = int(current["id"].split("-")[-1]) if current and re.fullmatch(r"CHECK-\d+", current.get("id", "")) else -1
        if number > current_number:
            identities[identity] = item
    sequence = max([int(item["id"].split("-")[-1]) for item in previous if re.fullmatch(r"CHECK-\d+", item.get("id", ""))] or [0])
    reviews = []

    def review(equation_id, symbol_id, kind, reasons, anchor_ids, basis=None):
        nonlocal sequence
        identity = (equation_id, symbol_id, kind)
        prior = identities.get(identity)
        signature = hashlib.sha256(json.dumps({"reasons": reasons, "anchors": anchor_ids,
                    "basis": basis}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        changed = prior and prior.get("finding_signature") and prior["finding_signature"] != signature
        if prior and not (prior.get("status") == "resolved" and changed):
            item = {**prior, "reasons": reasons, "anchor_ids": anchor_ids}
            # A user's explicit keep_unknown/confirm decision remains visible;
            # validation status itself stays unknown/fail where appropriate.
        else:
            sequence += 1
            item = {"id": f"CHECK-{sequence:02d}", "equation_id": equation_id,
                    "symbol_id": symbol_id, "kind": kind, "reasons": reasons,
                    "status": "pending", "anchor_ids": anchor_ids, "decision": None}
        item["finding_signature"] = signature
        reviews.append(item)

    next_symbol = max([int(symbol["id"].split("-")[-1]) for equation in result.get("equations", [])
                       for symbol in equation.get("symbols", []) if re.fullmatch(r"SYM-\d+", symbol.get("id", ""))] or [0])
    next_parameter = max([int(value["id"].split("-")[-1]) for equation in result.get("equations", [])
                          for symbol in equation.get("symbols", []) for value in symbol.get("values", [])
                          if re.fullmatch(r"PAR-\d+", value.get("id", ""))] or [0])
    for equation in result.get("equations", []):
        if equation.get("excluded"):
            equation["status"] = "excluded"
        if equation.get("edited_latex") and equation.get("status") != "excluded":
            names = extract_symbol_names(equation.get("latex") or "")
            present = {symbol["name"]: symbol for symbol in equation.get("symbols", [])}
            for symbol in equation.get("symbols", []):
                if symbol["name"] not in names:
                    if symbol.get("status") != "excluded":
                        symbol["previous_status"] = symbol.get("status", "unknown")
                    symbol["status"] = "excluded"
                elif symbol.get("status") == "excluded":
                    symbol["status"] = symbol.pop("previous_status", "unknown")
            for name in names:
                if name in present:
                    continue
                next_symbol += 1
                next_parameter += 1
                equation.setdefault("symbols", []).append({
                    "id": f"SYM-{next_symbol:02d}", "name": name, "meaning": None,
                    "status": "unknown", "provenance": "unknown", "evidence_anchor_ids": [],
                    "reasons": ["公式修改引入了新符号，需要补充定义和单位。"],
                    "values": [{"id": f"PAR-{next_parameter:02d}", "raw_value": None,
                                "normalized_value": None, "raw_unit": None, "normalized_unit": None,
                                "evidence_anchor_ids": [], "provenance": "unknown", "status": "unknown",
                                "reasons": ["修改后的新符号尚无数值或单位证据。"]}]})
    active = [equation for equation in result.get("equations", []) if equation.get("status") != "excluded"]
    if not active:
        review(None, None, "no_equations", ["未发现可用的带编号文本公式；首版检测范围有限，请核对原文。"], [])
    for equation in active:
        equation_id = equation["id"]
        recognition = equation.get("recognition", {})
        if recognition.get("status") != "pass" and not recognition.get("reviewed") and not equation.get("recognition_confirmed"):
            review(equation_id, None, "recognition", recognition.get("reasons") or ["公式识别仍需核对。"], equation["anchor_ids"], equation.get("latex"))
        syntax = validate_syntax(equation.get("latex"))
        if syntax["status"] != "pass":
            review(equation_id, None, "syntax", syntax["reasons"], equation["anchor_ids"], equation.get("latex"))
        missing = []
        symbol_unit_issues = False
        for symbol in equation.get("symbols", []):
            if symbol.get("status") == "excluded":
                continue
            if not symbol.get("meaning") or symbol.get("status") in ("unknown", "conflict", "uncertain"):
                reasons = symbol.get("reasons") or [f"符号 {symbol['name']} 的定义待核对。"]
                missing.append(symbol["name"])
                review(equation_id, symbol["id"], "definitions", reasons,
                       symbol.get("evidence_anchor_ids") or equation["anchor_ids"],
                       {"name": symbol["name"], "meaning": symbol.get("meaning"), "status": symbol.get("status")})
            unit_sources = [value.get("normalized_unit") or value.get("raw_unit") for value in symbol.get("values", [])]
            unit_sources.extend(candidate.get("raw_unit") for candidate in symbol.get("unit_candidates", []))
            units_for_symbol = [unit for unit in unit_sources if unit]
            unit_reasons = []
            if not units_for_symbol:
                unit_reasons = [f"符号 {symbol['name']} 未明确给出单位；缺失单位不视为无量纲。"]
            else:
                try:
                    choices = {unit_dimensions(unit) for unit in units_for_symbol}
                    if len(choices) != 1:
                        unit_reasons = [f"符号 {symbol['name']} 的单位候选量纲冲突。"]
                except (DimensionUnknown, DimensionConflict, ValueError, OverflowError) as exc:
                    unit_reasons = [f"符号 {symbol['name']} 的单位无法确定：{exc}"]
            if unit_reasons:
                symbol_unit_issues = True
                review(equation_id, symbol["id"], "units", unit_reasons,
                       list(dict.fromkeys(identifier for value in symbol.get("values", [])
                                          for identifier in value.get("evidence_anchor_ids", []))) or equation["anchor_ids"],
                       {"name": symbol["name"], "units": unit_sources})
        definitions = finding("unknown", "以下符号缺少无冲突的明确定义：" + ", ".join(missing)) if missing else finding("pass", "当前识别符号具有明确或已标注的用户补充定义。")
        units = validate_units(equation)
        if units["status"] != "pass" and not symbol_unit_issues:
            review(equation_id, None, "units", units["reasons"], equation["anchor_ids"],
                   {"latex": equation.get("latex"), "symbols": [{"name": symbol["name"], "units": [
                    value.get("normalized_unit") or value.get("raw_unit") for value in symbol.get("values", [])]}
                    for symbol in equation.get("symbols", [])]})
        equation["validation"] = {"syntax": syntax, "definitions": definitions, "units": units}
        equation["status"] = "needs_review" if any(item["equation_id"] == equation_id and item["status"] == "pending" for item in reviews) else "reviewed"
    # Keep historical resolved decisions even if their finding no longer applies.
    emitted = {item["id"] for item in reviews}
    reviews.extend(item for item in previous if item.get("status") == "resolved" and item["id"] not in emitted)
    result["review_items"] = reviews
    result["status"] = "waiting_review" if any(item["status"] == "pending" for item in reviews) else "validated"
    return result
