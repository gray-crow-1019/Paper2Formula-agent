import asyncio
import json
import os
import sys

from packages.domain.config import ROOT
from packages.domain.errors import DomainError

TOOL_NAMES = {"inspect": "inspect_document", "normalize": "normalize_document",
              "parse": "extract_document_blocks", "recognize": "recognize_equations",
              "resolve": "find_symbol_mentions", "relate": "build_equation_relations",
              "validate": "validate_equation", "export": "export_bundle"}


def call_mcp(settings, task_id: str, node: str, owner: str) -> dict:
    """Run a task-scoped, local stdio server. The authenticated owner is never model-supplied."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def call():
        effective_config = {name.upper(): str(value).lower() if isinstance(value, bool) else str(value)
                            for name, value in settings.model_dump().items()}
        env = {**os.environ, **effective_config, "PAPER_MCP_OWNER_ID": owner, "PAPER_MCP_TASK_ID": task_id,
               "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
        params = StdioServerParameters(command=sys.executable, args=["-m", "packages.paper_tools.server"],
                                       env=env, cwd=str(ROOT))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool(TOOL_NAMES[node], {"task_id": task_id})
        # Interpret business errors after closing TaskGroups, avoiding nested ExceptionGroups.
        payload = response.structuredContent
        if payload is None:
            payload = json.loads(next(content.text for content in response.content if content.type == "text"))
        if response.isError or "error" in payload:
            error = payload.get("error", {})
            raise DomainError(error.get("code", "TOOL_FAILED"), error.get("message", "MCP 工具失败"),
                              retryable=error.get("retryable", False))
        return payload
    return asyncio.run(asyncio.wait_for(call(), timeout=settings.task_timeout))
