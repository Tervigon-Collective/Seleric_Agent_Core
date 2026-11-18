"""Approval primitives for Meta writes: single-use, short-TTL HMAC-SHA256
confirmation tokens bound to the exact call (same primitive as seleric_systems
dispatch_actions), and the ``pending_actions`` record each preview creates.
A write cannot execute without a token minted from its own preview.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from ..storage.db import Database, utcnow_iso


def payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def mint_token(secret: str, action_request_id: str, p_hash: str, expires_epoch: int) -> str:
    msg = f"{action_request_id}:{p_hash}:{expires_epoch}".encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    raw = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    # Token embeds its own claims so commit() can re-verify without a lookup key.
    return f"{action_request_id}.{expires_epoch}.{raw}"


def parse_token(token: str) -> tuple[str, int, str] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    action_request_id, expires_str, sig = parts
    try:
        return action_request_id, int(expires_str), sig
    except ValueError:
        return None


def verify_token(secret: str, token: str, p_hash: str) -> tuple[bool, str | None, int | None]:
    """Returns (signature_valid, action_request_id, expires_epoch). Constant-time compare."""
    parsed = parse_token(token)
    if parsed is None:
        return False, None, None
    action_request_id, expires_epoch, _ = parsed
    expected = mint_token(secret, action_request_id, p_hash, expires_epoch)
    return hmac.compare_digest(token, expected), action_request_id, expires_epoch


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ActionStore:
    def __init__(self, db: Database):
        self._db = db

    def create(
        self,
        action_request_id: str,
        action_id: str,
        payload: dict,
        p_hash: str,
        preview: dict,
        token_h: str | None,
        token_expires_at: str | None,
    ) -> None:
        self._db.execute(
            """INSERT INTO pending_actions
               (action_request_id, action_id, payload_json, payload_hash, status,
                preview_json, token_hash, token_expires_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                action_request_id,
                action_id,
                json.dumps(payload),
                p_hash,
                "PENDING",
                json.dumps(preview),
                token_h,
                token_expires_at,
                utcnow_iso(),
            ),
        )

    def get(self, action_request_id: str) -> dict | None:
        row = self._db.fetchone(
            "SELECT * FROM pending_actions WHERE action_request_id = ?", (action_request_id,)
        )
        return dict(row) if row else None

    def consume_token(self, action_request_id: str) -> bool:
        """Atomically mark the token consumed; False if already consumed/absent.
        Marked BEFORE dispatch so the token stays single-use even on a crash."""
        now = utcnow_iso()
        with self._db._lock, self._db._conn:
            cur = self._db._conn.execute(
                """UPDATE pending_actions SET token_consumed_at = ?
                   WHERE action_request_id = ? AND token_consumed_at IS NULL""",
                (now, action_request_id),
            )
            return cur.rowcount == 1

    def set_status(
        self,
        action_request_id: str,
        status: str,
        *,
        executor_response: dict | None = None,
        failure_reason: str | None = None,
        audit_ref: str | None = None,
        executed_at: str | None = None,
    ) -> None:
        self._db.execute(
            """UPDATE pending_actions
               SET status = ?, executor_response_json = ?, failure_reason = ?,
                   audit_ref = COALESCE(?, audit_ref), executed_at = COALESCE(?, executed_at)
               WHERE action_request_id = ?""",
            (
                status,
                json.dumps(executor_response) if executor_response is not None else None,
                failure_reason,
                audit_ref,
                executed_at,
                action_request_id,
            ),
        )
