"""Validate local Markdown links and provenance in the authored demo."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    failures = []
    for file in ROOT.rglob("*.md"):
        if ".git" in file.parts:
            continue
        body = file.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", body):
            if "://" in target or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            if not (file.parent / path).is_file():
                failures.append(f"{file.relative_to(ROOT)}: missing {target}")
        if body.count("```") % 2:
            failures.append(f"{file.name}: unclosed code fence")

    data = json.loads((ROOT / "examples/result.json").read_text(encoding="utf-8"))
    anchors = {a["id"]: a for a in data["anchors"]}
    if len(anchors) != len(data["anchors"]):
        failures.append("duplicate anchor IDs")
    for anchor in anchors.values():
        x0, y0, x1, y1 = anchor["bbox"]
        if not (anchor["page"] >= 1 and 0 <= x0 < x1 <= anchor["page_width"]
                and 0 <= y0 < y1 <= anchor["page_height"]):
            failures.append(f"invalid coordinates: {anchor['id']}")

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"evidence_anchor_ids", "anchor_ids"}:
                    for ref in child:
                        if ref not in anchors:
                            failures.append(f"unknown anchor {ref}")
                visit(child)
            if value.get("provenance") == "source_explicit" and not value.get("evidence_anchor_ids"):
                failures.append("explicit source fact without evidence")
            if value.get("status") == "unknown" and not value.get("reasons"):
                failures.append("unknown fact without reason")
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    report = (ROOT / "examples/report.md").read_text(encoding="utf-8")
    for item in data["equations"] + data["review_items"]:
        if item["id"] not in report:
            failures.append(f"report missing {item['id']}")
    if failures:
        raise SystemExit("\n".join(failures))
    print("PASS: local documentation links, code fences, demo coordinates and evidence references")


if __name__ == "__main__":
    main()
