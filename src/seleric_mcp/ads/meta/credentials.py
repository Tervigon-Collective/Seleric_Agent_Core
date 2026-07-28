"""Multi-tenant Meta credential resolution from Postgres ``core.brand_envs``.

Same source of truth the data_platform/mage-ai loaders use
(``data_loaders/meta_ads/load_brand_env_meta_ads.py``): rows where
``platform='meta' AND is_active=true`` carry ``account_id``, ``access_token``,
``brand_id``, ``company_id`` and an ``extra_data.api_version``.

A caller selects a credential by ``account_id`` (act_ form or bare) or
``brand_id`` (+ optional ``company_id``). If Postgres is unreachable we soft-fall
back to a static ``META_ACCESS_TOKEN`` from env (mirrors the mage-ai behaviour);
if there is no fallback token, a structured error is raised. Token values are
never logged.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import psycopg2
import structlog
from psycopg2.extras import RealDictCursor

from ...config import Settings
from .client import MetaApiError

logger = structlog.get_logger()

_PLATFORM = "meta"


@dataclass(frozen=True)
class MetaCredential:
    access_token: str
    ad_account_id: str
    api_version: str
    brand_id: int | None
    company_id: int | None
    source: str  # "brand_envs" | "env_fallback"


def _normalize_account_id(account_id: str | None) -> str:
    """Strip optional act_ prefix for comparison (Meta stores act_123 or 123)."""
    if not account_id:
        return ""
    s = str(account_id).strip()
    return s[4:].strip() if s.lower().startswith("act_") else s


class MetaCredentialStore:
    """Resolves + caches Meta credentials from core.brand_envs."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._ttl = settings.meta_cred_cache_ttl_seconds
        self._cache: dict[tuple, tuple[float, MetaCredential]] = {}

    # ---- DSN (mirrors mage-ai _pg_dsn) ----

    def _dsn(self) -> str:
        s = self._settings
        if s.pg_dsn:
            return s.pg_dsn
        return (
            f"postgresql://{s.pg_user}:{s.pg_password}@{s.pg_host}:{s.pg_port}/{s.pg_dbname}"
        )

    def _configured(self) -> bool:
        s = self._settings
        return bool(s.pg_dsn or (s.pg_host and s.pg_dbname and s.pg_user))

    # ---- public resolution ----

    async def resolve(
        self,
        *,
        account_id: str | None = None,
        brand_id: str | int | None = None,
        company_id: str | int | None = None,
    ) -> MetaCredential:
        if not (account_id or brand_id is not None):
            raise MetaApiError(
                "Provide account_id or brand_id to select Meta credentials "
                "from core.brand_envs.",
                code=100,
            )
        key = (_normalize_account_id(account_id), str(brand_id), str(company_id))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return cached[1]

        try:
            row = await asyncio.to_thread(self._query, account_id, brand_id, company_id)
        except psycopg2.OperationalError as exc:
            cred = self._env_fallback(account_id, brand_id, company_id, exc)
            self._cache[key] = (time.monotonic(), cred)
            return cred
        except Exception as exc:  # non-connectivity DB error
            raise MetaApiError(f"brand_envs lookup failed: {exc}") from exc

        if not row:
            raise MetaApiError(
                "No active Meta credential in core.brand_envs for the given selector "
                f"(account_id={account_id!r}, brand_id={brand_id!r}).",
                error_type="AUTH",
            )
        cred = self._row_to_cred(row)
        self._cache[key] = (time.monotonic(), cred)
        return cred

    # ---- internals ----

    def _env_fallback(
        self, account_id, brand_id, company_id, exc: Exception
    ) -> MetaCredential:
        token = self._settings.meta_access_token
        if not token:
            raise MetaApiError(
                "Postgres unavailable for brand_envs lookup and no META_ACCESS_TOKEN "
                "fallback is configured.",
                http_status=503,
                retryable=True,
            ) from exc
        logger.warning("meta_cred_env_fallback", error=str(exc))
        return MetaCredential(
            access_token=token,
            ad_account_id=str(account_id).strip() if account_id else "",
            api_version=self._settings.meta_api_version,
            brand_id=int(brand_id) if str(brand_id).isdigit() else None,
            company_id=int(company_id) if str(company_id).isdigit() else None,
            source="env_fallback",
        )

    def _row_to_cred(self, row: dict) -> MetaCredential:
        extra = row.get("extra_data") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra) if extra else {}
            except json.JSONDecodeError:
                extra = {}
        api_version = (extra.get("api_version") if isinstance(extra, dict) else None) \
            or self._settings.meta_api_version
        account = row.get("account_id")
        return MetaCredential(
            access_token=row["access_token"],
            ad_account_id=str(account).strip() if account else "",
            api_version=str(api_version),
            brand_id=int(row["brand_id"]) if row.get("brand_id") is not None else None,
            company_id=int(row["company_id"]) if row.get("company_id") is not None else None,
            source="brand_envs",
        )

    def _query(self, account_id, brand_id, company_id) -> dict | None:
        """Blocking psycopg2 query (run via to_thread). Filters on platform +
        is_active + optional brand_id/company_id; account_id matched in Python
        by normalised value to tolerate act_ vs bare storage."""
        conditions = ["platform = %s", "is_active = true"]
        params: list = [_PLATFORM]
        if brand_id is not None:
            conditions.append("brand_id = %s")
            params.append(int(brand_id))
        if company_id is not None:
            conditions.append("company_id = %s")
            params.append(int(company_id))
        schema = self._settings.pg_schema or "core"
        sql = (
            "SELECT id, account_id, access_token, brand_id, company_id, extra_data, updated_at "
            f"FROM {schema}.brand_envs "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY updated_at DESC"
        )
        conn = psycopg2.connect(self._dsn(), connect_timeout=self._settings.pg_connect_timeout)
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
        if not rows:
            return None
        if account_id:
            target = _normalize_account_id(account_id)
            for r in rows:
                if _normalize_account_id(r.get("account_id")) == target:
                    return r
            return None
        return rows[0]
