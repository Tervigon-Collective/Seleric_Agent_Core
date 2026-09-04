"""Production dashboard BI-analyst service — the single agent brain for the
dashboard's in-page chat.

This is the server-side home of what the Node backend used to re-implement: a
multi-turn, module- and brand-scoped tool-calling agent that answers business
questions using ONLY the seleric-mcp analytics tools. It reuses the exact
runtime the terminal/test chat clients use — the task-tier ``LLMRouter``
(fast tool models drive the loop, a reasoning model does the final synthesis),
the durable ``Scratchpad`` conversation memory, and the tool-argument
sanitizers — so behaviour is consistent everywhere and lives here in Base_Agent,
not in Node.

Sessions are kept in-process keyed by an opaque ``session_id`` the dashboard
supplies (one per open chat panel). Each session owns its message history and
its scratchpad, so follow-ups like "break that down by day" build on the prior
turn's resolved metric/period without re-asking. Scope (module + brand) is
injected server-side into every data-tool call, so the model cannot widen it.

Tools run IN-PROCESS against the already-built FastMCP tool manager — no MCP
round-trip, no extra process — and are restricted to the read-only analytics
subset (Phase 1). Actions remain a deliberate, separately-gated phase 2.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import OrderedDict
from typing import Any

import structlog

from ..llm.agent_core import (
    SCRATCHPAD_TOOL,
    Scratchpad,
    build_router,
    load_azure_client,
    mcp_tools_to_openai,
    sanitize_history,
    sanitize_tool_arguments,
)
from . import prompts as prompt_templates

logger = structlog.get_logger()

# Read-only analytics tools the analyst may use. Anything else the MCP exposes
# (ads mutations, action commit/propose, google_*/meta_* writes) is NOT offered
# — this keeps the dashboard chat strictly read-only (Phase 1).
ANALYST_TOOLS = frozenset(
    {
        "modules_list",
        "catalogue_search_metrics",
        "catalogue_get_metric",
        "catalogue_get_ontology",
        "catalogue_related_metrics",
        "catalogue_list_dimensions",
        "catalogue_resolve_term",
        "catalogue_resolve_brand",
        "catalogue_list_brands",
        "metrics_query",
        "metrics_drilldown",
        "insights_explain",
    }
)

# Tools whose ``module`` argument the server forces to the page's module.
MODULE_SCOPED_TOOLS = frozenset(
    {
        "metrics_query",
        "metrics_drilldown",
        "catalogue_search_metrics",
        "catalogue_get_ontology",
        "catalogue_get_metric",
        "catalogue_related_metrics",
    }
)


def _analyst_env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Scope injection (module + brand) — mirrors the enforcement the tools already
# do, but applied here so the model never has to (and can't avoid it).
# ---------------------------------------------------------------------------


def _flag_repeated_failure(
    payload: str, fail_counts: dict[str, int], tool_name: str, args: dict[str, Any]
) -> str:
    """Track failures per (tool, args) signature across the whole turn and, once
    the identical call has failed twice, inject a hint into the error payload
    telling the model to stop retrying it. Without this a bad metric/tool call
    (e.g. a spend query with no ad platform connected) can burn most of
    max_rounds retrying the exact same call before ever reaching an answer.
    Mutates ``fail_counts``; returns the (possibly annotated) payload."""
    sig = f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
    fail_counts[sig] = fail_counts.get(sig, 0) + 1
    if fail_counts[sig] < 2:
        return payload
    try:
        err_obj = json.loads(payload)
    except json.JSONDecodeError:
        err_obj = {"error": payload}
    err_obj["hint"] = (
        "This exact call has now failed repeatedly. Do not retry it again — answer "
        "with whatever data you already have, or tell the user this specific "
        "metric/data is unavailable."
    )
    return json.dumps(err_obj, default=str)


def _ensure_brand_filter(filters: Any, brand_id: str) -> list[dict]:
    out = list(filters) if isinstance(filters, list) else []
    if not any(isinstance(f, dict) and f.get("dimension") == "brand_id" for f in out):
        out.append({"dimension": "brand_id", "operator": "equals", "values": [str(brand_id)]})
    return out


def inject_scope(name: str, args: dict[str, Any], *, module: str | None, brand_id: str | None) -> dict[str, Any]:
    """Force module + brand scope onto a tool call, ignoring anything the model
    set. Returns a new args dict."""
    a = dict(args or {})
    if module and name in MODULE_SCOPED_TOOLS:
        a["module"] = module
    if brand_id not in (None, ""):
        if name == "metrics_query":
            a["filters"] = _ensure_brand_filter(a.get("filters"), brand_id)
        elif name == "metrics_drilldown":
            a["additional_filters"] = _ensure_brand_filter(a.get("additional_filters"), brand_id)
    return a


# ---------------------------------------------------------------------------
# Rich-block parsing — the persona (prompts.py CONVERSATIONAL_BI_ANALYST) emits
# fenced ```chart / ```table / ```choices JSON blocks the dashboard renders as
# real UI. We strip + validate them here so the raw JSON never reaches the
# chat, and Node stays a dumb relay. Ported from the old Node agent.js.
# ---------------------------------------------------------------------------

_TABLE_FORMATS = frozenset({"inr", "int", "pct", "num"})
_CHART_TYPES = frozenset({"bar", "line", "area", "pie"})


def _fenced_json(answer: str, tag: str) -> tuple[Any, str]:
    """Parse the first ```<tag> ... ``` fenced JSON block. Returns (data|None,
    stripped) — the block is removed from ``stripped`` whether or not it parsed,
    so a malformed block never renders."""
    m = re.search(r"```" + tag + r"\s*([\s\S]*?)```", answer, re.IGNORECASE)
    if not m:
        return None, answer
    try:
        data = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        data = None
    return data, (answer[: m.start()] + answer[m.end():]).strip()


def _extract_choices(answer: str) -> tuple[str, list[dict] | None]:
    data, stripped = _fenced_json(answer, "choices")
    opts = data if isinstance(data, list) else (data.get("options") if isinstance(data, dict) else None)
    if not isinstance(opts, list):
        return stripped, None
    clean = []
    for o in opts:
        if not isinstance(o, dict):
            continue
        label = o.get("label").strip() if isinstance(o.get("label"), str) else ""
        value = o.get("value").strip() if isinstance(o.get("value"), str) and o.get("value").strip() else label
        if label and value:
            clean.append({"label": label, "value": value})
    return stripped, (clean[:4] or None)


def _extract_table(answer: str) -> tuple[str, dict | None]:
    data, stripped = _fenced_json(answer, "table")
    if not (isinstance(data, dict) and isinstance(data.get("columns"), list) and data.get("rows")):
        return stripped, None
    columns = []
    for c in data["columns"]:
        if not (isinstance(c, dict) and isinstance(c.get("key"), str) and c["key"]):
            continue
        col = {"key": c["key"], "label": c["key"]}
        if isinstance(c.get("label"), str) and c["label"]:
            col["label"] = c["label"]
        if c.get("format") in _TABLE_FORMATS:
            col["format"] = c["format"]
        if c.get("align") in ("right", "center"):
            col["align"] = c["align"]
        if c.get("muted") is True:
            col["muted"] = True
        columns.append(col)
    if not columns:
        return stripped, None
    keys = {c["key"] for c in columns}
    compare = {}
    if isinstance(data.get("compare"), dict):
        for k, v in data["compare"].items():
            if k in keys and isinstance(v, str) and v in keys:
                compare[k] = v
    table: dict[str, Any] = {
        "columns": columns,
        "rows": [r for r in data["rows"] if isinstance(r, dict)],
    }
    if isinstance(data.get("title"), str):
        table["title"] = data["title"]
    if data.get("primary") in keys:
        table["primary"] = data["primary"]
    if compare:
        table["compare"] = compare
    return stripped, table


def _extract_chart(answer: str) -> tuple[str, dict | None]:
    data, stripped = _fenced_json(answer, "chart")
    if not (
        isinstance(data, dict)
        and data.get("type") in _CHART_TYPES
        and isinstance(data.get("x"), str)
        and data.get("x")
        and isinstance(data.get("series"), list)
        and data.get("rows")
    ):
        return stripped, None
    series = []
    for s in data["series"]:
        if not (isinstance(s, dict) and isinstance(s.get("key"), str) and s["key"]):
            continue
        entry = {"key": s["key"], "label": s["key"]}
        if isinstance(s.get("label"), str) and s["label"]:
            entry["label"] = s["label"]
        if s.get("format") in _TABLE_FORMATS:
            entry["format"] = s["format"]
        series.append(entry)
    if not series:
        return stripped, None
    chart: dict[str, Any] = {
        "type": data["type"],
        "x": data["x"],
        "series": series,
        "rows": [r for r in data["rows"] if isinstance(r, dict)],
    }
    if isinstance(data.get("title"), str):
        chart["title"] = data["title"]
    if data.get("format") in _TABLE_FORMATS:
        chart["format"] = data["format"]
    if data.get("stacked") is True:
        chart["stacked"] = True
    return stripped, chart


def parse_rich_blocks(raw: str) -> dict[str, Any]:
    """Strip chart -> table -> choices blocks (same order Node used) and return
    the cleaned answer plus whichever blocks were present."""
    answer, chart = _extract_chart(raw or "")
    answer, table = _extract_table(answer)
    answer, choices = _extract_choices(answer)
    out: dict[str, Any] = {"answer": answer}
    if chart:
        out["chart"] = chart
    if table:
        out["table"] = table
    if choices:
        out["choices"] = choices
    return out


def _step_label(name: str, args: dict[str, Any]) -> str:
    a = args or {}
    if name in ("metrics_query", "metrics_drilldown"):
        detail = ", ".join(a.get("measures") or []) or ", ".join(a.get("target_dimensions") or [])
        return f"{name}: {detail}" if detail else name
    if name in ("catalogue_search_metrics", "catalogue_resolve_term"):
        q = a.get("query") or a.get("text")
        return f"{name}: {q}" if q else name
    if name == "catalogue_get_metric" and a.get("metric_id"):
        return f"{name}: {a['metric_id']}"
    return name


class _FenceStripper:
    """Incrementally strips fenced ```...``` blocks out of a STREAMED answer so
    the persona's raw ```chart/```table/```choices JSON never types out to the
    user as text — those blocks are parsed and rendered separately at the end
    (parse_rich_blocks on the full text). Emits only prose that is definitively
    OUTSIDE a fence, holding back a trailing run of up to two backticks in case
    a ``` marker is split across streaming chunks."""

    def __init__(self) -> None:
        self._buf = ""
        self._in_fence = False

    def feed(self, delta: str) -> str:
        self._buf += delta
        out: list[str] = []
        while True:
            idx = self._buf.find("```")
            if idx == -1:
                # No complete marker yet. Emit everything except a trailing
                # partial backtick run (<=2) that might begin a fence next chunk.
                hold = 0
                while hold < 2 and hold < len(self._buf) and self._buf[-1 - hold] == "`":
                    hold += 1
                cut = len(self._buf) - hold
                chunk, self._buf = self._buf[:cut], self._buf[cut:]
                if chunk and not self._in_fence:
                    out.append(chunk)
                break
            before = self._buf[:idx]
            if before and not self._in_fence:
                out.append(before)
            self._in_fence = not self._in_fence  # the ``` toggles fence state
            self._buf = self._buf[idx + 3:]
        return "".join(out)

    def flush(self) -> str:
        """Stream ended — emit any trailing prose left outside a fence."""
        tail, self._buf = self._buf, ""
        return tail if (tail and not self._in_fence) else ""


# ---------------------------------------------------------------------------
# Session + service
# ---------------------------------------------------------------------------


class AnalystSession:
    """One dashboard chat panel: message history + scratchpad + fixed scope."""

    def __init__(self, system_prompt: str, module: str | None, brand_id: str | None) -> None:
        self.module = module
        self.brand_id = brand_id
        self.scratchpad = Scratchpad()
        # messages[0] = system prompt (persona + scope), messages[1] = scratchpad
        # slot refreshed before every model call. Rest is the conversation.
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": self.scratchpad.render()},
        ]
        self.last_used = time.monotonic()

    def turns(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user")


class AnalystService:
    """Multi-session BI agent bound to one FastMCP server (in-process tools)."""

    def __init__(self, mcp: Any) -> None:
        self._mcp = mcp
        self._ctx = getattr(mcp, "_seleric_ctx", None)
        self._client, self._deployment = load_azure_client()
        self._router = build_router(self._client)
        self._tools: list[dict[str, Any]] | None = None  # lazy (list_tools is async)
        self._sessions: OrderedDict[str, AnalystSession] = OrderedDict()
        self._lock = asyncio.Lock()
        self.max_rounds = _analyst_env_int("ANALYST_MAX_ROUNDS", 40)
        self.session_ttl = _analyst_env_int("ANALYST_SESSION_TTL_SECONDS", 3600)
        self.max_sessions = _analyst_env_int("ANALYST_MAX_SESSIONS", 500)

    async def _openai_tools(self) -> list[dict[str, Any]]:
        if self._tools is None:
            listed = await self._mcp.list_tools()
            allowed = [t for t in listed if t.name in ANALYST_TOOLS]
            self._tools = mcp_tools_to_openai(allowed) + [SCRATCHPAD_TOOL]
        return self._tools

    def _system_prompt(self, module: str | None, brand_id: str | None) -> str:
        module_label = module or ""
        brand_label = ""
        if self._ctx is not None:
            if module:
                mod = self._ctx.catalogue.get_module(module)
                module_label = mod.display_name if mod is not None else module
            if brand_id:
                resolved = self._ctx.catalogue.resolve_brand(str(brand_id))
                brand_label = getattr(resolved, "name", None) or str(brand_id)
        persona = prompt_templates.dashboard_analyst_prompt(
            module_label=module_label, brand_label=brand_label
        )
        return persona + "\n\n" + prompt_templates.SCRATCHPAD_USAGE

    def _evict(self) -> None:
        now = time.monotonic()
        stale = [sid for sid, s in self._sessions.items() if now - s.last_used > self.session_ttl]
        for sid in stale:
            self._sessions.pop(sid, None)
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)  # drop least-recently-used

    def _session(self, session_id: str, module: str | None, brand_id: str | None) -> AnalystSession:
        s = self._sessions.get(session_id)
        # Scope is fixed per session; if the dashboard reuses an id under a new
        # module/brand, start fresh so the wrong scope can't leak across.
        if s is None or s.module != module or s.brand_id != brand_id:
            s = AnalystSession(self._system_prompt(module, brand_id), module, brand_id)
            self._sessions[session_id] = s
        self._sessions.move_to_end(session_id)
        return s

    async def _run_tool(self, name: str, args: dict[str, Any], session: AnalystSession) -> str:
        if name == "scratchpad_write":
            return session.scratchpad.write(args.get("key", ""), args.get("value", ""))
        tool = self._mcp._tool_manager.get_tool(name)
        if tool is None:
            return json.dumps({"error": f"unknown tool: {name}"})
        try:
            result = await tool.run(args)  # convert_result=False -> raw dict
        except Exception as exc:  # noqa: BLE001 — relay tool failure to the model
            # Many failures here (Cube read-timeouts, asyncio TimeoutError) stringify
            # to "" — which used to surface as a blank "tool call failed:" with no
            # server trace. Always keep the type + a repr fallback, and log the
            # full traceback so the real cause is diagnosable.
            logger.error("analyst_tool_call_failed", tool=name, error=repr(exc), exc_info=True)
            detail = str(exc).strip() or repr(exc)
            return json.dumps({"error": f"tool call failed: {type(exc).__name__}: {detail}"})
        if isinstance(result, (dict, list)):
            return json.dumps(result, default=str)
        return str(result)

    async def _stream_final(
        self, session: "AnalystSession", *, session_id: str,
        module: str | None, steps: list[dict[str, Any]], t0: float,
    ):
        """Stream the reasoning-tier final synthesis: emit ``{"type":"token"}``
        events (fence-stripped prose) as the model generates the answer, then a
        single authoritative ``{"type":"final", ...}`` with the parsed answer +
        rich blocks. The router streams synchronously, so a worker thread pumps
        its deltas into an asyncio queue this coroutine drains."""
        session.messages[1] = {"role": "system", "content": session.scratchpad.render()}
        sanitize_history(session.messages)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()

        def pump() -> None:
            try:
                for piece in self._router.complete_stream(
                    tier="reasoning", messages=session.messages
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, ("delta", piece))
            except Exception as exc:  # noqa: BLE001 — relay to the async side
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        fut = loop.run_in_executor(None, pump)
        stripper = _FenceStripper()
        parts: list[str] = []
        error: Exception | None = None
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            if item[0] == "delta":
                parts.append(item[1])
                safe = stripper.feed(item[1])
                if safe:
                    yield {"type": "token", "delta": safe}
            elif item[0] == "error":
                error = item[1]
        await fut

        if error is not None:
            logger.error("analyst_stream_synth_error", module=module, error=str(error))
            yield {"type": "error", "error": f"LLM error: {error}", "status": 502,
                   "elapsed_ms": int((time.monotonic() - t0) * 1000)}
            return

        tail = stripper.flush()
        if tail:
            yield {"type": "token", "delta": tail}
        full = "".join(parts)
        session.messages.append({"role": "assistant", "content": full})
        parsed = parse_rich_blocks(full.strip())
        yield {"type": "final", "module": module, "steps": steps,
               "session_id": session_id,
               "elapsed_ms": int((time.monotonic() - t0) * 1000), **parsed}

    async def _run_turn(
        self, session: "AnalystSession", *, session_id: str, question: str,
        module: str | None, brand_id: str | None,
    ):
        """The agent loop as an async generator. Yields progress events —
        ``{"type": "step", ...}`` as each tool call completes, then exactly one
        terminal ``{"type": "final", ...}`` (parsed answer + rich blocks) or
        ``{"type": "error", "error": ..., "status": ...}``. Both ``ask`` (buffered)
        and ``ask_stream`` (SSE) drive this so behaviour is identical. Caller
        holds ``self._lock``.
        """
        tools = await self._openai_tools()
        session.messages.append({"role": "user", "content": question})
        t0 = time.monotonic()  # server-side agent time (LLM + tools), surfaced to the UI
        steps: list[dict[str, Any]] = []
        # Same (tool, args) failing repeatedly across rounds means the model is
        # stuck retrying instead of moving on — without this a bad metric name
        # (e.g. a spend query with no ad platform connected) can burn most of
        # max_rounds retrying the identical call before ever reaching an answer.
        fail_counts: dict[str, int] = {}

        # Reason -> Act -> Observe -> loop. Each round the model REASONS (one LLM
        # call) about what to do next; if it asks for tools we ACT on them (in
        # parallel), OBSERVE the results back into the conversation, and loop.
        # When it stops asking for tools, the reasoning tier writes the answer.
        tier = "tools"
        for _ in range(self.max_rounds):
            # --- REASON: one model call decides the next action (or the answer) ---
            session.messages[1] = {"role": "system", "content": session.scratchpad.render()}
            sanitize_history(session.messages)
            try:
                resp = await asyncio.to_thread(
                    self._router.complete,
                    tier=tier,
                    messages=session.messages,
                    tools=tools,
                    tool_choice="auto",
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("analyst_llm_error", module=module, error=str(exc))
                yield {"type": "error", "error": f"LLM error: {exc}", "status": 502,
                       "elapsed_ms": int((time.monotonic() - t0) * 1000)}
                return

            choice = resp.message
            tool_calls = choice.tool_calls or []

            # Tool model is ready to answer -> escalate the final synthesis to a
            # reasoning model, and STREAM it so the answer types out live instead
            # of landing all at once. (The tools tier already decided no more
            # data is needed, so the synthesis pass answers with what it has.)
            if not tool_calls:
                async for ev in self._stream_final(
                    session, session_id=session_id, module=module, steps=steps, t0=t0
                ):
                    yield ev
                return

            session.messages.append({
                "role": "assistant",
                "content": choice.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": sanitize_tool_arguments(tc.function.arguments),
                        },
                    }
                    for tc in tool_calls
                ],
            })

            # --- ACT: run every tool the model asked for this round concurrently.
            # These are independent read-only calls whose cost is each one's Cube
            # round-trip, so gather overlaps them instead of paying them in series.
            # _run_tool never raises (it returns a JSON error string), so one bad
            # call can't cancel the batch.
            planned = []
            for tc in tool_calls:
                try:
                    raw_args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    raw_args = {}
                scoped = inject_scope(tc.function.name, raw_args, module=module, brand_id=brand_id)
                planned.append((tc, scoped))
            payloads = await asyncio.gather(
                *(self._run_tool(tc.function.name, scoped, session) for tc, scoped in planned)
            )
            # --- OBSERVE: fold results back into history in call order (stable
            # step trace + tool-message order), then loop.
            for (tc, scoped), payload in zip(planned, payloads):
                ok = not payload.lstrip().startswith('{"error"')
                if not ok:
                    payload = _flag_repeated_failure(
                        payload, fail_counts, tc.function.name, scoped
                    )
                step = {"tool": tc.function.name,
                        "label": _step_label(tc.function.name, scoped),
                        "module": module, "ok": ok}
                steps.append(step)
                yield {"type": "step", **step}
                session.messages.append({"role": "tool", "tool_call_id": tc.id, "content": payload})

        logger.warning("analyst_round_limit", module=module, rounds=self.max_rounds)
        yield {
            "type": "final",
            "answer": "I reached my step limit before finishing. Please narrow the question "
            "(a specific metric and date range works best).",
            "module": module,
            "steps": steps,
            "session_id": session_id,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }

    async def ask(
        self, *, session_id: str, question: str, module: str | None, brand_id: str | None
    ) -> dict[str, Any]:
        """Buffered turn: run the loop and return only the final answer dict."""
        async with self._lock:  # serialize turns of a given process (sessions share the router buckets)
            session = self._session(session_id, module, brand_id)
            session.last_used = time.monotonic()
            self._evict()
            final: dict[str, Any] | None = None
            async for ev in self._run_turn(
                session, session_id=session_id, question=question, module=module, brand_id=brand_id
            ):
                if ev.get("type") == "error":
                    err = RuntimeError(ev["error"])
                    err.status_code = ev.get("status", 502)  # type: ignore[attr-defined]
                    raise err
                if ev.get("type") == "final":
                    final = {k: v for k, v in ev.items() if k != "type"}
            return final or {"answer": "", "module": module, "steps": [], "session_id": session_id}

    async def ask_stream(
        self, *, session_id: str, question: str, module: str | None, brand_id: str | None
    ):
        """Streaming turn: yield each progress event (step -> … -> final/error)
        for the SSE endpoint. Same loop as ``ask``, nothing buffered."""
        async with self._lock:
            session = self._session(session_id, module, brand_id)
            session.last_used = time.monotonic()
            self._evict()
            async for ev in self._run_turn(
                session, session_id=session_id, question=question, module=module, brand_id=brand_id
            ):
                yield ev


# ---------------------------------------------------------------------------
# HTTP surface — mounted onto the same Starlette app that serves /mcp, behind
# the same bearer-token middleware. The dashboard's Node backend proxies to it.
# ---------------------------------------------------------------------------


def mount_analyst_routes(app: Any, mcp: Any) -> None:
    """Add POST /agent/ask + GET /agent/health to the streamable-http app.

    The AnalystService is built lazily on first use so a missing Azure config
    degrades to a 503 on the analyst endpoints only — it never blocks the MCP
    server from starting."""
    from starlette.requests import Request
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route

    state: dict[str, Any] = {"service": None, "error": None}

    def get_service() -> AnalystService | None:
        if state["service"] is None and state["error"] is None:
            try:
                state["service"] = AnalystService(mcp)
            except Exception as exc:  # noqa: BLE001
                state["error"] = str(exc)
                logger.error("analyst_service_init_failed", error=str(exc))
        return state["service"]

    async def health(request: "Request") -> "JSONResponse":
        svc = get_service()
        return JSONResponse({"ok": svc is not None, "configured": svc is not None, "error": state["error"]})

    async def ask(request: "Request") -> "JSONResponse":
        svc = get_service()
        if svc is None:
            return JSONResponse(
                {"success": False, "error": f"analyst not configured: {state['error']}"},
                status_code=503,
            )
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        if not isinstance(body, dict):
            body = {}
        question = str(body.get("question") or "").strip()
        if not question:
            return JSONResponse({"success": False, "error": "question is required"}, status_code=400)
        session_id = str(body.get("session_id") or "").strip() or "default"
        module = body.get("module") or None
        brand_raw = body.get("brand_id")
        brand_id = str(brand_raw) if brand_raw not in (None, "") else None
        try:
            result = await svc.ask(
                session_id=session_id, question=question, module=module, brand_id=brand_id
            )
            return JSONResponse({"success": True, "data": result})
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", 500)
            logger.error("analyst_ask_failed", module=module, error=str(exc))
            return JSONResponse({"success": False, "error": str(exc)}, status_code=status)

    def _parse_ask_body(body: Any) -> tuple[dict[str, Any] | None, "JSONResponse | None"]:
        if not isinstance(body, dict):
            body = {}
        question = str(body.get("question") or "").strip()
        if not question:
            return None, JSONResponse({"success": False, "error": "question is required"}, status_code=400)
        brand_raw = body.get("brand_id")
        return {
            "session_id": str(body.get("session_id") or "").strip() or "default",
            "question": question,
            "module": body.get("module") or None,
            "brand_id": str(brand_raw) if brand_raw not in (None, "") else None,
        }, None

    async def ask_stream(request: "Request") -> Any:
        """SSE variant of /agent/ask — emits `data: {json}\\n\\n` frames: one
        `{"type":"step"}` per tool call as it lands, then a terminal
        `{"type":"final"}` (or `{"type":"error"}`). The Node backend pipes these
        straight to the dashboard so the user sees live progress instead of a
        blank spinner (parity with scripts/chat_web.py)."""
        svc = get_service()
        if svc is None:
            return JSONResponse(
                {"success": False, "error": f"analyst not configured: {state['error']}"},
                status_code=503,
            )
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        params, err_resp = _parse_ask_body(body)
        if params is None:
            return err_resp

        async def event_stream():
            # Emit immediately so proxies (Next.js / nginx) see first-byte activity
            # before the LLM/tool loop — otherwise idle waits look like a hung
            # gateway and surface as 504 while Azure rate-limits are still spinning.
            yield f"data: {json.dumps({'type': 'status', 'label': 'Working on it…'})}\n\n"
            try:
                async for ev in svc.ask_stream(**params):
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
            except Exception as exc:  # noqa: BLE001 — deliver failure in-band
                logger.error("analyst_stream_failed", module=params["module"], error=str(exc))
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    app.router.routes.append(Route("/agent/ask", ask, methods=["POST"]))
    app.router.routes.append(Route("/agent/ask/stream", ask_stream, methods=["POST"]))
    app.router.routes.append(Route("/agent/health", health, methods=["GET"]))
