"""Normalised response/error envelopes (blueprint §6, §15) and the cross-cutting
read/write guard wrappers shared by every ads tool.

The tool functions themselves only build the platform call + success payload;
scope checks, the ``write_enabled`` kill switch, hard budget policy, idempotency,
``validate_only`` short-circuit, audit/operation logging, and error normalisation
all live here so every tool behaves identically.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from .errors import AdsApiError
from .idempotency import request_hash

if TYPE_CHECKING:  # avoid a circular import at runtime; only for type hints
    from ..gateway.server import AppContext

logger = structlog.get_logger()

PLATFORM = "meta"


def new_operation_id() -> str:
    return f"op_{uuid.uuid4().hex[:16]}"


# ---- error-code mapping (delegates to the platform error's own mapper) ----


def map_error_code(exc: AdsApiError) -> str:
    return exc.error_code()


# ---- envelope builders ----


def ok(
    operation: str,
    *,
    platform: str = PLATFORM,
    entity: dict | None = None,
    changes: dict | None = None,
    warnings: list[str] | None = None,
    request_id: str | None = None,
    raw_response: Any = None,
    validated: bool = False,
    extra: dict | None = None,
) -> dict:
    """Blueprint §6 success envelope. ``operation_id`` is injected by the wrapper."""
    payload: dict[str, Any] = {
        "success": True,
        "platform": platform,
        "operation": operation,
        "request_id": request_id,
        "entity": entity,
        "changes": changes or {},
        "warnings": warnings or [],
        "errors": [],
        "raw_response": raw_response,
    }
    if validated:
        payload["validated"] = True
    if extra:
        payload.update(extra)
    return payload


def error(
    operation: str,
    *,
    code: str,
    message: str,
    platform: str = PLATFORM,
    retryable: bool = False,
    field: str | None = None,
    platform_error_code: Any = None,
    request_id: str | None = None,
    details: dict | None = None,
) -> dict:
    """Blueprint §15 error envelope."""
    return {
        "success": False,
        "operation": operation,
        "error": {
            "code": code,
            "message": message,
            "platform": platform,
            "retryable": retryable,
            "field": field,
            "platform_error_code": platform_error_code,
            "request_id": request_id,
            "details": details or {},
        },
    }


def error_from_exc(operation: str, exc: AdsApiError) -> dict:
    return error(
        operation,
        code=exc.error_code(),
        message=exc.message,
        platform=exc.platform,
        retryable=exc.retryable,
        platform_error_code=exc.code,
        request_id=exc.request_id,
        details=exc.details(),
    )


# back-compat alias (Meta-only call sites / tests)
error_from_meta = error_from_exc


def bulk(operation: str, results: list[dict], errors: list[dict]) -> dict:
    """Blueprint §6 bulk envelope (used by future batch tools)."""
    requested = len(results) + len(errors)
    succeeded = len(results)
    return {
        "success": len(errors) == 0,
        "partial_success": bool(errors) and succeeded > 0,
        "operation": operation,
        "requested": requested,
        "succeeded": succeeded,
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }


# ---- guard wrappers ----


async def execute_read(
    ctx: AppContext,
    tool_name: str,
    operation: str,
    *,
    account_id: str | None = None,
    platform: str = "meta",
    read_scope: str = "meta_ads:read",
    action: Callable[[], Awaitable[dict]],
) -> dict:
    """Read wrapper: scope gate + error normalisation + operation log. Reads
    never mutate, so no kill switch / idempotency."""
    started = time.monotonic()
    op_id = new_operation_id()
    if read_scope not in ctx.settings.caller_scopes:
        return error(
            operation, platform=platform,
            code="PERMISSION_DENIED",
            message=f"Caller lacks scope '{read_scope}'.",
        ) | {"operation_id": op_id}
    try:
        result = await action()
        result.setdefault("operation_id", op_id)
        ctx.ads_ops.write(
            op_id, tool_name, account_id=account_id, status="OK",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result
    except AdsApiError as exc:
        env = error_from_exc(operation, exc)
        env["operation_id"] = op_id
        ctx.ads_ops.write(
            op_id, tool_name, account_id=account_id, status="FAILED",
            request_id=exc.request_id, error_code=env["error"]["code"],
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        logger.warning("ads_read_failed", tool=tool_name, code=env["error"]["code"])
        return env


async def execute_write(
    ctx: AppContext,
    tool_name: str,
    operation: str,
    *,
    payload: dict,
    idempotency_key: str | None,
    validate_only: bool,
    account_id: str | None = None,
    budget_minor: int | None = None,
    budget_cap: int | None = None,
    platform: str = "meta",
    write_scope: str = "meta_ads:write",
    action: Callable[[], Awaitable[dict]],
) -> dict:
    """Write wrapper. Order (blueprint §5/§11/§12/§13/§16):
    scope -> kill switch -> hard budget policy -> idempotency -> validate_only
    short-circuit -> execute -> store idempotency + audit + op-log.

    ``action`` performs the platform call and returns a success envelope (via
    ``ok``); ``operation_id`` is injected here. ``payload`` is the canonical
    request used for the idempotency hash and audit (already validated by the
    caller). ``budget_cap`` is the platform's hard cap (defaults to the Meta cap
    for back-compat); Google passes its micros cap explicitly.
    """
    started = time.monotonic()
    op_id = new_operation_id()
    if budget_cap is None:
        budget_cap = ctx.settings.meta_max_budget_minor

    def _fail(code: str, message: str, **kw) -> dict:
        ctx.ads_ops.write(
            op_id, tool_name, account_id=account_id, status="REJECTED",
            error_code=code, idempotency_key=idempotency_key,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return error(operation, platform=platform, code=code, message=message, **kw) | {
            "operation_id": op_id
        }

    # 1. scope
    if write_scope not in ctx.settings.caller_scopes:
        return _fail("PERMISSION_DENIED", f"Caller lacks scope '{write_scope}'.")

    # 2. hard budget policy (checked even for validate_only, so previews surface it)
    if budget_minor is not None and budget_cap > 0 and budget_minor > budget_cap:
        return _fail(
            "POLICY_RESTRICTION",
            f"Requested budget {budget_minor} exceeds policy cap {budget_cap}.",
            field="budget",
        )

    # 3. validate_only short-circuit — no kill switch, no idempotency, no mutation.
    if validate_only:
        ctx.ads_ops.write(
            op_id, tool_name, account_id=account_id, status="VALIDATED",
            idempotency_key=idempotency_key,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return ok(operation, platform=platform, validated=True,
                  warnings=["validate_only: no mutation performed."]) | {"operation_id": op_id}

    # 4. kill switch
    if not ctx.settings.write_enabled:
        return _fail(
            "POLICY_RESTRICTION",
            "Writes are disabled (write_enabled=false). Enable to allow mutations.",
        )

    # 5. idempotency
    req_hash = request_hash(payload)
    if idempotency_key:
        found = ctx.ads_idempotency.lookup(tool_name, idempotency_key, req_hash)
        if found.status == "HIT" and found.result is not None:
            return found.result
        if found.status == "CONFLICT":
            return _fail(
                "IDEMPOTENCY_CONFLICT",
                f"idempotency_key '{idempotency_key}' was already used with different arguments.",
            )

    # 6. execute
    try:
        result = await action()
    except AdsApiError as exc:
        env = error_from_exc(operation, exc)
        env["operation_id"] = op_id
        ctx.ads_ops.write(
            op_id, tool_name, account_id=account_id, status="FAILED",
            request_id=exc.request_id, error_code=env["error"]["code"],
            idempotency_key=idempotency_key,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        ctx.audit.write(
            f"{platform}_ads.{operation}.failed", ctx.actor,
            payload={"tool": tool_name, "error_code": env["error"]["code"]},
        )
        logger.warning("ads_write_failed", tool=tool_name, code=env["error"]["code"])
        return env

    result.setdefault("operation_id", op_id)
    entity = result.get("entity") or {}
    entity_id = entity.get("id") if isinstance(entity, dict) else None
    if idempotency_key:
        ctx.ads_idempotency.store(tool_name, idempotency_key, req_hash, result)
    ctx.ads_ops.write(
        op_id, tool_name, account_id=account_id,
        entity_ids=[entity_id] if entity_id else None, status="EXECUTED",
        request_id=result.get("request_id"), idempotency_key=idempotency_key,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    ctx.audit.write(
        f"{platform}_ads.{operation}.executed", ctx.actor,
        payload={"tool": tool_name, "entity_id": entity_id, "account_id": account_id},
    )
    return result
