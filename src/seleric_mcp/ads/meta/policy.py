"""Meta write guardrails (owner decisions, 2026-11-18): brand allow list,
budget cap / max raise, and the created-only rule. Pure checks that raise
``PolicyViolation``; ``envelope.execute_write`` turns that into a
POLICY_RESTRICTION envelope before any approval token is minted.
"""

from __future__ import annotations

from ...storage.db import Database, utcnow_iso

LIFETIME_CAP_MULTIPLE = 30  # lifetime cap = 30 x the daily cap
MAX_RAISE_PCT = 10  # a raise may be at most +10 % over the live budget
WRITE_TARGETS_ALL = "all"


class PolicyViolation(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def check_brand(brand_id, allowed: frozenset[str]) -> None:
    if brand_id is None or str(brand_id) not in allowed:
        raise PolicyViolation(
            f"Meta writes are not enabled for brand {brand_id!r} "
            f"(allowed: {', '.join(sorted(allowed)) or 'none'}).",
            field="brand_id",
        )


def check_budget(payload: dict, current: dict | None, daily_cap: int) -> None:
    """``payload`` carries daily_budget / lifetime_budget (minor units).
    ``current`` is the live entity for an update, None for a create.
    A cut (below the live value) is always allowed; otherwise the cap and the
    +10 % rule apply. An update whose live budget of the same kind is unknown
    is refused (fail closed)."""
    for kind in ("daily_budget", "lifetime_budget"):
        new = payload.get(kind)
        if new is None:
            continue
        new = int(new)
        cap = daily_cap * (LIFETIME_CAP_MULTIPLE if kind == "lifetime_budget" else 1)
        if current is not None:
            live = current.get(kind)
            if live in (None, "", "0", 0):
                raise PolicyViolation(
                    f"Live {kind} is unknown or unset on this entity, so the change "
                    "cannot be checked against the +10 % rule.", field=kind,
                )
            live = int(live)
            if new <= live:
                continue  # cut (or no change): always allowed
            if new * 100 > live * (100 + MAX_RAISE_PCT):
                raise PolicyViolation(
                    f"{kind} {new} raises the live {live} by more than {MAX_RAISE_PCT} % "
                    f"(max {live * (100 + MAX_RAISE_PCT) // 100}).", field=kind,
                )
        if cap <= 0:
            raise PolicyViolation("META_MAX_BUDGET_MINOR is not set; budgets are refused.", field=kind)
        if new > cap:
            raise PolicyViolation(f"{kind} {new} exceeds the policy cap {cap}.", field=kind)


class OwnedEntities:
    """Meta ids created through the meta_* tools (``meta_owned_entities``)."""

    def __init__(self, db: Database):
        self._db = db

    def add(self, entity_id: str, entity_type: str, *, brand_id=None,
            account_id: str | None = None, parent_id: str | None = None) -> None:
        self._db.execute(
            """INSERT OR IGNORE INTO meta_owned_entities
               (entity_id, entity_type, brand_id, account_id, parent_id, created_at)
               VALUES (?,?,?,?,?,?)""",
            (str(entity_id), entity_type, None if brand_id is None else str(brand_id),
             account_id, parent_id, utcnow_iso()),
        )

    def has(self, entity_id: str) -> bool:
        return self._db.fetchone(
            "SELECT 1 FROM meta_owned_entities WHERE entity_id = ?", (str(entity_id),)
        ) is not None


def require_owned(owned: OwnedEntities, entity_id: str, write_targets: str) -> None:
    if write_targets == WRITE_TARGETS_ALL:
        return
    if not owned.has(entity_id):
        raise PolicyViolation(
            f"{entity_id} was not created by Seleric; existing Meta entities are "
            "read-only (META_WRITE_TARGETS=created_only).", field="entity_id",
        )
