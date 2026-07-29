"""Async client for the Meta Marketing Graph API.

Self-contained httpx wrapper (there is no shared HTTP layer to reuse; modelled
on ``actions/executors/pipeboard.py``). Handles auth (access token +
optional appsecret_proof), GET/POST/multipart, cursor pagination, retry/backoff
for Meta rate-limit codes, and Meta-error -> ``MetaApiError`` normalisation.

The client returns parsed JSON and raises ``MetaApiError`` on failure; the
envelope layer (``ads/envelope.py``) turns both into the blueprint response
shapes. Nothing here decides business meaning.
"""

import asyncio
import hashlib
import hmac

import httpx
import structlog

from ...config import Settings
from ..errors import AdsApiError

logger = structlog.get_logger()

# Meta error codes that indicate throttling and are worth retrying with backoff.
# 4/17/32 = app/user/page rate limits; 613 = custom-audience rate limit;
# 80000-80014 = business-use-case (ads) rate limits.
_RATE_LIMIT_CODES = {4, 17, 32, 341, 613} | set(range(80000, 80015))
_PERMISSION_CODES = {10, 200, 272, 3}
_AUTH_CODES = {190, 102, 463, 467}


class MetaApiError(AdsApiError):
    """A structured Meta Graph API error (or transport failure)."""

    platform = "meta"

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        code: int | None = None,
        subcode: int | None = None,
        fbtrace_id: str | None = None,
        error_type: str | None = None,
        user_message: str | None = None,
        retryable: bool = False,
    ):
        super().__init__(
            message, http_status=http_status, code=code, subcode=subcode,
            request_id=fbtrace_id, error_type=error_type, user_message=user_message,
            retryable=retryable,
        )
        # keep the Meta-native alias for existing callers/tests
        self.fbtrace_id = fbtrace_id

    def error_code(self) -> str:
        if self.error_type == "AUTH" or self.code in _AUTH_CODES:
            return "AUTHENTICATION_FAILED"
        if self.code in _PERMISSION_CODES:
            return "PERMISSION_DENIED"
        if self.retryable and (self.http_status or 0) < 500 and self.code is not None:
            return "RATE_LIMITED"
        if self.code == 803 or self.subcode == 33:
            return "ENTITY_NOT_FOUND"
        if self.code == 100:
            return "INVALID_ARGUMENT"
        if (self.http_status or 0) >= 500:
            return "PLATFORM_UNAVAILABLE"
        if self.code is not None:
            return "VALIDATION_FAILED"
        return "INTERNAL_ERROR"


class MetaAdsClient:
    """Thin async transport over graph.facebook.com/{version}."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._host = settings.meta_graph_base_url
        self._client = httpx.AsyncClient(timeout=settings.meta_http_timeout_seconds)

    # ---- auth ----

    def _auth_headers(self, access_token: str) -> dict[str, str]:
        """Token goes in the Authorization header, NOT the query string, so it
        never lands in httpx request logs / URLs."""
        if not access_token:
            raise MetaApiError(
                "No Meta access token resolved for this call", error_type="AUTH"
            )
        return {"Authorization": f"Bearer {access_token}"}

    def _proof_params(self, access_token: str) -> dict[str, str]:
        """appsecret_proof is a derived HMAC (safe to send as a param); it is not
        the token itself."""
        secret = self._settings.meta_app_secret
        if not secret:
            return {}
        return {
            "appsecret_proof": hmac.new(
                secret.encode(), access_token.encode(), hashlib.sha256
            ).hexdigest()
        }

    # ---- error handling ----

    @staticmethod
    def _raise_for_error(resp: httpx.Response) -> dict:
        """Parse a Graph API response; raise MetaApiError on an error body or
        non-2xx status, else return the JSON dict."""
        try:
            body = resp.json()
        except ValueError:
            if resp.status_code >= 300:
                raise MetaApiError(
                    f"Meta API HTTP {resp.status_code}: {resp.text[:300]}",
                    http_status=resp.status_code,
                    retryable=resp.status_code >= 500,
                )
            return {"raw": resp.text[:1000]}
        if isinstance(body, dict) and "error" in body:
            err = body["error"] or {}
            code = err.get("code")
            retryable = code in _RATE_LIMIT_CODES or resp.status_code >= 500
            raise MetaApiError(
                err.get("message", "Meta API error"),
                http_status=resp.status_code,
                code=code,
                subcode=err.get("error_subcode"),
                fbtrace_id=err.get("fbtrace_id"),
                error_type=err.get("type"),
                user_message=err.get("error_user_msg") or err.get("error_user_title"),
                retryable=retryable,
            )
        if resp.status_code >= 300:
            raise MetaApiError(
                f"Meta API HTTP {resp.status_code}",
                http_status=resp.status_code,
                retryable=resp.status_code >= 500,
            )
        return body

    # ---- core request with retry/backoff ----

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        api_version: str | None = None,
        params: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
    ) -> dict:
        version = api_version or self._settings.meta_api_version
        url = f"{self._host}/{version}/{path.lstrip('/')}"
        headers = self._auth_headers(access_token)
        query = {**(params or {}), **self._proof_params(access_token)}
        attempts = self._settings.meta_max_retries + 1
        last_exc: MetaApiError | None = None
        for attempt in range(attempts):
            try:
                resp = await self._client.request(
                    method, url, params=query, data=data, files=files, headers=headers
                )
                return self._raise_for_error(resp)
            except MetaApiError as exc:
                last_exc = exc
                if not exc.retryable or attempt == attempts - 1:
                    raise
                logger.warning(
                    "meta_api_retry",
                    path=path,
                    attempt=attempt + 1,
                    code=exc.code,
                    status=exc.http_status,
                )
            except httpx.TransportError as exc:
                last_exc = MetaApiError(f"Meta API transport error: {exc}", retryable=True)
                if attempt == attempts - 1:
                    raise last_exc from exc
                logger.warning("meta_api_transport_retry", path=path, attempt=attempt + 1)
            await asyncio.sleep(min(2.0 * (2**attempt), 8.0))
        assert last_exc is not None  # loop always raises on the final attempt
        raise last_exc

    # ---- public verbs ----

    async def get(
        self, path: str, params: dict | None = None, *,
        access_token: str, api_version: str | None = None,
    ) -> dict:
        return await self._request(
            "GET", path, params=params, access_token=access_token, api_version=api_version
        )

    async def post(
        self, path: str, data: dict | None = None, *,
        access_token: str, api_version: str | None = None,
    ) -> dict:
        return await self._request(
            "POST", path, data=data, access_token=access_token, api_version=api_version
        )

    async def post_multipart(
        self, path: str, data: dict | None = None, files: dict | None = None, *,
        access_token: str, api_version: str | None = None,
    ) -> dict:
        return await self._request(
            "POST", path, data=data, files=files,
            access_token=access_token, api_version=api_version,
        )

    async def paginate(
        self,
        path: str,
        params: dict | None = None,
        *,
        access_token: str,
        api_version: str | None = None,
        limit: int | None = None,
        after: str | None = None,
    ) -> dict:
        """One page of an edge. Returns {"data": [...], "after": <cursor|None>,
        "has_next": bool}. Callers pass the returned ``after`` back to advance."""
        q = dict(params or {})
        if limit is not None:
            q["limit"] = limit
        if after:
            q["after"] = after
        body = await self.get(path, q, access_token=access_token, api_version=api_version)
        data = body.get("data", []) if isinstance(body, dict) else []
        paging = body.get("paging", {}) if isinstance(body, dict) else {}
        cursors = paging.get("cursors", {}) if isinstance(paging, dict) else {}
        next_after = cursors.get("after") if paging.get("next") else None
        return {"data": data, "after": next_after, "has_next": bool(paging.get("next"))}

    async def aclose(self) -> None:
        await self._client.aclose()
