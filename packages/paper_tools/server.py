"""Task-scoped MCP stdio tools. Paths and owner identity are resolved on the server."""
import json
import os

from mcp import types
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from packages.domain.config import get_settings
from packages.domain.database import Database
from packages.domain.errors import DomainError
from packages.domain.models import NodeOutput, Task
from packages.domain.service import current_result, owned_document, owned_task
from packages.domain.storage import Storage
from packages.workflow.operations import execute_operation

settings = get_settings()
database = Database(settings)
storage = Storage(settings)
server = FastMCP("Paper2Formula Paper Tools")


def trusted_owner():
    owner = os.environ.get("PAPER_MCP_OWNER_ID")
    if not owner and settings.auth_mode == "development":
        owner = settings.dev_owner_id
    if not owner:
        raise DomainError("UNAUTHENTICATED", "MCP 进程缺少可信身份上下文", 401)
    return owner


def run_operation(task_id: str, node: str):
    try:
        scope = os.environ.get("PAPER_MCP_TASK_ID")
        if scope and task_id != scope:
            raise DomainError("NOT_FOUND", "任务不存在", 404)
        return execute_operation(database, storage, settings, task_id, node, trusted_owner())
    except Exception as exc:
        code = getattr(exc, "code", "TOOL_FAILED")
        payload = {"error": {"code": code, "message": str(exc) if hasattr(exc, "code") else "工具执行失败",
                             "retryable": getattr(exc, "retryable", False)}}
        return types.CallToolResult(isError=True, structuredContent=payload,
            content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))])


@server.tool()
def inspect_document(task_id: str) -> dict:
    """Inspect the original document belonging to an authorized task."""
    return run_operation(task_id, "inspect")


@server.tool()
def normalize_document(task_id: str) -> dict:
    """Create a normalized PDF without changing the original document."""
    return run_operation(task_id, "normalize")


@server.tool()
def extract_document_blocks(task_id: str) -> dict:
    """Extract bounded text blocks and normalized source anchors."""
    return run_operation(task_id, "parse")


@server.tool()
def recognize_equations(task_id: str) -> dict:
    """Recognize numbered equation candidates and retain source images."""
    return run_operation(task_id, "recognize")


@server.tool()
def find_symbol_mentions(task_id: str) -> dict:
    """Resolve symbol candidates using nearby actual source anchors."""
    return run_operation(task_id, "resolve")


@server.tool()
def build_equation_relations(task_id: str) -> dict:
    """Find explicitly stated equation references."""
    return run_operation(task_id, "relate")


@server.tool()
def validate_equation(task_id: str) -> dict:
    """Validate syntax, definitions, evidence, and supported unit expressions."""
    return run_operation(task_id, "validate")


@server.tool()
def export_bundle(task_id: str) -> dict:
    """Create three temporary artifacts; only the coordinator publishes the manifest."""
    return run_operation(task_id, "export")


@server.tool()
def create_annotated_pdf(task_id: str) -> dict:
    """Produce an annotated PDF as part of a coherent export bundle."""
    output = run_operation(task_id, "export")
    return output if isinstance(output, types.CallToolResult) else output["artifacts"]["annotated_pdf"]


@server.tool()
def export_markdown_report(task_id: str) -> dict:
    """Produce the source-linked report from the same result version."""
    output = run_operation(task_id, "export")
    return output if isinstance(output, types.CallToolResult) else output["artifacts"]["markdown"]


@server.tool()
def export_structured_json(task_id: str) -> dict:
    """Produce structured JSON from the same result version."""
    output = run_operation(task_id, "export")
    return output if isinstance(output, types.CallToolResult) else output["artifacts"]["json"]


@server.tool()
def get_source_anchor(task_id: str, anchor_id: str, result_version: int) -> dict:
    """Read an authorized, explicitly versioned source location."""
    with database.session() as session:
        scope = os.environ.get("PAPER_MCP_TASK_ID")
        if scope and task_id != scope:
            raise ValueError("NOT_FOUND")
        task = owned_task(session, task_id, trusted_owner())
        data = current_result(session, task, result_version).data
        for anchor in data["anchors"]:
            if anchor["id"] == anchor_id:
                return anchor
    raise ValueError("NOT_FOUND")


def resource_data(document_id: str, version: int, field: str):
    with database.session() as session:
        owner = trusted_owner()
        owned_document(session, document_id, owner)
        tasks = list(session.scalars(select(Task).where(Task.document_id == document_id, Task.owner_id == owner)
                                    .order_by(Task.created_at.desc())))
        scope = os.environ.get("PAPER_MCP_TASK_ID")
        if scope:
            tasks = [task for task in tasks if task.id == scope]
        for task in tasks:
            try:
                data = current_result(session, task, version).data
            except DomainError:
                continue
            if field == "symbols":
                return json.dumps([symbol for eq in data["equations"] for symbol in eq["symbols"]], ensure_ascii=False)
            if field == "sections":
                parsed = session.scalar(select(NodeOutput).where(NodeOutput.task_id == task.id,
                                         NodeOutput.node == "parse"))
                return json.dumps({"document_id": document_id, "task_id": task.id, "version": version,
                                   "sections": parsed.output.get("sections", []) if parsed else []}, ensure_ascii=False)
            sanitized = [{key: value for key, value in eq.items() if key != "evidence_image"}
                         for eq in data["equations"]]
            return json.dumps(sanitized, ensure_ascii=False)
    raise ValueError("NOT_FOUND")


@server.resource("paper://{document_id}/equations?version={version}")
def equations_resource(document_id: str, version: int) -> str:
    return resource_data(document_id, version, "equations")


@server.resource("paper://{document_id}/symbols?version={version}")
def symbols_resource(document_id: str, version: int) -> str:
    return resource_data(document_id, version, "symbols")


@server.resource("paper://{document_id}/sections?version={version}")
def sections_resource(document_id: str, version: int) -> str:
    return resource_data(document_id, version, "sections")


if __name__ == "__main__":
    if settings.mcp_transport != "stdio":
        raise SystemExit("首版 MCP 仅支持受控本地 stdio")
    trusted_owner()
    database.initialize()
    server.run(transport="stdio")
