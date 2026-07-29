"""Multi-tenant Google Ads credential resolution.

Combines shared app credentials from env/settings (developer token, OAuth
client id/secret, optional login_customer_id) with per-tenant OAuth
``refresh_token`` + ``customer_id`` from Postgres ``core.brand_envs``
(``platform IN ('google_ads','google-ads')``) — the same source the
data_platform/mage-ai ``load_brand_env_google_ads`` loader reads.

A caller selects a tenant by ``customer_id`` (digits, dashes stripped) or
``brand_id``. Secrets are never logged.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import psycopg2
import structlog
from psycopg2.extras import RealDictCursor

from ...config import Settings
from ..errors import AdsApiError

logger = structlog.get_logger()

_PLATFORMS = ("google_ads", "google-ads")


class GoogleApiError(AdsApiError):
    platform = "google"

    def error_code(self) -> str:
        et = (self.error_type or "").upper()
        if "AUTHENTICATION" in et:
            return "AUTHENTICATION_FAILED"
        if "AUTHORIZATION" in et or "PERMISSION" in et:
            return "PERMISSION_DENIED"
        if "NOT_FOUND" in et:
            return "ENTITY_NOT_FOUND"
        if "QUOTA" in et or "RESOURCE_EXHAUSTED" in et:
            return "RATE_LIMITED"
        if (self.http_status or 0) >= 500:
            return "PLATFORM_UNAVAILABLE"
        if self.retryable:
            return "RATE_LIMITED"
        if self.code is not None or et:
            return "INVALID_ARGUMENT"
        return "INTERNAL_ERROR"


def _normalize_customer_id(customer_id: str | None) -> str:
    if not customer_id:
        return ""
    return str(customer_id).replace("-", "").strip()


@dataclass(frozen=True)
class GoogleCredential:
    developer_token: str
    client_id: str
    client_secret: str
    refresh_token: str
    customer_id: str
    login_customer_id: str | None
    api_version: str
    brand_id: int | None
    company_id: int | None

    def client_config(self) -> dict:
        """Config dict for GoogleAdsClient.load_from_dict (never logged)."""
        cfg = {
            "developer_token": self.developer_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "use_proto_plus": True,
        }
        if self.login_customer_id:
            cfg["login_customer_id"] = self.login_customer_id
        return cfg


class GoogleCredentialStore:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._ttl = settings.google_cred_cache_ttl_seconds
        self._cache: dict[tuple, tuple[float, GoogleCredential]] = {}

    def _dsn(self) -> str:
        s = self._settings
        if s.pg_dsn:
            return s.pg_dsn
        return f"postgresql://{s.pg_user}:{s.pg_password}@{s.pg_host}:{s.pg_port}/{s.pg_dbname}"

    async def resolve(
        self, *, customer_id: str | None = None, brand_id: str | int | None = None,
        company_id: str | int | None = None,
    ) -> GoogleCredential:
        s = self._settings
        if not (s.google_developer_token and s.google_client_id and s.google_client_secret):
            raise GoogleApiError(
                "Google Ads app credentials are not configured "
                "(GOOGLE_ADS_DEVELOPER_TOKEN / CLIENT_ID / CLIENT_SECRET).",
                error_type="AUTHENTICATION",
            )
        if not (customer_id or brand_id is not None):
            raise GoogleApiError(
                "Provide customer_id or brand_id to select Google credentials.",
                code="INVALID_ARGUMENT",
            )
        key = (_normalize_customer_id(customer_id), str(brand_id), str(company_id))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return cached[1]

        try:
            row = await asyncio.to_thread(self._query, customer_id, brand_id, company_id)
        except psycopg2.OperationalError as exc:
            raise GoogleApiError(
                f"Postgres unavailable for brand_envs lookup: {exc}",
                http_status=503, retryable=True,
            ) from exc
        except Exception as exc:
            raise GoogleApiError(f"brand_envs lookup failed: {exc}") from exc

        if not row:
            raise GoogleApiError(
                "No active Google Ads credential in core.brand_envs for the given selector "
                f"(customer_id={customer_id!r}, brand_id={brand_id!r}).",
                error_type="AUTHENTICATION",
            )
        cred = self._row_to_cred(row)
        self._cache[key] = (time.monotonic(), cred)
        return cred

    def _row_to_cred(self, row: dict) -> GoogleCredential:
        s = self._settings
        extra = row.get("extra_data") or {}
        if not isinstance(extra, dict):
            extra = {}
        refresh_token = row.get("refresh_token") or extra.get("refresh_token")
        cust = _normalize_customer_id(row.get("account_id") or extra.get("customer_id"))
        if not refresh_token:
            raise GoogleApiError(
                f"brand_envs row (id={row.get('id')}) missing refresh_token.",
                error_type="AUTHENTICATION",
            )
        if not cust:
            raise GoogleApiError(
                f"brand_envs row (id={row.get('id')}) missing customer_id/account_id.",
                code="INVALID_ARGUMENT",
            )
        api_version = extra.get("api_version") or s.google_api_version
        if not str(api_version).startswith("v"):
            api_version = f"v{api_version}"
        return GoogleCredential(
            developer_token=s.google_developer_token,
            client_id=s.google_client_id,
            client_secret=s.google_client_secret,
            refresh_token=refresh_token,
            customer_id=cust,
            login_customer_id=_normalize_customer_id(
                s.google_login_customer_id or extra.get("login_customer_id")
            ) or None,
            api_version=str(api_version),
            brand_id=int(row["brand_id"]) if row.get("brand_id") is not None else None,
            company_id=int(row["company_id"]) if row.get("company_id") is not None else None,
        )

    def _query(self, customer_id, brand_id, company_id) -> dict | None:
        conditions = ["platform IN ('google_ads','google-ads')", "is_active = true"]
        params: list = []
        if brand_id is not None:
            conditions.append("brand_id = %s")
            params.append(int(brand_id))
        if company_id is not None:
            conditions.append("company_id = %s")
            params.append(int(company_id))
        schema = self._settings.pg_schema or "core"
        sql = (
            "SELECT id, account_id, refresh_token, brand_id, company_id, extra_data, updated_at "
            f"FROM {schema}.brand_envs "
            f"WHERE {' AND '.join(conditions)} ORDER BY updated_at DESC"
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
        if customer_id:
            target = _normalize_customer_id(customer_id)
            for r in rows:
                if _normalize_customer_id(r.get("account_id")) == target:
                    return r
            return None
        return rows[0]
