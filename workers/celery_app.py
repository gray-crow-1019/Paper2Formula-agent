from celery import Celery

from packages.domain.config import get_settings
from packages.domain.database import Database
from packages.domain.storage import Storage
from packages.workflow.runner import WorkflowRunner

settings = get_settings()
celery_app = Celery("paper2formula", broker=settings.celery_broker_url)
celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json",
                       task_acks_late=True, task_reject_on_worker_lost=True,
                       worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True)


@celery_app.task(name="paper2formula.run", ignore_result=True)
def run_task(task_id: str):
    database = Database(settings)
    try:
        WorkflowRunner(database, Storage(settings), settings).run(task_id)
    finally:
        database.engine.dispose()
