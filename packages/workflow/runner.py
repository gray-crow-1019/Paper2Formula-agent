import copy
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select, update
from sqlalchemy.engine import make_url

from packages.domain.config import ROOT
from packages.domain.errors import DomainError
from packages.domain.models import Document, NodeOutput, Outbox, ResultVersion, Task, new_id
from packages.domain.schemas import validate_result
from packages.domain.service import content_hash, current_result

from .operations import execute_operation

log = logging.getLogger(__name__)
VERSION = "0.1.0"
STAGES = [("inspect", "inspecting", 5), ("normalize", "normalizing", 15), ("parse", "parsing", 30),
          ("recognize", "recognizing", 45), ("resolve", "resolving", 60), ("relate", "relating", 70),
          ("validate", "validating", 80), ("export", "exporting", 90)]


class WorkflowState(TypedDict):
    task_id: str
    current_node: str
    result_version: int


@contextmanager
def persistent_checkpointer(settings):
    url = make_url(settings.checkpoint_database_url)
    if url.drivername.startswith("sqlite"):
        from langgraph.checkpoint.sqlite import SqliteSaver
        path = (ROOT / (url.database or "data/checkpoints.db")).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield SqliteSaver(connection)
        finally:
            connection.close()
    else:
        from langgraph.checkpoint.postgres import PostgresSaver
        connection_url = url.set(drivername="postgresql").render_as_string(hide_password=False)
        with PostgresSaver.from_conn_string(connection_url) as saver:
            saver.setup()
            yield saver


class WorkflowRunner:
    def __init__(self, database, storage, settings):
        self.database, self.storage, self.settings = database, storage, settings
        self.task_id = ""
        self.token = ""
        self.started = 0.0

    def _check(self, session) -> Task:
        task = session.get(Task, self.task_id)
        if not task or task.lease_token != self.token or task.lease_until < time.time():
            raise DomainError("LEASE_LOST", "任务执行租约已失效", 409, True)
        doc = session.get(Document, task.document_id)
        if task.cancel_requested or task.status == "cancelled" or not doc or doc.deleted:
            raise DomainError("CANCELLED", "任务已取消", 409)
        if time.monotonic() - self.started > self.settings.task_timeout:
            raise DomainError("TASK_TIMEOUT", "任务超过配置的执行时间", 408, True)
        return task

    def _heartbeat(self, stop):
        while not stop.wait(max(1, self.settings.task_lease_seconds / 3)):
            try:
                with self.database.session() as session:
                    session.execute(update(Task).where(Task.id == self.task_id, Task.lease_token == self.token,
                        Task.cancel_requested.is_(False), Task.lease_until > time.time()).values(
                            lease_until=time.time() + self.settings.task_lease_seconds))
            except Exception:
                log.exception("Heartbeat failed for task %s", self.task_id)

    def _commit(self, session, task, **values):
        revision = task.revision
        result = session.execute(update(Task).where(Task.id == self.task_id, Task.lease_token == self.token,
                Task.revision == revision, Task.cancel_requested.is_(False), Task.lease_until > time.time()).values(
                    **values, revision=Task.revision + 1, updated_at=time.time()))
        if not result.rowcount:
            raise DomainError("LEASE_LOST", "任务状态或租约发生变化", 409, True)
        session.flush()

    def _stage(self, name: str, status: str, progress: int):
        def node(state):
            with self.database.session() as session:
                task = self._check(session)
                doc = session.get(Document, task.document_id)
                config = {"implementation": VERSION, "model": self.settings.model_name,
                          "provider": self.settings.model_provider, "max_calls": self.settings.model_max_calls}
                hash_input = {"document": doc.sha256, "node": name, "config": config}
                if name in {"resolve", "relate", "validate", "export"}:
                    hash_input["result"] = current_result(session, task).data
                key = content_hash(hash_input)
                previous = session.scalar(select(NodeOutput).where(NodeOutput.task_id == task.id,
                                          NodeOutput.node == name, NodeOutput.input_hash == key))
                output = copy.deepcopy(previous.output) if previous else None
                owner = task.owner_id
                self._commit(session, task, status=status, node=name, progress=progress)
            if output is None:
                if self.settings.tool_transport == "mcp":
                    from .mcp_client import call_mcp
                    output = call_mcp(self.settings, self.task_id, name, owner)
                else:
                    output = execute_operation(self.database, self.storage, self.settings, self.task_id, name, owner)
            with self.database.session() as session:
                task = self._check(session)
                doc = session.get(Document, task.document_id)
                node_record = None
                if previous is None:
                    node_record = NodeOutput(task_id=task.id, node=name, input_hash=key, output=output)
                    session.add(node_record)
                if name == "normalize":
                    from pathlib import Path
                    if output.get("pdf_key"):
                        doc.pdf_key = output["pdf_key"]
                        self.storage.fetch(doc.pdf_key)
                    else:
                        doc.pdf_key = self.storage.publish(Path(output["pdf_path"]))
                        output["pdf_key"] = doc.pdf_key
                    doc.rendition_id = doc.rendition_id or new_id()
                    doc.page_count = output["page_count"]
                    doc.metadata_json = {**doc.metadata_json, "pdf_sha256": output["sha256"],
                                         "converter_version": output.get("converter_version"),
                                         "pages": output.get("pages", [])}
                if name == "recognize":
                    from pathlib import Path
                    for equation in output["equations"]:
                        if equation.get("evidence_image") and not equation.get("evidence_image_key"):
                            equation["evidence_image_key"] = self.storage.publish(Path(equation["evidence_image"]))
                    data = {"schema_version": "1.0", "document_id": doc.id, "rendition_id": doc.rendition_id,
                            "result_version": task.result_version, "status": "draft", "anchors": output["anchors"],
                            "equations": output["equations"], "review_items": [], "relations": [], "manifest": {},
                            "processing": {**config, "prompt_version": VERSION, "input_hash": doc.sha256}}
                    existing = session.scalar(select(ResultVersion).where(ResultVersion.task_id == task.id,
                                               ResultVersion.version == task.result_version))
                    if existing is None:
                        validate_result(data)
                        session.add(ResultVersion(task_id=task.id, version=task.result_version, data=data))
                if name in {"resolve", "relate", "validate"}:
                    row = current_result(session, task)
                    data = copy.deepcopy(row.data)
                    if name == "validate":
                        data = output
                        data["validated_result_version"] = task.result_version
                    else:
                        data.update(output)
                    validate_result(data)
                    row.data = data
                if name == "export":
                    from pathlib import Path
                    row = current_result(session, task)
                    manifest = {}
                    artifacts = output.get("artifacts", output)
                    for kind in ("markdown", "json", "annotated_pdf"):
                        artifact = artifacts[kind]
                        path = (self.storage.fetch(artifact["key"]) if isinstance(artifact, dict) and artifact.get("key")
                                else Path(artifact["path"] if isinstance(artifact, dict) else artifact))
                        from packages.domain.storage import file_hash
                        if not path.is_file():
                            raise DomainError("EXPORT_FAILED", "导出产物缺失", 500, True)
                        manifest[kind] = {"key": self.storage.publish(path), "sha256": file_hash(path),
                                          "size": path.stat().st_size, "result_version": row.version}
                        if isinstance(artifact, dict):
                            artifact["key"] = manifest[kind]["key"]
                    data = copy.deepcopy(row.data)
                    data["status"], data["manifest"] = "published", manifest
                    validate_result(data)
                    row.data, row.manifest, row.status = data, manifest, "published"
                    if node_record:
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(node_record, "output")
                    self._commit(session, task, status="completed", node="export", progress=100)
                else:
                    if node_record:
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(node_record, "output")
                    self._commit(session, task, status=status, node=name, progress=progress)
                return {"task_id": task.id, "current_node": name, "result_version": task.result_version}
        return node

    def _review_gate(self, state):
        with self.database.session() as session:
            task = self._check(session)
            data = current_result(session, task).data
            pending = [item["id"] for item in data["review_items"] if item["status"] == "pending"]
            if pending:
                self._commit(session, task, status="waiting_review", node="review_gate", progress=85)
        if pending:
            interrupt({"task_id": self.task_id, "review_ids": pending})
        return {"current_node": "review_gate"}

    def _route_review(self, state):
        with self.database.session() as session:
            task = self._check(session)
            data = current_result(session, task).data
            # Revalidate changed results after resuming, then return through the gate.
            if data.get("validated_result_version") != task.result_version:
                return "validate"
            return "export"

    def build_graph(self, checkpointer):
        builder = StateGraph(WorkflowState)
        for name, status, progress in STAGES:
            builder.add_node(name, self._stage(name, status, progress))
        builder.add_node("review_gate", self._review_gate)
        builder.add_edge(START, "inspect")
        names = [name for name, _, _ in STAGES[:-1]]
        for before, after in zip(names, names[1:]):
            builder.add_edge(before, after)
        builder.add_edge("validate", "review_gate")
        builder.add_conditional_edges("review_gate", self._route_review, {"validate": "validate", "export": "export"})
        builder.add_edge("export", END)
        return builder.compile(checkpointer=checkpointer)

    def run(self, task_id: str):
        self.task_id, self.token, self.started = task_id, new_id(), time.monotonic()
        with self.database.session() as session:
            task = session.get(Task, task_id)
            if task is None or task.status in {"completed", "cancelled", "waiting_review", "failed"}:
                return
            changed = session.execute(update(Task).where(Task.id == task_id, Task.lease_until < time.time(),
                Task.cancel_requested.is_(False), Task.status.not_in(["completed", "cancelled", "waiting_review", "failed"])).values(
                    lease_token=self.token, lease_until=time.time() + self.settings.task_lease_seconds,
                    run_deadline=time.time() + self.settings.task_timeout, error=None)).rowcount
            if not changed:
                return
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(stop,), daemon=True)
        heartbeat.start()
        try:
            with persistent_checkpointer(self.settings) as saver:
                graph = self.build_graph(saver)
                config = {"configurable": {"thread_id": task_id}, "recursion_limit": 50}
                snapshot = graph.get_state(config)
                if snapshot.next:
                    interrupted = any(getattr(task, "interrupts", ()) for task in snapshot.tasks)
                    input_state = Command(resume=True) if interrupted else None
                else:
                    input_state = {"task_id": task_id, "current_node": "inspect", "result_version": 1}
                graph.invoke(input_state, config=config)
        except Exception as exc:
            code = getattr(exc, "code", "INTERNAL_ERROR")
            retryable = getattr(exc, "retryable", False)
            message = str(exc) if code != "INTERNAL_ERROR" else "任务执行失败，请检查服务日志"
            log.exception("Workflow failed task=%s code=%s", task_id, code)
            with self.database.session() as session:
                task = session.get(Task, task_id)
                if task and task.lease_token == self.token:
                    cancelled = code == "CANCELLED" or task.cancel_requested
                    task.status = "cancelled" if cancelled else "failed"
                    task.error = None if cancelled else {"code": code, "message": message, "retryable": retryable}
                    task.updated_at = time.time()
                    task.revision += 1
                    task.attempts += 1
                    if retryable and task.attempts < 3 and code != "LEASE_LOST":
                        task.status = "queued"
                        session.add(Outbox(task_id=task_id, available_at=time.time() + 2 ** task.attempts))
        finally:
            stop.set()
            heartbeat.join(timeout=3)
            with self.database.session() as session:
                session.execute(update(Task).where(Task.id == task_id, Task.lease_token == self.token).values(
                    lease_token=None, lease_until=0))
