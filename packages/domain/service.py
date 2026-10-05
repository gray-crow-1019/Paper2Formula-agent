import copy
import hashlib
import json
import time
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .errors import DomainError
from .models import Document, IdempotencyRecord, Outbox, ResultVersion, ReviewDecision, Task
from .schemas import validate_result

TERMINAL = {"completed", "failed", "cancelled"}


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def document_json(doc: Document) -> dict:
    return {"id": doc.id, "document_id": doc.id, "filename": doc.filename, "mime": doc.mime,
            "page_count": doc.page_count, "status": "ready" if doc.pdf_key else "uploaded",
            "rendition_id": doc.rendition_id, "document_version": 1, "sha256": doc.sha256,
            "size": doc.size, "created_at": timestamp(doc.created_at), "metadata": doc.metadata_json}


def task_json(task: Task) -> dict:
    return {"id": task.id, "task_id": task.id, "document_id": task.document_id, "status": task.status,
            "node": task.node, "revision": task.revision, "progress": task.progress, "error": task.error,
            "result_version": task.result_version, "cancel_requested": task.cancel_requested,
            "model_calls": task.model_calls,
            "created_at": timestamp(task.created_at), "updated_at": timestamp(task.updated_at)}


def owned_document(session, document_id: str, owner: str) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.owner_id != owner or document.deleted:
        raise DomainError("NOT_FOUND", "文档不存在", 404)
    return document


def owned_task(session, task_id: str, owner: str) -> Task:
    task = session.get(Task, task_id)
    if task is None or task.owner_id != owner:
        raise DomainError("NOT_FOUND", "任务不存在", 404)
    owned_document(session, task.document_id, owner)
    return task


def current_result(session, task: Task, version: int | None = None, draft: bool = True) -> ResultVersion:
    stmt = select(ResultVersion).where(ResultVersion.task_id == task.id)
    if version is not None:
        stmt = stmt.where(ResultVersion.version == version)
    if not draft:
        stmt = stmt.where(ResultVersion.status == "published")
    result = session.scalar(stmt.order_by(ResultVersion.version.desc()))
    if result is None:
        raise DomainError("RESULT_NOT_READY", "该版本尚未生成或发布", 404)
    return result


class DomainService:
    def __init__(self, database, storage, settings):
        self.database, self.storage, self.settings = database, storage, settings

    def create_task(self, document_id: str, owner: str, key: str | None) -> dict:
        if key and len(key) > 200:
            raise DomainError("INVALID_IDEMPOTENCY_KEY", "幂等键过长")
        scope = f"documents/{document_id}/tasks"
        request_hash = hashlib.sha256(document_id.encode()).hexdigest()
        try:
            with self.database.session() as session:
                doc = owned_document(session, document_id, owner)
                # Serialize creators before checking keys and active tasks.
                locked = session.execute(update(Document).where(Document.id == doc.id,
                    Document.deleted.is_(False)).values(size=Document.size)).rowcount
                if not locked:
                    raise DomainError("NOT_FOUND", "文档不存在", 404)
                if key:
                    previous = session.scalar(select(IdempotencyRecord).where(
                        IdempotencyRecord.owner_id == owner, IdempotencyRecord.scope == scope,
                        IdempotencyRecord.key == key))
                    if previous:
                        if previous.request_hash != request_hash:
                            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于其他请求", 409)
                        return task_json(owned_task(session, previous.task_id, owner))
                active = session.scalar(select(Task).where(Task.document_id == doc.id,
                                                           Task.status.not_in(TERMINAL)))
                if active:
                    raise DomainError("TASK_ACTIVE", "该文档已有进行中的任务", 409)
                task = Task(document_id=doc.id, owner_id=owner)
                session.add(task)
                session.flush()
                session.add(Outbox(task_id=task.id))
                if key:
                    session.add(IdempotencyRecord(owner_id=owner, scope=scope, key=key,
                                                  request_hash=request_hash, task_id=task.id))
                return task_json(task)
        except IntegrityError:
            if key:
                with self.database.session() as session:
                    row = session.scalar(select(IdempotencyRecord).where(
                        IdempotencyRecord.owner_id == owner, IdempotencyRecord.scope == scope,
                        IdempotencyRecord.key == key))
                    if row:
                        return task_json(owned_task(session, row.task_id, owner))
            raise DomainError("TASK_CONFLICT", "任务创建冲突，请重试", 409) from None

    def decide(self, task_id: str, review_id: str, owner: str, request) -> dict:
        with self.database.session() as session:
            task = owned_task(session, task_id, owner)
            if task.revision != request.expected_revision:
                raise DomainError("REVISION_CONFLICT", "复核版本已更新，请刷新后重试", 409)
            if task.status != "waiting_review":
                raise DomainError("TASK_NOT_REVIEWABLE", "任务当前不处于人工复核阶段", 409)
            result = current_result(session, task)
            data = copy.deepcopy(result.data)
            review = next((r for r in data["review_items"] if r["id"] == review_id), None)
            if not review:
                raise DomainError("NOT_FOUND", "复核项不存在", 404)
            if review["status"] != "pending":
                raise DomainError("REVIEW_RESOLVED", "复核项已处理", 409)
            allowed = {"latex", "explanation", "meaning", "raw_value", "raw_unit", "excluded"}
            if set(request.patch) - allowed or (request.action != "edit" and request.patch):
                raise DomainError("INVALID_REVIEW_PATCH", "无效的复核修改字段")
            if any((key == "excluded" and not isinstance(value, bool)) or
                   (key != "excluded" and value is not None and
                    (not isinstance(value, str) or len(value) > 10000))
                   for key, value in request.patch.items()):
                raise DomainError("INVALID_REVIEW_PATCH", "修改值类型或长度无效")
            if "latex" in request.patch and (not isinstance(request.patch["latex"], str) or not request.patch["latex"].strip()):
                raise DomainError("INVALID_REVIEW_PATCH", "公式不能为空")
            request.patch = {name: (None if isinstance(value, str) and not value.strip() and name != "latex" else value)
                             for name, value in request.patch.items()}
            anchors = {anchor["id"] for anchor in data["anchors"]}
            if any(ref not in anchors for ref in request.evidence_anchor_ids):
                raise DomainError("SOURCE_MISMATCH", "复核引用了无效的证据")
            equation = next((eq for eq in data["equations"] if eq["id"] == review.get("equation_id")), {})
            symbol = next((s for s in equation.get("symbols", []) if s["id"] == review.get("symbol_id")), None)
            before = copy.deepcopy({"review": review, "equation": equation})
            if request.action == "edit":
                if not equation:
                    raise DomainError("INVALID_REVIEW_PATCH", "此文档检查项只能保留未知")
                for name in {"latex", "explanation", "excluded"} & request.patch.keys():
                    equation[name] = request.patch[name]
                    equation["provenance"] = "user_supplied"
                if "explanation" in request.patch:
                    equation["explanation_provenance"] = "user_supplied"
                if "latex" in request.patch:
                    equation.setdefault("recognition", {})["reviewed"] = True
                    # Existing symbol bindings remain visible, but cannot silently assert new-symbol definitions.
                    equation["edited_latex"] = True
                for name in {"meaning", "raw_value", "raw_unit"} & request.patch.keys():
                    if symbol is None:
                        raise DomainError("INVALID_REVIEW_PATCH", "该复核项没有对应符号")
                    if name == "meaning":
                        symbol["meaning"] = request.patch[name]
                        symbol["status"] = "resolved" if request.patch[name] else "unknown"
                        symbol["provenance"] = "user_supplied"
                        symbol["evidence_anchor_ids"] = request.evidence_anchor_ids
                        symbol["reasons"] = [] if request.patch[name] else ["用户保留未知定义"]
                    else:
                        values = symbol.setdefault("values", [])
                        if not values:
                            values.append({"id": f"PAR-U-{review_id}", "raw_value": None,
                                           "normalized_value": None, "raw_unit": None, "normalized_unit": None,
                                           "status": "unknown", "reasons": ["用户补充参数"],
                                           "provenance": "user_supplied", "evidence_anchor_ids": []})
                        # First version edits a selected review's first parameter, preserving other conditions.
                        values[0][name] = request.patch[name]
                        from packages.paper_tools.resolve import normalize_unit, normalize_value
                        values[0]["normalized_value"] = (normalize_value(values[0]["raw_value"])
                                                         if values[0].get("raw_value") else None)
                        values[0]["normalized_unit"] = normalize_unit(values[0].get("raw_unit"))
                        values[0]["provenance"] = "user_supplied"
                        values[0]["evidence_anchor_ids"] = request.evidence_anchor_ids
                        values[0]["status"] = "known" if values[0].get("raw_value") and values[0].get("raw_unit") else "unknown"
                        values[0]["reasons"] = ([] if values[0]["status"] == "known" else
                                               ["用户补充仍缺少明确数值或单位。"])
                if not request.patch:
                    raise DomainError("INVALID_REVIEW_PATCH", "修改操作需要填写修改内容")
            if request.action == "confirm":
                if not equation:
                    raise DomainError("EVIDENCE_REQUIRED", "未识别到公式，请保留未知后导出")
                if review.get("kind") in {"missing_definition", "definition", "definitions"} and (
                    symbol is None or not symbol.get("meaning")):
                    raise DomainError("EVIDENCE_REQUIRED", "缺失定义请补充说明或保留未知")
                if review.get("kind") == "units" and equation.get("validation", {}).get("units", {}).get("status") != "pass":
                    raise DomainError("EVIDENCE_REQUIRED", "单位校验尚未通过，请修订单位或保留未知")
                equation.setdefault("recognition", {})["reviewed"] = True
            review["status"] = "resolved"
            review["decision"] = {"action": request.action, "patch": request.patch,
                                  "evidence_anchor_ids": request.evidence_anchor_ids,
                                  "owner_id": owner, "created_at": timestamp(time.time())}
            next_version = task.result_version + 1
            data["result_version"] = next_version
            data["status"] = "draft"
            data["manifest"] = {}
            validate_result(data)
            pending = any(r["status"] == "pending" for r in data["review_items"])
            changed = session.execute(update(Task).where(Task.id == task.id,
                Task.revision == request.expected_revision, Task.status == "waiting_review").values(
                    revision=Task.revision + 1, result_version=next_version,
                    status="waiting_review" if pending else "queued", updated_at=time.time())).rowcount
            if not changed:
                raise DomainError("REVISION_CONFLICT", "复核版本已更新，请刷新后重试", 409)
            session.add(ResultVersion(task_id=task.id, version=next_version, data=data))
            session.add(ReviewDecision(task_id=task.id, review_id=review_id, owner_id=owner,
                revision=request.expected_revision + 1, action=request.action, before=before,
                after={"review": copy.deepcopy(review), "equation": copy.deepcopy(equation)},
                evidence_anchor_ids=request.evidence_anchor_ids))
            if not pending:
                session.add(Outbox(task_id=task.id))
            session.flush()
            session.refresh(task)
            return {"revision": task.revision, "review": review, "task": task_json(task)}


def content_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
