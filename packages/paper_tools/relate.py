"""Record only explicit in-text equation references; never infer derivations."""
from __future__ import annotations

import re


def build_equation_relations(equations: list[dict], parsed: dict) -> list[dict]:
    indexed = {}
    for equation in equations:
        if equation.get("status") != "excluded" and equation.get("original_number"):
            indexed.setdefault(equation["original_number"], []).append(equation)
    relations = []
    seen = set()
    references = re.compile(r"(?:eq(?:uation)?s?\.?|公式|式)\s*(?:\(|（)\s*(\d+(?:[.\-]\d+)*[a-z]?)\s*(?:\)|）)", re.I)
    for block in parsed["blocks"]:
        matches = list(references.finditer(block["text"]))
        if not matches:
            continue
        for match in matches:
            targets = indexed.get(match.group(1), [])
            if len(targets) != 1:
                continue
            # A simple reference has evidence, but attaching it to the nearest
            # equation would invent a relationship. Only use two explicit numbers
            # in the same statement as an equation-to-equation reference.
            for source_match in matches:
                sources = indexed.get(source_match.group(1), [])
                if len(sources) != 1 or sources[0]["id"] == targets[0]["id"]:
                    continue
                identity = (sources[0]["id"], targets[0]["id"], block["id"])
                if identity in seen:
                    continue
                seen.add(identity)
                relations.append({"id": f"REL-{len(relations) + 1:02d}", "from_equation_id": sources[0]["id"],
                                  "to_equation_id": targets[0]["id"], "relation_type": "explicit_co_reference",
                                  "evidence_anchor_ids": block["anchor_ids"], "provenance": "source_explicit",
                                  "reasons": ["同一句原文明确共同引用这两个公式；未推断推导方向。"]})
    return relations
