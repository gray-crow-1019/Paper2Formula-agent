import copy
import time

from sqlalchemy import select, update

from packages.domain.errors import DomainError
from packages.domain.models import Document, NodeOutput, Task
from packages.domain.service import current_result


NODES = ["inspect", "normalize", "parse", "recognize", "resolve", "relate", "validate", "export"]


class RuntimeSettings:
    """Per-task runtime guards supplement immutable deployment settings."""
    def __init__(self, settings, database, task_id: str, owner: str):
        self.settings, self.database, self.task_id, self.owner = settings, database, task_id, owner

    def __getattr__(self, name):
        return getattr(self.settings, name)

    def _task(self, session):
        task = session.get(Task, self.task_id)
        document = session.get(Document, task.document_id) if task else None
        if not task or task.owner_id != self.owner or not document or document.deleted or task.cancel_requested:
            raise DomainError("CANCELLED", "任务已取消", 409)
        if not task.lease_token or task.lease_until <= time.time():
            raise DomainError("LEASE_LOST", "任务执行租约已失效", 409, True)
        if task.run_deadline <= time.time():
            raise DomainError("TASK_TIMEOUT", "任务超过配置的执行时间", 408, True)
        return task

    def check_processing_budget(self):
        with self.database.session() as session:
            self._task(session)

    def claim_model_call(self):
        with self.database.session() as session:
            task = self._task(session)
            changed = session.execute(update(Task).where(Task.id == task.id, Task.lease_token == task.lease_token,
                Task.model_calls < self.settings.model_max_calls, Task.cancel_requested.is_(False),
                Task.lease_until > time.time()).values(model_calls=Task.model_calls + 1)).rowcount
            if not changed:
                raise DomainError("MODEL_BUDGET", "已达到任务累计模型调用次数限制")


def execute_operation(database, storage, settings, task_id: str, node: str, owner: str) -> dict:
    """The common implementation used by local tools and the controlled MCP process."""
    from packages.paper_tools import PaperTools
    if node not in NODES:
        raise DomainError("UNKNOWN_TOOL", "未授权的工具")
    with database.session() as session:
        task = session.get(Task, task_id)
        doc = session.get(Document, task.document_id) if task else None
        if task is None or task.owner_id != owner or doc is None or doc.deleted or doc.owner_id != owner:
            raise DomainError("NOT_FOUND", "任务不存在", 404)
        result = None
        if node in {"resolve", "relate", "validate", "export"}:
            result = copy.deepcopy(current_result(session, task).data)
        parsed_row = session.scalar(select(NodeOutput).where(NodeOutput.task_id == task_id,
                                   NodeOutput.node == "parse")) if node in {"recognize", "resolve", "relate"} else None
        parsed = copy.deepcopy(parsed_row.output) if parsed_row else None
        document_id, original_key, mime = doc.id, doc.original_key, doc.mime
        rendition_id, pdf_key = doc.rendition_id, doc.pdf_key
    tools = PaperTools(RuntimeSettings(settings, database, task_id, owner))
    directory = storage.path(f"documents/{document_id}")
    original = storage.fetch(original_key)
    if node == "inspect":
        return tools.inspect(original, mime)
    if node == "normalize":
        if pdf_key:
            pdf_path = storage.fetch(pdf_key)
            return {"pdf_path": str(pdf_path), "sha256": doc.metadata_json.get("pdf_sha256"),
                    "page_count": doc.page_count, "converter_version": doc.metadata_json.get("converter_version"),
                    "rendition_id": rendition_id, "pages": doc.metadata_json.get("pages", [])}
        return tools.normalize(original, mime, directory / "normalized")
    if not pdf_key or not rendition_id:
        raise DomainError("RENDITION_NOT_READY", "规范 PDF 尚未生成", 409)
    pdf = storage.fetch(pdf_key)
    if node == "parse":
        return tools.parse(pdf, rendition_id, 1)
    if node == "recognize":
        return {"equations": tools.recognize(parsed, pdf, directory / "evidence" / task_id),
                "anchors": parsed["anchors"]}
    if node == "resolve":
        return {"equations": tools.resolve(result["equations"], parsed)}
    if node == "relate":
        return {"relations": tools.relate(result["equations"], parsed)}
    if node == "validate":
        return tools.validate(result)
    if node == "export":
        result["status"] = "published"
        return tools.export(result, pdf, directory / "artifacts" / task_id / f"v{result['result_version']}")
    raise AssertionError(node)
