import hashlib
import json
import time

import fitz
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.api.main import create_app
from packages.domain.config import Settings
from packages.domain.models import NodeOutput, Outbox, Task
from packages.workflow.runner import WorkflowRunner


@pytest.fixture
def system(tmp_path):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'domain.db').as_posix()}",
                        checkpoint_database_url=f"sqlite:///{(tmp_path / 'checkpoints.db').as_posix()}",
                        storage_dir=tmp_path / "objects", dispatcher_enabled=False,
                        auth_mode="jwt", jwt_secret="test-secret-" * 4, model_provider="heuristic")
    app = create_app(settings)

    def headers(subject="alice"):
        token = jwt.encode({"sub": subject, "exp": int(time.time()) + 3600,
                            "iss": settings.jwt_issuer, "aud": settings.jwt_audience},
                           settings.jwt_secret, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        yield client, app, settings, headers


def paper_bytes(equation="F = m * a (1)"):
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((60, 60), "1 Introduction")
        page.insert_text((100, 110), equation)
        page.insert_text((60, 150), "where F is force (N), m is mass (kg), and a is acceleration (m/s^2).")
        page.insert_text((60, 175), "The measured values are m = 2 kg and a = 3 m/s^2.")
        return doc.tobytes()


def create_task(system, raw=None):
    client, app, settings, headers = system
    upload = client.post("/api/v1/documents", headers=headers(),
                         files={"file": ("paper.pdf", raw or paper_bytes(), "application/pdf")})
    assert upload.status_code == 201, upload.text
    document = upload.json()
    response = client.post(f"/api/v1/documents/{document['id']}/tasks",
                           headers={**headers(), "Idempotency-Key": "unique-input"})
    assert response.status_code == 202, response.text
    return document, response.json()


def run(system, task_id):
    _, app, settings, _ = system
    WorkflowRunner(app.state.database, app.state.storage, settings).run(task_id)


def resolve_all(system, task_id):
    client, _, _, headers = system
    while True:
        response = client.get(f"/api/v1/tasks/{task_id}/reviews", headers=headers()).json()
        pending = next((item for item in response["items"] if item["status"] == "pending"), None)
        if not pending:
            break
        decision = client.post(f"/api/v1/tasks/{task_id}/reviews/{pending['id']}/decisions", headers=headers(),
            json={"expected_revision": response["revision"], "action": "keep_unknown"})
        assert decision.status_code == 201, decision.text


def test_upload_review_restart_and_coherent_publication(system):
    client, app, _, headers = system
    original = paper_bytes()
    document, task = create_task(system, original)
    duplicate = client.post(f"/api/v1/documents/{document['id']}/tasks",
                            headers={**headers(), "Idempotency-Key": "unique-input"})
    assert duplicate.json()["id"] == task["id"]
    with app.state.database.session() as session:
        assert session.scalar(select(func.count()).select_from(Outbox)) == 1
    run(system, task["id"])
    state = client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()
    assert state["status"] == "waiting_review", state
    draft = client.get(f"/api/v1/tasks/{task['id']}/results?draft=true&version=1", headers=headers()).json()
    assert draft["equations"][0]["id"] == "EQ-01"
    assert client.get(f"/api/v1/tasks/{task['id']}/results", headers=headers()).status_code == 404
    assert client.get(f"/api/v1/tasks/{task['id']}/artifacts/json?version=1", headers=headers()).status_code == 404
    resolve_all(system, task["id"])
    # New runner instance reads the same persisted LangGraph checkpoint after human decisions.
    run(system, task["id"])
    state = client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()
    assert state["status"] == "completed", state
    result = client.get(f"/api/v1/tasks/{task['id']}/results", headers=headers()).json()
    assert set(result["manifest"]) == {"markdown", "json", "annotated_pdf"}
    for kind, artifact in result["manifest"].items():
        downloaded = client.get(f"/api/v1/tasks/{task['id']}/artifacts/{kind}?version={state['result_version']}",
                                headers=headers())
        assert downloaded.status_code == 200
        assert hashlib.sha256(downloaded.content).hexdigest() == artifact["sha256"]
        if kind == "json":
            exported = json.loads(downloaded.content)
            assert exported["result_version"] == result["result_version"]
            assert exported["equations"][0]["id"] == result["equations"][0]["id"]
        if kind == "annotated_pdf":
            with fitz.open(stream=downloaded.content, filetype="pdf") as pdf:
                assert len(list(pdf[0].annots())) >= 4
    doc = client.get(f"/api/v1/documents/{document['id']}", headers=headers()).json()
    assert doc["sha256"] == hashlib.sha256(original).hexdigest()
    run(system, task["id"])
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()["revision"] == state["revision"]


def test_permission_and_revision_conflicts(system):
    client, _, _, headers = system
    document, task = create_task(system)
    run(system, task["id"])
    reviews = client.get(f"/api/v1/tasks/{task['id']}/reviews", headers=headers()).json()
    review = reviews["items"][0]
    url = f"/api/v1/tasks/{task['id']}/reviews/{review['id']}/decisions"
    data = {"expected_revision": reviews["revision"], "action": "keep_unknown"}
    assert client.post(url, headers=headers(), json=data).status_code == 201
    assert client.post(url, headers=headers(), json=data).status_code == 409
    for path in [f"/documents/{document['id']}", f"/documents/{document['id']}/pdf",
                 f"/tasks/{task['id']}", f"/tasks/{task['id']}/reviews",
                 f"/tasks/{task['id']}/results?draft=true", f"/tasks/{task['id']}/artifacts/json?version=1"]:
        assert client.get(f"/api/v1{path}", headers=headers("bob")).status_code == 404
    assert client.get("/api/v1/documents").status_code == 401
    assert client.get("/api/v1/documents", headers=headers("bob")).json()["items"] == []


def test_unknown_definition_cannot_be_confirmed_and_cancel_is_terminal(system):
    client, _, _, headers = system
    _, task = create_task(system, paper_bytes("x = y + 1 (1)"))
    run(system, task["id"])
    reviews = client.get(f"/api/v1/tasks/{task['id']}/reviews", headers=headers()).json()
    target = next(item for item in reviews["items"] if item["kind"] == "definitions")
    rejected = client.post(f"/api/v1/tasks/{task['id']}/reviews/{target['id']}/decisions", headers=headers(),
        json={"expected_revision": reviews["revision"], "action": "confirm"})
    assert rejected.status_code == 400
    for _ in range(2):
        assert client.post(f"/api/v1/tasks/{task['id']}/cancel", headers=headers()).json()["status"] == "cancelled"
    run(system, task["id"])
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()["status"] == "cancelled"


def test_invalid_uploads_and_immediate_delete_revocation(system):
    client, app, _, headers = system
    bad = client.post("/api/v1/documents", headers=headers(), files={"file": ("fake.pdf", b"hello", "application/pdf")})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_FILE"
    document, task = create_task(system)
    assert client.delete(f"/api/v1/documents/{document['id']}", headers=headers()).status_code == 202
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).status_code == 404
    run(system, task["id"])
    app.state.dispatcher.tick()
    assert not app.state.storage.path(f"documents/{document['id']}").exists()


def test_export_failure_resumes_without_repeating_recognition(system, monkeypatch):
    from packages.paper_tools import PaperToolError, PaperTools
    client, app, _, headers = system
    _, task = create_task(system)
    run(system, task["id"])
    resolve_all(system, task["id"])
    real_export = PaperTools.export
    calls = 0

    def fail_once(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PaperToolError("EXPORT_FAILED", "temporary export failure", retryable=True)
        return real_export(self, *args, **kwargs)

    monkeypatch.setattr(PaperTools, "export", fail_once)
    run(system, task["id"])
    state = client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()
    assert state["status"] in {"queued", "failed"}
    assert client.get(f"/api/v1/tasks/{task['id']}/results", headers=headers()).status_code == 404
    run(system, task["id"])
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()["status"] == "completed"
    with app.state.database.session() as session:
        assert session.scalar(select(func.count()).select_from(NodeOutput).where(
            NodeOutput.task_id == task["id"], NodeOutput.node == "recognize")) == 1
        assert session.get(Task, task["id"]).attempts == 1


def test_real_mcp_stdio_workflow_and_owner_scope(system):
    from packages.workflow.mcp_client import call_mcp
    from packages.domain.errors import DomainError
    client, app, settings, headers = system
    _, task = create_task(system)
    settings.tool_transport = "mcp"
    # Tool process inherits effective settings and an owner bound by its parent, not tool arguments.
    assert call_mcp(settings, task["id"], "inspect", "alice")["page_count"] == 1
    with pytest.raises(DomainError) as error:
        call_mcp(settings, task["id"], "inspect", "bob")
    assert error.value.code == "NOT_FOUND"
    run(system, task["id"])
    state = client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()
    assert state["status"] == "waiting_review", state
    resolve_all(system, task["id"])
    run(system, task["id"])
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()["status"] == "completed"


def test_concurrent_create_serializes_by_document(system):
    from concurrent.futures import ThreadPoolExecutor
    client, app, _, headers = system
    response = client.post("/api/v1/documents", headers=headers(),
                           files={"file": ("paper.pdf", paper_bytes(), "application/pdf")})
    document = response.json()

    def create():
        return app.state.service.create_task(document["id"], "alice", "same-concurrent-key")["id"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(lambda _: create(), range(4)))
    assert len(set(ids)) == 1
    with app.state.database.session() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 1


def test_latex_edit_adds_unknown_symbol_without_reusing_definitions(system):
    client, _, _, headers = system
    _, task = create_task(system)
    run(system, task["id"])
    reviews = client.get(f"/api/v1/tasks/{task['id']}/reviews", headers=headers()).json()
    target = next(item for item in reviews["items"] if item["kind"] == "recognition")
    response = client.post(f"/api/v1/tasks/{task['id']}/reviews/{target['id']}/decisions", headers=headers(),
        json={"expected_revision": reviews["revision"], "action": "edit", "patch": {"latex": "F = q * a"}})
    assert response.status_code == 201
    resolve_all(system, task["id"])
    run(system, task["id"])
    result = client.get(f"/api/v1/tasks/{task['id']}/results?draft=true", headers=headers()).json()
    q = next(symbol for symbol in result["equations"][0]["symbols"] if symbol["name"] == "q")
    assert q["meaning"] is None
    assert q["status"] == "unknown"
    assert any(item["symbol_id"] == q["id"] and item["status"] == "pending" for item in result["review_items"])


def test_background_dispatcher_runs_and_resumes_without_manual_runner(system):
    client, app, _, headers = system
    app.state.dispatcher.start()
    _, task = create_task(system)

    def wait_status(expected):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()
            if state["status"] == expected:
                return state
            assert state["status"] != "failed", state
            time.sleep(0.05)
        pytest.fail(f"Background task did not reach {expected}: {state}")

    wait_status("waiting_review")
    resolve_all(system, task["id"])
    wait_status("completed")


def test_model_budget_persists_across_runtime_instances(system):
    from packages.domain.errors import DomainError
    from packages.workflow.operations import RuntimeSettings
    _, app, settings, _ = system
    _, task = create_task(system)
    settings.model_max_calls = 2
    with app.state.database.session() as session:
        row = session.get(Task, task["id"])
        row.lease_token = "test-lease"
        row.lease_until = time.time() + 60
        row.run_deadline = time.time() + 60
    RuntimeSettings(settings, app.state.database, task["id"], "alice").claim_model_call()
    # A recreated adapter/worker does not get a fresh model request allowance.
    RuntimeSettings(settings, app.state.database, task["id"], "alice").claim_model_call()
    with pytest.raises(DomainError) as error:
        RuntimeSettings(settings, app.state.database, task["id"], "alice").claim_model_call()
    assert error.value.code == "MODEL_BUDGET"


def test_cancel_during_tool_call_cannot_publish_stale_outputs(system, monkeypatch):
    from packages.paper_tools import PaperTools
    client, _, _, headers = system
    _, task = create_task(system)
    recognize = PaperTools.recognize

    def cancelled(self, *args):
        client.post(f"/api/v1/tasks/{task['id']}/cancel", headers=headers())
        return recognize(self, *args)

    monkeypatch.setattr(PaperTools, "recognize", cancelled)
    run(system, task["id"])
    state = client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()
    assert state["status"] == "cancelled"
    assert client.get(f"/api/v1/tasks/{task['id']}/results?draft=true", headers=headers()).status_code == 404


def test_no_equations_can_export_only_after_explicit_review(system):
    client, _, _, headers = system
    _, task = create_task(system, paper_bytes("This is a text-only document without numbered mathematics."))
    run(system, task["id"])
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()["status"] == "waiting_review"
    resolve_all(system, task["id"])
    run(system, task["id"])
    result = client.get(f"/api/v1/tasks/{task['id']}/results", headers=headers()).json()
    assert result["equations"] == []
    assert result["review_items"][0]["decision"]["action"] == "keep_unknown"
