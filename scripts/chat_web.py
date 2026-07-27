"""Browser chat for testing seleric-mcp — same agent loop as chat_client.py
(Azure OpenAI + MCP tools over stdio + scratchpad + agent policy), with live
tool-call streaming to the page.

Run from Base_Agent:

    uv run python scripts/chat_web.py          # http://127.0.0.1:8766 (auto-reload)
    CHAT_WEB_RELOAD=0 uv run python scripts/chat_web.py
    CHAT_WEB_PORT=9000 uv run python scripts/chat_web.py

Port / preview size: config.yaml → chat (env overrides still work).
Auto-reloads on src/catalogue/scripts/config changes (CHAT_WEB_RELOAD=0 to disable).
Single-session by design (testing tool): one MCP connection, one conversation,
one scratchpad. POST /reset starts over.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

import chat_client  # noqa: E402  (reuses _load_azure, Scratchpad, policy, helpers)
from seleric_mcp.config import load_chat_settings  # noqa: E402
from seleric_mcp.gateway.prompts import NO_HALLUCINATION_GUARD  # noqa: E402

UI_PATH = Path(__file__).with_name("chat_web_ui.html")
_CHAT = load_chat_settings()
PORT = _CHAT.web_port
MAX_TOOL_ROUNDS = chat_client.MAX_TOOL_ROUNDS
TOOL_RESULT_PREVIEW_CHARS = _CHAT.tool_preview_chars


class AgentRuntime:
    """One MCP session + one conversation, shared by all page requests."""

    def __init__(self) -> None:
        self.llm, self.deployment = chat_client._load_azure()
        self.router = chat_client._build_router(self.llm)
        self.scratchpad = chat_client.Scratchpad()
        self.session: ClientSession | None = None
        self.openai_tools: list[dict] = []
        self.messages: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()
        self._stack = None

    def _fresh_messages(self) -> list[dict[str, Any]]:
        return [
            {"role": "system",
             "content": NO_HALLUCINATION_GUARD + "\n" + chat_client._load_agent_policy()},
            {"role": "system", "content": self.scratchpad.render()},
        ]

    async def start(self) -> None:
        from contextlib import AsyncExitStack

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "seleric_mcp", "--transport", "stdio"],
            cwd=str(ROOT),
            env={**os.environ},
        )
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        listed = await self.session.list_tools()
        self.openai_tools = chat_client._mcp_tools_to_openai(listed.tools) + [
            chat_client.SCRATCHPAD_TOOL
        ]
        self.messages = self._fresh_messages()

    async def stop(self) -> None:
        if self._stack:
            await self._stack.aclose()

    def reset(self) -> None:
        self.scratchpad = chat_client.Scratchpad()
        self.messages = self._fresh_messages()

    async def run_turn(self, user_text: str):
        """Async generator of event dicts for one user turn."""
        self.messages.append({"role": "user", "content": user_text})

        # Tool models drive the loop; the final synthesis escalates to a
        # reasoning model (see chat_client._chat_loop for the same policy).
        tier = "tools"
        for _ in range(MAX_TOOL_ROUNDS):
            self.messages[1] = {"role": "system", "content": self.scratchpad.render()}
            try:
                resp = await asyncio.to_thread(
                    self.router.complete,
                    tier=tier,
                    messages=self.messages,
                    tools=self.openai_tools,
                    tool_choice="auto",
                )
            except Exception as exc:
                yield {"type": "error", "text": f"LLM error: {exc}"}
                return

            choice = resp.message
            tool_calls = choice.tool_calls or []

            # Tool model is ready to answer -> redo synthesis with a reasoning
            # model instead of committing the tentative answer.
            if not tool_calls and tier == "tools":
                tier = "reasoning"
                continue

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": choice.content or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name,
                                  "arguments": tc.function.arguments or "{}"}}
                    for tc in tool_calls
                ]
            self.messages.append(assistant_msg)

            if not tool_calls:
                yield {"type": "assistant", "model": resp.model,
                       "text": (choice.content or "").strip() or "(empty)"}
                yield {"type": "done", "scratchpad": self.scratchpad.notes}
                return

            tier = "tools"

            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield {"type": "tool_call", "tool": name, "args": args, "model": resp.model}
                if name == "scratchpad_write":
                    payload = self.scratchpad.write(args.get("key", ""), args.get("value", ""))
                else:
                    try:
                        result = await self.session.call_tool(name, args)
                        payload = chat_client._tool_result_text(result)
                    except Exception as exc:
                        payload = json.dumps({"error": f"tool call failed: {exc}"})
                yield {
                    "type": "tool_result",
                    "tool": name,
                    "preview": payload[:TOOL_RESULT_PREVIEW_CHARS],
                    "truncated": len(payload) > TOOL_RESULT_PREVIEW_CHARS,
                }
                self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": payload})

        yield {"type": "error", "text": f"stopped after {MAX_TOOL_ROUNDS} tool rounds"}
        yield {"type": "done", "scratchpad": self.scratchpad.notes}


runtime = AgentRuntime()


async def index(request: Request):
    return FileResponse(UI_PATH, media_type="text/html")


async def chat(request: Request):
    body = await request.json()
    text = (body.get("message") or "").strip()
    if not text:
        return JSONResponse({"error": "empty message"}, status_code=400)

    async def stream():
        async with runtime.lock:  # single conversation — serialize turns
            async for event in runtime.run_turn(text):
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


async def reset(request: Request):
    async with runtime.lock:
        runtime.reset()
    return JSONResponse({"ok": True})


async def state(request: Request):
    return JSONResponse({
        "models": {
            "tools": runtime.router.candidates("tools"),
            "reasoning": runtime.router.candidates("reasoning"),
        },
        "tools": [t["function"]["name"] for t in runtime.openai_tools],
        "scratchpad": runtime.scratchpad.notes,
        "turns": sum(1 for m in runtime.messages if m.get("role") == "user"),
    })


from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    await runtime.start()
    yield
    await runtime.stop()

app = Starlette(
    routes=[
        Route("/", index),
        Route("/chat", chat, methods=["POST"]),
        Route("/reset", reset, methods=["POST"]),
        Route("/state", state),
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    reload = os.environ.get("CHAT_WEB_RELOAD", "1") != "0"
    print(f"seleric-mcp test chat -> http://127.0.0.1:{PORT}"
          + (" (reload on)" if reload else ""))
    # Import string required for uvicorn --reload (re-spawns worker on change).
    uvicorn.run(
        "chat_web:app",
        app_dir=str(Path(__file__).parent),
        host="127.0.0.1",
        port=PORT,
        log_level="info" if reload else "warning",
        reload=reload,
        reload_dirs=[
            str(ROOT / "src"),
            str(ROOT / "catalogue"),
            str(ROOT / "scripts"),
            str(ROOT),
        ],
        reload_excludes=[
            ".venv",
            "var",
            "logs",
            ".pytest_cache",
            ".git",
            "**/__pycache__",
        ],
        reload_includes=[
            "*.py",
            "*.yaml",
            "*.yml",
            "*.html",
            "*.md",
            "config.yaml",
            ".env",
            ".env.local",
        ],
    )

