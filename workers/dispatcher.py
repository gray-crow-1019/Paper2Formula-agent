import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import delete, select, update

from packages.domain.models import Document, IdempotencyRecord, NodeOutput, Outbox, ResultVersion, ReviewDecision, Task
from packages.workflow.runner import WorkflowRunner, persistent_checkpointer

log = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, database, storage, settings):
        self.database, self.storage, self.settings = database, storage, settings
        self.stop_event = threading.Event()
        self.thread = None
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="paper-worker")
        self.futures = {}
        self.last_recovery = 0.0

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="paper-outbox", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.pool.shutdown(wait=True, cancel_futures=True)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Outbox dispatcher failed")
            self.stop_event.wait(0.5)

    def _delete(self, document_id):
        with self.database.session() as session:
            doc = session.get(Document, document_id)
            if doc is None or not doc.deleted:
                return
            tasks = list(session.scalars(select(Task).where(Task.document_id == document_id)))
            ids = [task.id for task in tasks]
            # A stopped worker must first release its lease, before its files can be removed.
            if any(task.lease_until > time.time() for task in tasks):
                return
            if any(task_id in self.futures for task_id in ids):
                return
            if any(task.lease_token for task in tasks) and time.time() - (doc.deleted_at or time.time()) < (
                self.settings.task_timeout + self.settings.conversion_timeout + self.settings.model_timeout):
                return
        with persistent_checkpointer(self.settings) as saver:
            for task_id in ids:
                saver.delete_thread(task_id)
        self.storage.delete_document(document_id)
        with self.database.session() as session:
            for model in (ReviewDecision, NodeOutput, ResultVersion, IdempotencyRecord):
                session.execute(delete(model).where(model.task_id.in_(ids)))
            session.execute(delete(Outbox).where(Outbox.task_id.in_(ids)))
            session.execute(delete(Task).where(Task.id.in_(ids)))
            session.execute(delete(Document).where(Document.id == document_id))

    def tick(self):
        self.futures = {key: future for key, future in self.futures.items() if not future.done()}
        if time.time() - self.last_recovery > 5:
            self.last_recovery = time.time()
            with self.database.session() as session:
                tasks = list(session.scalars(select(Task).join(Document).where(Document.deleted.is_(False),
                    Task.cancel_requested.is_(False), Task.lease_until < time.time(),
                    Task.status.not_in(["completed", "cancelled", "waiting_review", "failed"]))))
                for task in tasks:
                    if task.id in self.futures:
                        continue
                    pending = session.scalar(select(Outbox.id).where(Outbox.task_id == task.id,
                                               Outbox.delivered.is_(False)))
                    if not pending:
                        session.add(Outbox(task_id=task.id))
                deleted_ids = list(session.scalars(select(Document.id).where(Document.deleted.is_(True))))
            for document_id in deleted_ids:
                self._delete(document_id)
        with self.database.session() as session:
            events = list(session.scalars(select(Outbox).where(Outbox.delivered.is_(False),
                                          Outbox.available_at <= time.time()).limit(30)))
            for event in events:
                if event.task_id in self.futures:
                    continue
                if self.settings.queue_backend == "local":
                    if len(self.futures) >= 2:
                        break
                    self.futures[event.task_id] = self.pool.submit(
                        WorkflowRunner(self.database, self.storage, self.settings).run, event.task_id)
                else:
                    from .celery_app import celery_app
                    celery_app.send_task("paper2formula.run", args=[event.task_id], task_id=event.id)
                session.execute(update(Outbox).where(Outbox.id == event.id).values(delivered=True))
