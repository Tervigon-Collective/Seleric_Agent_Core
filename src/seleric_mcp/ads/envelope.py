"""Normalised response/error envelopes (blueprint §6, §15) and the cross-cutting
read/write guard wrappers shared by every ads tool.

The tool functions themselves only build the platform call + success payload;
scope checks, the ``write_enabled`` kill switch, hard budget policy, idempotency,
``validate_only`` handling (server-side validate for platforms that support it,
schema-only short-circuit otherwise), audit/operation logging, and error
normalisation all live here so every tool behaves identically.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from ..gateway.auth import request_scopes
from . import approval
from .errors import AdsApiError
from .idempotency import request_hash
from .meta import policy

if TYPE_CHECKING:  # avoid a circular import at runtime; only for type hints
    from ..gateway.server import AppContext

logger = structlog.get_logger()

PLATFORM = "meta"
APPROVAL_TTL_SECONDS = 300
MIN_APPROVAL_SECRET_LEN = 32


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
    if read_scope not in request_scopes(ctx.settings):
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
    server_side_validate: bool = False,
    confirmation_token: str | None = None,
    prepare: Callable[[], Awaitable[dict | None]] | None = None,
    approval_scope: dict | None = None,
    action: Callable[[], Awaitable[dict]],
) -> dict:
    """Write wrapper. Order (blueprint §5/§11/§12/§13/§16):
    scope -> hard budget policy -> validate_only -> kill switch -> idempotency
    -> execute -> store idempotency + audit + op-log.

    Meta (``platform == "meta"``) adds the owner's guardrails and a two-step
    approval: scope -> ``prepare()`` (brand allow list, created-only rule, live
    state; raises ``policy.PolicyViolation``) -> budget rules vs the live state
    -> validate_only -> no ``confirmation_token``: mint one bound to this exact
    call and return ``approval_required`` (nothing executed) | with a token:
    verify -> idempotency replay -> kill switch -> consume (single use) ->
    execute -> audit with ``approval_id``. ``approval_scope`` adds the tenant
    selectors (brand_id/account_id) to what the token is bound to.

    ``action`` performs the platform call and returns a success envelope (via
    ``ok``); ``operation_id`` is injected here. ``payload`` is the canonical
    request used for the idempotency hash and audit (already validated by the
    caller). ``budget_cap`` is the platform's hard cap (defaults to the Meta cap
    for back-compat); Google passes its micros cap explicitly.

    validate_only with ``server_side_validate`` (Google) runs ``action`` against
    the platform's validate-only mode — a real server-side check that mutates
    nothing, so it is allowed even when the kill switch is off. Without
    ``server_side_validate`` (Meta) it short-circuits to a schema-only check and
    no platform call is made — the envelope's warning then truthfully says the
    platform call was not performed.
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
    if write_scope not in request_scopes(ctx.settings):
        return _fail("PERMISSION_DENIED", f"Caller lacks scope '{write_scope}'.")

    if platform == PLATFORM:
        return await _execute_meta_write(
            ctx, tool_name, operation, op_id=op_id, started=started, fail=_fail,
            payload=payload, idempotency_key=idempotency_key, validate_only=validate_only,
            account_id=account_id, confirmation_token=confirmation_token,
            prepare=prepare, approval_scope=approval_scope, action=action,
        )

    # 2. hard budget policy (checked even for validate_only, so previews surface it)
    if budget_minor is not None and budget_cap > 0 and budget_minor > budget_cap:
        return _fail(
            "POLICY_RESTRICTION",
            f"Requested budget {budget_minor} exceeds policy cap {budget_cap}.",
            field="budget",
        )

    # 3. validate_only — kill switch / idempotency skipped in both paths below
    #    (a validate must never mutate, and its outcome is not replayable. The
    #    budget-policy check above still runs so previews surface the cap).
    if validate_only:
        if server_side_validate:
            try:
                result = await action()
            except AdsApiError as exc:
                env_out = error_from_exc(operation, exc)
                env_out["operation_id"] = op_id
                ctx.ads_ops.write(
                    op_id, tool_name, account_id=account_id, status="REJECTED",
                    request_id=exc.request_id, error_code=env_out["error"]["code"],
                    idempotency_key=idempotency_key,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                return env_out
            result.setdefault("operation_id", op_id)
            result["validated"] = True
            warnings = list(result.setdefault("warnings", []))
            warnings.append("validate_only: server-side validation by the platform — no mutation.")
            result["warnings"] = warnings
            ctx.ads_ops.write(
                op_id, tool_name, account_id=account_id, status="VALIDATED",
                idempotency_key=idempotency_key,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return result
        ctx.ads_ops.write(
            op_id, tool_name, account_id=account_id, status="VALIDATED",
            idempotency_key=idempotency_key,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return ok(operation, platform=platform, validated=True,
                  warnings=["validate_only: schema checked locally; no platform "
                            "call was made and no mutation performed."]) | {"operation_id": op_id}

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
    return await _run(ctx, tool_name, operation, op_id=op_id, started=started,
                      platform=platform, payload=payload, req_hash=req_hash,
                      idempotency_key=idempotency_key, account_id=account_id, action=action)


async def _run(
    ctx: AppContext, tool_name: str, operation: str, *, op_id: str, started: float,
    platform: str, payload: dict, req_hash: str, idempotency_key: str | None,
    account_id: str | None, action: Callable[[], Awaitable[dict]],
    audit_extra: dict | None = None,
) -> dict:
    """Execute + store idempotency + op-log + audit (shared by every platform)."""
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
            payload={"tool": tool_name, "error_code": env["error"]["code"], **(audit_extra or {})},
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
        payload={"tool": tool_name, "entity_id": entity_id, "account_id": account_id,
                 **(audit_extra or {})},
    )
    return result


async def _execute_meta_write(
    ctx: AppContext, tool_name: str, operation: str, *, op_id: str, started: float,
    fail: Callable[..., dict], payload: dict, idempotency_key: str | None,
    validate_only: bool, account_id: str | None, confirmation_token: str | None,
    prepare: Callable[[], Awaitable[dict | None]] | None, approval_scope: dict | None,
    action: Callable[[], Awaitable[dict]],
) -> dict:
    settings = ctx.settings
    # 2. guardrails: brand, created-only, live state, budget (re-run on the
    #    token call so the +10 % rule uses fresh live values)
    current: dict | None = None
    try:
        if prepare is not None:
            current = await prepare()
        policy.check_budget(payload, current, settings.meta_max_budget_minor)
    except policy.PolicyViolation as exc:
        return fail("POLICY_RESTRICTION", exc.message, field=exc.field)
    except AdsApiError as exc:
        env = error_from_exc(operation, exc)
        env["operation_id"] = op_id
        ctx.ads_ops.write(
            op_id, tool_name, account_id=account_id, status="REJECTED",
            request_id=exc.request_id, error_code=env["error"]["code"],
            idempotency_key=idempotency_key,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return env

    # 3. validate_only: schema + guardrails only, never a token or a mutation
    if validate_only:
        ctx.ads_ops.write(
            op_id, tool_name, account_id=account_id, status="VALIDATED",
            idempotency_key=idempotency_key,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return ok(operation, validated=True,
                  warnings=["validate_only: schema and guardrails checked locally; no "
                            "platform call was made and no mutation performed."]) | {
            "operation_id": op_id, "current": current}

    secret = settings.approval_secret or ""
    if len(secret) < MIN_APPROVAL_SECRET_LEN:
        return fail("POLICY_RESTRICTION",
                    f"APPROVAL_SECRET is not set (min {MIN_APPROVAL_SECRET_LEN} chars); "
                    "Meta writes are refused.")
    bound = {"tool": tool_name, "payload": payload, "scope": approval_scope or {},
             "account_id": account_id}
    p_hash = approval.payload_hash(bound)
    store = ctx.approvals
    now = int(time.time())

    # 4a. no token: preview + mint (nothing executes)
    if not confirmation_token:
        request_id = f"apr_{uuid.uuid4().hex[:16]}"
        expires = now + APPROVAL_TTL_SECONDS
        token = approval.mint_token(secret, request_id, p_hash, expires)
        expires_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires))
        preview = {"tool": tool_name, "operation": operation, "current": current,
                   "change": payload, "target": approval_scope or {}}
        store.create(request_id, tool_name, bound, p_hash, preview,
                     approval.token_hash(token), expires_iso)
        ctx.ads_ops.write(
            op_id, tool_name, account_id=account_id, status="APPROVAL_REQUIRED",
            idempotency_key=idempotency_key,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        ctx.audit.write(f"meta_ads.{operation}.proposed", ctx.actor,
                        payload={"tool": tool_name, "approval_id": request_id})
        return {
            "success": False,
            "approval_required": True,
            "status": "APPROVAL_REQUIRED",
            "platform": PLATFORM,
            "operation": operation,
            "operation_id": op_id,
            "approval_id": request_id,
            "preview": preview,
            "confirmation_token": token,
            "expires_at": expires_iso,
            "write_enabled": settings.write_enabled,
            "message": "Not executed. Show this preview to a person; only after they "
                       "approve, call the same tool with the same arguments plus "
                       "confirmation_token. The token is single-use and expires in "
                       f"{APPROVAL_TTL_SECONDS // 60} minutes.",
        }

    # 4b. token: signature + expiry + bound to this exact call
    valid, request_id, expires = approval.verify_token(secret, confirmation_token, p_hash)
    row = store.get(request_id) if valid and request_id else None
    if not valid or row is None or row["payload_hash"] != p_hash:
        return fail("APPROVAL_INVALID",
                    "confirmation_token does not match this call (different tool or "
                    "arguments) or is not a token this server issued.")
    if expires is None or expires < now:
        store.set_status(request_id, "EXPIRED")
        return fail("APPROVAL_EXPIRED", "confirmation_token expired; preview again.")

    # 5. idempotency replay (a retry after success returns the stored result)
    req_hash = request_hash(payload)
    if idempotency_key:
        found = ctx.ads_idempotency.lookup(tool_name, idempotency_key, req_hash)
        if found.status == "HIT" and found.result is not None:
            return found.result
        if found.status == "CONFLICT":
            return fail(
                "IDEMPOTENCY_CONFLICT",
                f"idempotency_key '{idempotency_key}' was already used with different arguments.",
            )

    # 6. kill switch (before consuming, so a disabled server doesn't burn the token)
    if not settings.write_enabled:
        return fail(
            "POLICY_RESTRICTION",
            "Writes are disabled (write_enabled=false). Enable to allow mutations.",
        )

    # 7. single use
    if not store.consume_token(request_id):
        return fail("APPROVAL_INVALID", "confirmation_token was already used.")

    result = await _run(ctx, tool_name, operation, op_id=op_id, started=started,
                        platform=PLATFORM, payload=payload, req_hash=req_hash,
                        idempotency_key=idempotency_key, account_id=account_id,
                        action=action, audit_extra={"approval_id": request_id})
    executed = result.get("success") is True
    store.set_status(
        request_id, "EXECUTED" if executed else "FAILED",
        executor_response=result,
        failure_reason=None if executed else (result.get("error") or {}).get("code"),
        executed_at=approval.utcnow_iso() if executed else None,
    )
    return result
