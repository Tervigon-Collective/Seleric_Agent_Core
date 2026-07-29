"""Client-supplied-key idempotency + operation logging for the Ads write tools.

Blueprint §13 (idempotency) and §16 (operation log). Distinct from the broker's
``IdempotencyStore`` (``actions/stores.py``), which keys on a server-derived
``{action_id}:{payload_hash}``. Here the key is the caller's ``idempotency_key``
scoped per tool, and the full prior result is stored so a replay returns it
verbatim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..actions.tokens import payload_hash
from ..storage.db import Database, utcnow_iso

DEFAULT_ADS_IDEMPOTENCY_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class IdempotencyLookup:
    """Result of a lookup: exactly one of the three states is true."""

    status: str  # "HIT" | "CONFLICT" | "MISS"
    result: dict | None = None


def request_hash(payload: dict) -> str:
    """Canonical-JSON SHA-256 of the request payload (reuses tokens.payload_hash)."""
    return payload_hash(payload)


class AdsIdempotencyStore:
    """SQLite-backed idempotency for granular ads write tools."""

    def __init__(self, db: Database, window: timedelta = DEFAULT_ADS_IDEMPOTENCY_WINDOW):
        self._db = db
        self._window = window

    def lookup(self, tool_name: str, key: str, req_hash: str) -> IdempotencyLookup:
        """Look up a prior call for (tool_name, key). Purges expired rows first.

        - HIT: same key + same request_hash -> returns the stored result.
        - CONFLICT: same key + different request_hash (blueprint §13).
        - MISS: no live row for this key.
        """
        now = datetime.now(UTC)
        self._db.execute(
            "DELETE FROM ads_idempotency WHERE expires_at <= ?", (now.isoformat(),)
        )
        row = self._db.fetchone(
            "SELECT request_hash, result_json FROM ads_idempotency "
            "WHERE tool_name = ? AND idempotency_key = ?",
            (tool_name, key),
        )
        if row is None:
            return IdempotencyLookup("MISS")
        if row["request_hash"] != req_hash:
            return IdempotencyLookup("CONFLICT")
        try:
            return IdempotencyLookup("HIT", json.loads(row["result_json"]))
        except json.JSONDecodeError:
            return IdempotencyLookup("MISS")

    def store(self, tool_name: str, key: str, req_hash: str, result: dict) -> None:
        now = datetime.now(UTC)
        self._db.execute(
            """INSERT OR REPLACE INTO ads_idempotency
               (tool_name, idempotency_key, request_hash, result_json, created_at, expires_at)
               VALUES (?,?,?,?,?,?)""",
            (
                tool_name,
                key,
                req_hash,
                json.dumps(result),
                now.isoformat(),
                (now + self._window).isoformat(),
            ),
        )


class OperationLog:
    """Append-only structured log of ads tool calls (blueprint §16)."""

    def __init__(self, db: Database):
        self._db = db

    def write(
        self,
        operation_id: str,
        tool_name: str,
        *,
        account_id: str | None = None,
        entity_ids: list[str] | None = None,
        status: str,
        request_id: str | None = None,
        error_code: str | None = None,
        idempotency_key: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self._db.execute(
            """INSERT INTO ads_operations
               (operation_id, tool_name, account_id, entity_ids, status, request_id,
                error_code, idempotency_key, duration_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                operation_id,
                tool_name,
                account_id,
                json.dumps(entity_ids) if entity_ids else None,
                status,
                request_id,
                error_code,
                idempotency_key,
                duration_ms,
                utcnow_iso(),
            ),
        )
