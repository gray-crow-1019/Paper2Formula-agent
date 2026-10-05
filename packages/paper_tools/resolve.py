"""Bind nearby explicit statements; absent facts remain null."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from .recognize import GREEK

NUMBER_VALUE = r"[+−-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?(?:\s*(?:±|\+/-|–|~|to|至)\s*[+−-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?)?"
UNIT_PATTERN = r"(?:[A-Za-zµμΩ°%]+(?:\s*[*/·]\s*[A-Za-zµμΩ°%]+)*(?:\s*(?:\^\s*[+−-]?\d+|[²³]))?|1)"
FUNCTIONS = {"sin", "cos", "tan", "log", "ln", "exp", "sqrt", "max", "min", "abs"}


def extract_symbol_names(text: str) -> list[str]:
    text = re.sub(r"\\(?:text|mathrm|operatorname)\{[^}]*\}", "", text)
    text = re.sub(r"\\([A-Za-z]+)", lambda match: next((key for key, value in GREEK.items()
                             if value == match.group(1)), " "), text)
    names = []
    for token in re.findall(r"[A-Za-z]+(?:_\{[A-Za-z0-9]+\}|_[A-Za-z0-9]+)?|[\u0370-\u03ff](?:_[A-Za-z0-9]+)?", text):
        if token in FUNCTIONS or token.lower() in {"where", "and", "for", "with", "eq", "the"}:
            continue
        if "_" in token or re.match(r"[\u0370-\u03ff]", token):
            components = [token.replace("_{", "_").replace("}", "")]
        elif len(token) <= 2:
            components = list(token)
        else:
            # Longer identifier words may be functions/prose; they are not
            # automatically treated as three or more independent variables.
            continue
        for component in components:
            if component not in names:
                names.append(component)
    return names[:50]


def normalize_value(raw: str) -> str | None:
    try:
        if re.fullmatch(r"[+−-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?", raw):
            value = Decimal(raw.replace("−", "-"))
            if value.is_finite() and -1000 <= value.adjusted() <= 1000:
                return str(value)
    except InvalidOperation:
        pass
    return None


def normalize_unit(raw: str | None) -> str | None:
    if raw is None:
        return None
    normalized = re.sub(r"\s+", "", raw).replace("·", "*").replace("²", "^2").replace("³", "^3").replace("−", "-")
    aliases = {"newton": "N", "newtons": "N", "kilogram": "kg", "kilograms": "kg",
               "meter": "m", "meters": "m", "metre": "m", "metres": "m", "second": "s",
               "seconds": "s", "dimensionless": "1", "unitless": "1", "无量纲": "1"}
    return aliases.get(normalized.lower(), normalized)


def symbol_pattern(name: str) -> str:
    # Match the variable as a symbol, not a substring of an ordinary word.
    return r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])"


def find_symbol_mentions(equation: dict, symbol: str, parsed: dict) -> list[dict]:
    candidate_anchors = {identifier for candidate in parsed["candidates"] for identifier in candidate["anchor_ids"]}
    order = equation.get("reading_order", 0)
    mentions = []
    for block in parsed["blocks"]:
        if block["section_id"] != equation.get("section_id"):
            continue
        if set(block["anchor_ids"]) & candidate_anchors:
            continue
        distance = abs(block["reading_order"] - order)
        if distance > 25 or not re.search(symbol_pattern(symbol), block["text"]):
            continue
        mentions.append({**block, "distance": distance,
                         "ranking_reason": "同一章节且位于公式前后 25 个文本行内。"})
    return sorted(mentions, key=lambda mention: (mention["distance"], mention["reading_order"]))[:12]


def resolve_symbols(equations: list[dict], parsed: dict) -> list[dict]:
    symbol_number = 0
    parameter_number = 0
    for equation in equations:
        if equation.get("status") == "excluded":
            continue
        bindings = []
        for name in extract_symbol_names(equation["latex"]):
            symbol_number += 1
            mentions = find_symbol_mentions(equation, name, parsed)
            meanings = []
            values = []
            name_pattern = symbol_pattern(name)
            definition_pattern = re.compile(name_pattern + r"\s*(?:is|denotes|represents|stands\s+for|为|表示|代表|是)\s+([^,;，；。]+)", re.I)
            value_pattern = re.compile(name_pattern + r"\s*(?:=|equals|取值为|为)\s*(" + NUMBER_VALUE + r")\s*(" + UNIT_PATTERN + r")?", re.I)
            definition_units = []
            for mention in mentions:
                text = mention["text"]
                definition = definition_pattern.search(text)
                if definition:
                    meaning = definition.group(1).strip().rstrip(".")
                    # Avoid labeling an explicit number as a semantic definition.
                    if meaning and not re.match(r"^" + NUMBER_VALUE, meaning):
                        meanings.append({"meaning": meaning, "anchor_ids": mention["anchor_ids"]})
                        unit_match = re.search(r"(?:\(|（)\s*(" + UNIT_PATTERN + r"|dimensionless|无量纲)\s*(?:\)|）)", meaning, re.I)
                        if unit_match:
                            definition_units.append({"raw_unit": unit_match.group(1),
                                                     "evidence_anchor_ids": mention["anchor_ids"]})
                for matched in value_pattern.finditer(text):
                    raw_value = matched.group(1).strip()
                    raw_unit = matched.group(2)
                    # Words after a number are not implicitly physical units.
                    if raw_unit and raw_unit.lower() in {"and", "where", "for", "the", "is", "in"}:
                        raw_unit = None
                    parameter_number += 1
                    values.append({"id": f"PAR-{parameter_number:02d}", "raw_value": raw_value,
                                   "normalized_value": normalize_value(raw_value),
                                   "raw_unit": raw_unit, "normalized_unit": normalize_unit(raw_unit),
                                   "evidence_anchor_ids": mention["anchor_ids"], "provenance": "source_explicit",
                                   "status": "known" if raw_unit else "unknown",
                                   "reasons": [] if raw_unit else ["原文未在该数值后明确给出单位。"],
                                   "context": text})
            unique_meanings = list(dict.fromkeys(item["meaning"] for item in meanings))
            evidence = list(dict.fromkeys(identifier for item in meanings for identifier in item["anchor_ids"]))
            status = "known" if len(unique_meanings) == 1 else "conflict" if unique_meanings else "unknown"
            binding = {"id": f"SYM-{symbol_number:02d}", "name": name,
                       "meaning": unique_meanings[0] if len(unique_meanings) == 1 else None,
                       "status": status, "provenance": "source_explicit" if meanings else "unknown",
                       "evidence_anchor_ids": evidence, "values": values,
                       "reasons": ["附近存在多个不同定义，需要人工选择。"] if len(unique_meanings) > 1 else
                                  [] if meanings else ["未在附近文本找到明确符号定义。"],
                       "definition_candidates": meanings, "unit_candidates": definition_units,
                       "retrieval_candidates": [{"anchor_ids": mention["anchor_ids"], "quote": mention["text"],
                                                  "ranking_reason": mention["ranking_reason"]} for mention in mentions]}
            if not values:
                parameter_number += 1
                unit = definition_units[0] if len(definition_units) == 1 else None
                binding["values"] = [{"id": f"PAR-{parameter_number:02d}", "raw_value": None,
                                      "normalized_value": None, "raw_unit": unit["raw_unit"] if unit else None,
                                      "normalized_unit": normalize_unit(unit["raw_unit"]) if unit else None,
                                      "evidence_anchor_ids": unit["evidence_anchor_ids"] if unit else [],
                                      "provenance": "source_explicit" if unit else "unknown", "status": "unknown",
                                      "reasons": ["未找到该符号的明确数值。"]}]
            bindings.append(binding)
        equation["symbols"] = bindings
    return equations
