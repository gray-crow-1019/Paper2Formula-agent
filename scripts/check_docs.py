"""Validate maintained Markdown and, when present, the authored demo."""
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "node_modules", "data", "dist", "build", ".next", ".pytest_cache",
             "__pycache__", ".venv", ".conda", "coverage", "uploads", "artifacts"}
OPTIONAL_EXAMPLES = {ROOT / "examples/result.json", ROOT / "examples/report.md"}


def markdown_files():
    for directory, children, files in os.walk(ROOT):
        children[:] = [name for name in children if name not in SKIP_DIRS]
        for name in files:
            if name.endswith(".md"):
                yield Path(directory) / name


def main():
    failures = []
    for file in markdown_files():
        body = file.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", body):
            if urlsplit(target).scheme or target.startswith("#"):
                continue
            path = unquote(target.split("#", 1)[0].split("?", 1)[0]).strip("<>")
            linked = (file.parent / path).resolve()
            # Hand-authored examples may be removed by the repository owner.
            if linked in OPTIONAL_EXAMPLES and not linked.exists():
                continue
            if not linked.exists():
                failures.append(f"{file.relative_to(ROOT)}: missing {target}")
        if body.count("```") % 2:
            failures.append(f"{file.name}: unclosed code fence")

    result_file = ROOT / "examples/result.json"
    if not result_file.exists():
        if failures:
            raise SystemExit("\n".join(failures))
        print("PASS: local documentation links and code fences (optional authored demo absent)")
        return
    data = json.loads(result_file.read_text(encoding="utf-8"))
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
    report_file = ROOT / "examples/report.md"
    if report_file.exists():
        report = report_file.read_text(encoding="utf-8")
        for item in data["equations"] + data["review_items"]:
            if item["id"] not in report:
                failures.append(f"report missing {item['id']}")
    if failures:
        raise SystemExit("\n".join(failures))
    print("PASS: local documentation links, code fences, demo coordinates and evidence references")


if __name__ == "__main__":
    main()
