"""Real MCP stdio client with a fixed local command and explicit project scope."""
from __future__ import annotations

from contextlib import AsyncExitStack
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ..chat_output import payload

ALLOWED = frozenset({"list_applications", "list_analyses", "get_complete_discovery", "get_discovery_section",
                     "get_semantic_context", "search_findings", "get_evidence", "get_sql_lineage", "list_sql_queries"})


class StdioTools:
    def __init__(self, state: Path, project: str, roots=()):
        self.state, self.project, self.roots = state, project, roots

    async def __aenter__(self):
        self.stack = AsyncExitStack()
        args = ["-m", "diamond_mcp", "--state", str(self.state), "--project", self.project]
        for root in self.roots:
            args += ["--root", str(root)]
        args += ["serve", "--transport", "stdio"]
        env = {**os.environ, "ORIGINSPAN_ALLOW_GITHUB": "false", "DIAMOND_ALLOW_GITHUB": "false",
               "ORIGINSPAN_DATABASE_URL": "sqlite:///" + (self.state / "state.sqlite").as_posix(),
               "DIAMOND_DATABASE_URL": "sqlite:///" + (self.state / "state.sqlite").as_posix(),
               "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
        try:
            read, write = await self.stack.enter_async_context(stdio_client(StdioServerParameters(
                command=sys.executable, args=args, env=env)))
            self.session = await self.stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *_):
        await self.stack.aclose()

    async def call(self, tool, arguments):
        if tool not in ALLOWED or arguments.get("project_id") != self.project:
            raise PermissionError("Offline controller denied this tool or project")
        result = await self.session.call_tool(tool, arguments)
        envelope = payload(result)
        if envelope.get("status") != "OK":
            raise ValueError("MCP request failed: " + str(envelope.get("code", "UNAVAILABLE")))
        return dict(data=envelope["data"], text="\n".join(c.text for c in result.content if getattr(c, "type", "") == "text"))
