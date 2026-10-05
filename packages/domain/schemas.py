import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .errors import DomainError


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    action: Literal["confirm", "edit", "keep_unknown"]
    patch: dict[str, Any] = Field(default_factory=dict)
    evidence_anchor_ids: list[str] = Field(default_factory=list, max_length=50)


def validate_result(data: dict):
    """Reject broken coordinates, references and unsupported evidence assertions."""
    anchors = {anchor["id"]: anchor for anchor in data.get("anchors", [])}
    if len(anchors) != len(data.get("anchors", [])):
        raise DomainError("SOURCE_MISMATCH", "来源 ID 重复")
    for anchor in anchors.values():
        box = anchor.get("bbox", [])
        if len(box) != 4 or not all(isinstance(n, (int, float)) and math.isfinite(n) for n in box):
            raise DomainError("SOURCE_MISMATCH", "来源坐标无效")
        x0, y0, x1, y1 = box
        if not (anchor.get("page", 0) >= 1 and 0 <= x0 < x1 <= anchor["page_width"] + 0.1
                and 0 <= y0 < y1 <= anchor["page_height"] + 0.1
                and anchor["rendition_id"] == data["rendition_id"]):
            raise DomainError("SOURCE_MISMATCH", "来源超出页面或版本不匹配")

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"anchor_ids", "evidence_anchor_ids", "definition_anchor_ids", "value_anchor_ids"}:
                    if not isinstance(child, list) or any(ref not in anchors for ref in child):
                        raise DomainError("SOURCE_MISMATCH", "事实引用了无效的原文来源")
                visit(child)
            if value.get("provenance") == "source_explicit" and not value.get("evidence_anchor_ids"):
                raise DomainError("SOURCE_MISMATCH", "原文明示事实缺少证据")
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(data)
