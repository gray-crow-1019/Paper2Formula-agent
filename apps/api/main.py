import hashlib
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, File, Header, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select, update

from packages.domain.config import Settings, get_settings
from packages.domain.database import Database
from packages.domain.errors import DomainError
from packages.domain.models import Document, Outbox, Task, new_id
from packages.domain.schemas import DecisionRequest
from packages.domain.service import DomainService, current_result, document_json, owned_document, owned_task, task_json
from packages.domain.storage import Storage
from workers.dispatcher import Dispatcher

from .auth import authenticated_owner

log = logging.getLogger(__name__)
MIMES = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    database = Database(settings)
    storage = Storage(settings)
    service = DomainService(database, storage, settings)
    dispatcher = Dispatcher(database, storage, settings)

    @asynccontextmanager
    async def lifespan(app):
        database.initialize()
        if settings.dispatcher_enabled:
            dispatcher.start()
        try:
            yield
        finally:
            dispatcher.stop()
            database.engine.dispose()

    app = FastAPI(title="Paper2Formula", version="0.1.0", lifespan=lifespan)
    app.state.settings, app.state.database, app.state.storage = settings, database, storage
    app.state.service, app.state.dispatcher = service, dispatcher
    app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
                       allow_credentials=False, allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = f"req_{uuid.uuid4().hex}"
        if request.method == "POST" and request.url.path == "/api/v1/documents":
            try:
                size = int(request.headers.get("content-length", "0"))
            except ValueError:
                size = 0
            if size > (settings.max_upload_mb + 1) * 1024 * 1024:
                return JSONResponse(status_code=413, content={"error": {"code": "FILE_TOO_LARGE",
                    "message": "文件超过上传大小限制", "retryable": False, "request_id": request.state.request_id}})
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message,
            "retryable": exc.retryable, "request_id": request.state.request_id}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={"error": {"code": "INVALID_REQUEST", "message": "请求参数无效",
            "retryable": False, "request_id": request.state.request_id}})

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        log.exception("Unhandled API error request=%s", getattr(request.state, "request_id", "unknown"), exc_info=exc)
        return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误",
            "retryable": False, "request_id": getattr(request.state, "request_id", "unknown")}})

    api = APIRouter(prefix="/api/v1")

    @api.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "model_provider": settings.model_provider,
                "auth_mode": settings.auth_mode, "queue_backend": settings.queue_backend}

    @api.get("/documents")
    def documents(owner: str = Depends(authenticated_owner)):
        with database.session() as session:
            docs = session.scalars(select(Document).where(Document.owner_id == owner,
                                   Document.deleted.is_(False)).order_by(Document.created_at.desc()).limit(100))
            return {"items": [document_json(doc) for doc in docs]}

    @api.post("/documents", status_code=201)
    async def upload(file: UploadFile = File(...), owner: str = Depends(authenticated_owner)):
        from packages.paper_tools import PaperTools
        filename = Path((file.filename or "document").replace("\\", "/")).name[:255]
        document_id = new_id()
        temporary = storage.path(f"documents/{document_id}/original/upload.bin")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        digest, total, prefix = hashlib.sha256(), 0, b""
        try:
            with temporary.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.max_upload_mb * 1024 * 1024:
                        raise DomainError("FILE_TOO_LARGE", "文件超过上传大小限制", 413)
                    if not prefix:
                        prefix = chunk[:1024]
                    digest.update(chunk)
                    stream.write(chunk)
            kind = "pdf" if prefix.startswith(b"%PDF-") else "docx" if prefix.startswith(b"PK\x03\x04") else None
            if kind is None:
                raise DomainError("INVALID_FILE", "只支持有效的 PDF 或 DOCX 文件")
            suffix = Path(filename).suffix.lower()
            if suffix != f".{kind}":
                raise DomainError("INVALID_FILE", "文件扩展名与实际格式不一致")
            try:
                inspection = PaperTools(settings).inspect(temporary, MIMES[kind])
            except Exception as exc:
                if hasattr(exc, "code"):
                    raise DomainError(exc.code, str(exc), 400, getattr(exc, "retryable", False)) from None
                raise
            final = temporary.with_name(f"source.{kind}")
            temporary.replace(final)
            key = storage.publish(final)
            with database.session() as session:
                doc = Document(id=document_id, owner_id=owner, filename=filename, mime=MIMES[kind],
                    original_key=key, sha256=digest.hexdigest(), size=total,
                    page_count=inspection.get("page_count"), metadata_json=inspection)
                session.add(doc)
                session.flush()
                return document_json(doc)
        except BaseException:
            storage.delete_document(document_id)
            raise
        finally:
            await file.close()

    @api.get("/documents/{document_id}")
    def document(document_id: str, owner: str = Depends(authenticated_owner)):
        with database.session() as session:
            return document_json(owned_document(session, document_id, owner))

    @api.delete("/documents/{document_id}", status_code=202)
    def delete_document(document_id: str, owner: str = Depends(authenticated_owner)):
        import time
        with database.session() as session:
            doc = owned_document(session, document_id, owner)
            doc.deleted = True
            doc.deleted_at = time.time()
            session.execute(update(Task).where(Task.document_id == doc.id).values(
                cancel_requested=True, status="cancelled", updated_at=time.time(), revision=Task.revision + 1))
            return {"document_id": doc.id, "status": "deleting"}

    @api.post("/documents/{document_id}/tasks", status_code=202)
    def create_task(document_id: str, owner: str = Depends(authenticated_owner),
                    idempotency_key: str | None = Header(default=None)):
        return service.create_task(document_id, owner, idempotency_key)

    @api.get("/documents/{document_id}/tasks")
    def document_tasks(document_id: str, owner: str = Depends(authenticated_owner)):
        with database.session() as session:
            owned_document(session, document_id, owner)
            tasks = session.scalars(select(Task).where(Task.document_id == document_id,
                                    Task.owner_id == owner).order_by(Task.created_at.desc()))
            return {"items": [task_json(task) for task in tasks]}

    @api.get("/documents/{document_id}/pdf")
    def pdf(document_id: str, owner: str = Depends(authenticated_owner)):
        with database.session() as session:
            doc = owned_document(session, document_id, owner)
            if not doc.pdf_key:
                raise DomainError("RENDITION_NOT_READY", "规范 PDF 尚未生成", 409)
            return FileResponse(storage.fetch(doc.pdf_key), media_type="application/pdf")

    @api.get("/tasks/{task_id}")
    def task(task_id: str, owner: str = Depends(authenticated_owner)):
        with database.session() as session:
            return task_json(owned_task(session, task_id, owner))

    @api.post("/tasks/{task_id}/cancel", status_code=202)
    def cancel(task_id: str, owner: str = Depends(authenticated_owner)):
        import time
        with database.session() as session:
            task = owned_task(session, task_id, owner)
            if task.status == "completed":
                raise DomainError("TASK_COMPLETED", "已完成的任务不能取消", 409)
            if task.status != "cancelled":
                session.execute(update(Task).where(Task.id == task_id).values(cancel_requested=True,
                    status="cancelled", revision=Task.revision + 1, updated_at=time.time()))
                session.refresh(task)
            return task_json(task)

    @api.post("/tasks/{task_id}/retry", status_code=202)
    def retry(task_id: str, owner: str = Depends(authenticated_owner)):
        import time
        with database.session() as session:
            task = owned_task(session, task_id, owner)
            if task.status != "failed" or not (task.error or {}).get("retryable"):
                raise DomainError("NOT_RETRYABLE", "该任务无法重试", 409)
            session.execute(update(Task).where(Task.id == task_id, Task.status == "failed").values(
                status="queued", error=None, attempts=0, updated_at=time.time(), revision=Task.revision + 1))
            session.add(Outbox(task_id=task.id))
            session.refresh(task)
            return task_json(task)

    @api.get("/tasks/{task_id}/reviews")
    def reviews(task_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500),
                owner: str = Depends(authenticated_owner)):
        with database.session() as session:
            task = owned_task(session, task_id, owner)
            data = current_result(session, task).data
            return {"items": data["review_items"][offset:offset + limit], "revision": task.revision,
                    "total": len(data["review_items"])}

    @api.post("/tasks/{task_id}/reviews/{review_id}/decisions", status_code=201)
    def decide(task_id: str, review_id: str, body: DecisionRequest, owner: str = Depends(authenticated_owner)):
        return service.decide(task_id, review_id, owner, body)

    @api.get("/tasks/{task_id}/results")
    def results(task_id: str, version: int | None = Query(default=None, ge=1), draft: bool = False,
                owner: str = Depends(authenticated_owner)):
        with database.session() as session:
            task = owned_task(session, task_id, owner)
            import copy
            result = copy.deepcopy(current_result(session, task, version, draft).data)
            for equation in result["equations"]:
                if equation.get("evidence_image"):
                    equation["evidence_image"] = (f"/api/v1/tasks/{task.id}/equations/{equation['id']}/image"
                                                  f"?version={result['result_version']}")
                equation.pop("evidence_image_key", None)
            for item in result.get("manifest", {}).values():
                item.pop("key", None)
            return result

    @api.get("/documents/{document_id}/anchors/{anchor_id}")
    def anchor(document_id: str, anchor_id: str, version: int = Query(ge=1),
               task_id: str | None = None, owner: str = Depends(authenticated_owner)):
        with database.session() as session:
            owned_document(session, document_id, owner)
            tasks = list(session.scalars(select(Task).where(Task.document_id == document_id, Task.owner_id == owner)))
            if task_id:
                tasks = [task for task in tasks if task.id == task_id]
            matches = []
            for task in tasks:
                try:
                    row = current_result(session, task, version)
                except DomainError:
                    continue
                matches.extend(a for a in row.data["anchors"] if a["id"] == anchor_id)
            if not matches:
                raise DomainError("NOT_FOUND", "来源位置不存在", 404)
            if len(matches) > 1 and not task_id:
                raise DomainError("AMBIGUOUS_VERSION", "多个任务包含该版本，请提供 task_id", 409)
            return matches[0]

    @api.get("/tasks/{task_id}/equations/{equation_id}/image")
    def equation_image(task_id: str, equation_id: str, version: int = Query(ge=1),
                       owner: str = Depends(authenticated_owner)):
        with database.session() as session:
            task = owned_task(session, task_id, owner)
            row = current_result(session, task, version)
            eq = next((eq for eq in row.data["equations"] if eq["id"] == equation_id), None)
            if not eq or not eq.get("evidence_image"):
                raise DomainError("NOT_FOUND", "公式截图不存在", 404)
            key = eq.get("evidence_image_key") or storage.key(Path(eq["evidence_image"]))
            if not key.startswith(f"documents/{task.document_id}/evidence/{task.id}/"):
                raise DomainError("SOURCE_MISMATCH", "证据文件不属于当前任务", 404)
            return FileResponse(storage.fetch(key), media_type="image/png")

    @api.get("/tasks/{task_id}/artifacts/{kind}")
    def artifact(task_id: str, kind: str, version: int = Query(ge=1), owner: str = Depends(authenticated_owner)):
        if kind not in {"markdown", "json", "annotated_pdf"}:
            raise DomainError("NOT_FOUND", "产物类型不存在", 404)
        with database.session() as session:
            task = owned_task(session, task_id, owner)
            row = current_result(session, task, version, False)
            item = row.manifest.get(kind)
            if not item:
                raise DomainError("RESULT_NOT_READY", "产物尚未发布", 404)
            path = storage.fetch(item["key"])
            types = {"markdown": "text/markdown; charset=utf-8", "json": "application/json", "annotated_pdf": "application/pdf"}
            return FileResponse(path, media_type=types[kind], filename=path.name)

    app.include_router(api)
    return app


app = create_app()
