"""Reusable agent-runtime primitives shared by every seleric-mcp agent surface.

Historically these lived only in ``scripts/chat_client.py`` (terminal REPL) and
``scripts/chat_web.py`` (single-session test page). The production dashboard
analyst (``gateway/analyst.py``) needs the SAME machinery — durable per-turn
memory (Scratchpad), tool-call JSON hardening (sanitizers), the OpenAI tool
schema shape, and the task-tier ``LLMRouter`` — so it lives here in the package
where any surface can import it, instead of being re-implemented in Node.

Nothing here talks to a specific transport: callers pass in the tool list and
execute tool calls however they like (in-process for the HTTP service, over an
MCP session for the scripts).
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from openai import AzureOpenAI

from ..config import load_azure_settings, load_llm_settings
from . import LLMRouter, RateLimit


class Scratchpad:
    """Durable per-conversation memory, re-injected into context every round.

    Same contract as the chat clients: the model writes short, reusable facts
    (resolved term -> metric id, chosen period, active filters, latest
    query_id) via the ``scratchpad_write`` tool and they survive across turns.
    """

    def __init__(self, notes: dict[str, str] | None = None) -> None:
        self.notes: dict[str, str] = dict(notes or {})

    def write(self, key: str, value: str) -> str:
        key = (key or "").strip()
        if not key:
            return "ignored: empty key"
        if value is None or str(value).strip() == "":
            self.notes.pop(key, None)
            return f"deleted '{key}'"
        self.notes[key] = str(value).strip()
        return f"saved '{key}'"

    def render(self) -> str:
        if not self.notes:
            return "SCRATCHPAD (conversation memory): empty"
        lines = [f"- {k}: {v}" for k, v in self.notes.items()]
        return "SCRATCHPAD (conversation memory):\n" + "\n".join(lines)


SCRATCHPAD_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "scratchpad_write",
        "description": (
            "Save a durable fact/decision for this conversation (term "
            "resolutions, chosen default periods, active filters, useful "
            "query_ids). Overwrites the key. Empty value deletes the key. "
            "Handled locally — never sent to the MCP server."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Short stable key, e.g. 'default_period' or 'term:net profit'",
                },
                "value": {
                    "type": "string",
                    "description": "The fact to remember; empty string deletes",
                },
            },
            "required": ["key", "value"],
        },
    },
}


def sanitize_tool_arguments(raw: str | None) -> str:
    """Return a guaranteed-valid JSON object string for an assistant tool call.

    Some tool-tier models emit '' or malformed/truncated JSON for
    ``function.arguments``. Echoing that back to the chat API is rejected with
    400 'Assistant tool call function.arguments must be valid JSON', and because
    the bad message stays in history it poisons every later turn. Coerce
    anything unparseable (or non-object) to '{}'.
    """
    if not raw or not raw.strip():
        return "{}"
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return "{}"
    return json.dumps(parsed) if isinstance(parsed, dict) else "{}"


def sanitize_history(messages: list[dict]) -> None:
    """In-place repair of any assistant tool_call arguments that aren't valid
    JSON — heals conversations poisoned before this fix so a stuck session
    recovers on the next turn instead of 400-ing forever."""
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function")
                if isinstance(fn, dict):
                    fn["arguments"] = sanitize_tool_arguments(fn.get("arguments"))


def mcp_tools_to_openai(tools: Iterable[Any]) -> list[dict[str, Any]]:
    """Convert MCP tool descriptors (``.name`` / ``.description`` /
    ``.inputSchema``) to the OpenAI chat-completions function-tool shape."""
    out: list[dict[str, Any]] = []
    for t in tools:
        schema = getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
        if not schema.get("type"):
            schema = {**schema, "type": "object"}
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": (getattr(t, "description", "") or "")[:1024],
                    "parameters": schema,
                },
            }
        )
    return out


def load_azure_client() -> tuple[AzureOpenAI, str]:
    """Build the AzureOpenAI client + default deployment from config/.env.

    Raises RuntimeError (not SystemExit — this runs inside a web server) when a
    required Azure setting is missing.
    """
    azure = load_azure_settings()
    missing = [
        name
        for name, val in [
            ("AZURE_OPENAI_API_KEY", azure.api_key),
            ("azure.endpoint (config.yaml or AZURE_OPENAI_ENDPOINT)", azure.endpoint),
            ("azure.deployment (config.yaml or AZURE_DEPLOYMENT)", azure.deployment),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(f"Missing required Azure settings: {', '.join(missing)}")
    client = AzureOpenAI(
        api_key=azure.api_key,
        azure_endpoint=azure.endpoint,
        api_version=azure.api_version,
    )
    return client, azure.deployment


def build_router(client: AzureOpenAI) -> LLMRouter:
    """Build the task-tier router with per-model rate limiting from config.yaml
    (``llm`` section). Falls back to the single azure.deployment when no tiers
    are configured — same behaviour the chat clients rely on."""
    llm = load_llm_settings()
    return LLMRouter(
        client,
        tiers=llm.tiers,
        fallback=llm.fallback,
        default_limit=RateLimit(llm.requests_per_minute, llm.tokens_per_minute),
        model_limits={
            name: RateLimit(rpm, tpm) for name, (rpm, tpm) in llm.model_limits.items()
        },
        completion_reserve=llm.completion_reserve,
        max_wait_rounds=llm.max_wait_rounds,
        max_sleep_seconds=llm.max_sleep_seconds,
    )
