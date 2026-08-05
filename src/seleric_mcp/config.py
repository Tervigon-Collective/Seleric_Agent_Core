"""Application configuration.

Non-secret settings live in config.yaml (project root). Secrets and optional
overrides live in .env / .env.local. Load order:

  1. config.yaml defaults
  2. .env then .env.local (secrets + any key override)

Nothing from Settings is ever placed in a tool response.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def cube_model_dir() -> Path:
    """Canonical Cube model directory. The model moved from Base_Agent/cube/model
    (now a stub README) to data_platform/mage-ai/infra/cube/model — the compose
    file mounts that path into the Cube container. Override with CUBE_MODEL_DIR;
    falls back to the legacy in-repo path if the canonical one is absent."""
    env = os.getenv("CUBE_MODEL_DIR", "").strip()
    if env:
        return Path(env)
    for candidate in (
        PROJECT_ROOT.parent / "mage-ai" / "infra" / "cube" / "model",
        PROJECT_ROOT.parent / "data_platform" / "mage-ai" / "infra" / "cube" / "model",
    ):
        if candidate.is_dir():
            return candidate
    return PROJECT_ROOT / "cube" / "model"


def _load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PROJECT_ROOT / ".env.local", override=True)


def _load_yaml_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path} must be a mapping at the top level")
    return doc


def _section(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    raw = cfg.get(name) or {}
    return raw if isinstance(raw, dict) else {}


def _cfg_get(section: dict[str, Any], key: str, default: Any) -> Any:
    if key not in section or section[key] is None:
        return default
    return section[key]


def _env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip()


def _env_or(name: str, fallback: Any) -> Any:
    """Return env string if set (non-empty after strip for strings), else fallback."""
    raw = os.getenv(name)
    if raw is None:
        return fallback
    stripped = raw.strip()
    return stripped if stripped != "" else fallback


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    cube_api_url: str
    seleric_api_key: str
    cubejs_api_secret: str
    pipeboard_mcp_url: str
    pipeboard_token: str
    write_enabled: bool
    mcp_service_token: str
    approval_secret: str
    caller_scopes: frozenset[str]
    db_path: Path
    # Optional deployment-level module pin (catalogue/modules.yaml id). When set,
    # this instance is locked to that module: query/search tools scope to it and
    # a per-call module that contradicts it is refused. Empty = unscoped.
    module: str = ""
    catalogue_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "catalogue")

    # --- behavior tunables ---
    resolve_auto_threshold: float = 0.85
    resolve_ambiguous_threshold: float = 0.60
    resolve_runner_up_margin: float = 0.05
    default_row_limit: int = 0
    max_row_limit: int = 100_000
    cube_timeout_seconds: float = 30.0
    result_ttl_minutes: int = 60
    idempotency_window_hours: int = 24
    top_movers_limit: int = 10
    anomaly_sigma: float = 3.0
    anomaly_min_points: int = 14
    freshness_cache_ttl_seconds: int = 600
    freshness_enforcement: bool = True
    freshness_grace_days: int = 1
    default_brand_id: str = "20"

    # --- Meta Marketing Graph API (direct read/write backend for ads/meta/) ---
    meta_access_token: str = ""
    meta_app_secret: str = ""
    meta_api_version: str = "v21.0"
    meta_graph_base_url: str = "https://graph.facebook.com"
    meta_max_budget_minor: int = 0
    meta_http_timeout_seconds: float = 30.0
    meta_max_retries: int = 2
    # Resolved Meta credentials cache TTL (seconds) to avoid a DB hit per call.
    meta_cred_cache_ttl_seconds: int = 300

    # --- Google Ads API (shared app creds from env; per-tenant refresh_token
    #     + customer_id from core.brand_envs) ---
    google_developer_token: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_login_customer_id: str = ""
    google_api_version: str = "v21"
    google_max_budget_micros: int = 0
    google_cred_cache_ttl_seconds: int = 300

    # --- Postgres (core.brand_envs multi-tenant ad credentials) ---
    # Mirrors mage-ai's DSN resolution: PG_DSN wins, else PG*/POSTGRES_* parts.
    pg_dsn: str = ""
    pg_host: str = ""
    pg_port: str = "5432"
    pg_dbname: str = ""
    pg_user: str = ""
    pg_password: str = ""
    pg_schema: str = "core"
    pg_connect_timeout: int = 8


@dataclass(frozen=True)
class AzureSettings:
    """Chat-client Azure OpenAI settings. API key is always from env."""

    api_key: str
    endpoint: str
    deployment: str
    api_version: str = "2024-12-01-preview"


@dataclass(frozen=True)
class ChatSettings:
    max_tool_rounds: int = 1100
    web_port: int = 8766
    tool_preview_chars: int = 4000


@dataclass(frozen=True)
class LLMSettings:
    """Task-tier model routing + per-model rate limits for the chat clients.

    ``tiers`` maps a task tier ("tools", "reasoning") to the ordered list of
    models to try; ``fallback`` is the last-resort model for any tier. Limits
    are per-model (each model gets its own independent bucket); ``model_limits``
    overrides the default for a specific model.

    ``max_wait_rounds`` controls how long to wait when all models are throttled:
    0 = wait indefinitely, >0 = max retry rounds before raising RateLimitExceeded.
    ``max_sleep_seconds`` caps the sleep duration per wait round.
    """

    tiers: dict[str, tuple[str, ...]]
    fallback: str
    requests_per_minute: float = 20.0
    tokens_per_minute: float = 20000.0
    completion_reserve: int = 1024
    max_wait_rounds: int = 0  # 0 = unlimited waiting
    max_sleep_seconds: float = 60.0
    model_limits: dict[str, tuple[float, float]] = field(default_factory=dict)


def load_settings(config_path: Path | None = None) -> Settings:
    _load_env()
    cfg = _load_yaml_config(config_path or CONFIG_PATH)
    cube = _section(cfg, "cube")
    pipeboard = _section(cfg, "pipeboard")
    meta = _section(cfg, "meta")
    meta_write_policies = _section(meta, "write_policies")
    google = _section(cfg, "google")
    postgres = _section(cfg, "postgres")
    gateway = _section(cfg, "gateway")
    storage = _section(cfg, "storage")
    defaults = _section(cfg, "defaults")
    tunables = _section(cfg, "tunables")
    field_defaults = Settings.__dataclass_fields__

    db_raw = str(
        _env_or("SELERIC_MCP_DB", _cfg_get(storage, "db_path", "var/seleric_mcp.db"))
    )
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    scopes_cfg = _cfg_get(gateway, "scopes", ["metrics:read"])
    if isinstance(scopes_cfg, str):
        scopes_default = scopes_cfg
    else:
        scopes_default = ",".join(str(s) for s in scopes_cfg)
    scopes = frozenset(
        s.strip()
        for s in str(_env_or("SELERIC_MCP_SCOPES", scopes_default)).split(",")
        if s.strip()
    )

    module = str(_env_or("SELERIC_MCP_MODULE", _cfg_get(gateway, "module", ""))).strip()

    write_cfg = _as_bool(_cfg_get(gateway, "write_enabled", False), False)
    write_env = os.getenv("WRITE_ENABLED")
    if write_env is not None and write_env.strip() != "":
        write_enabled = write_env.strip() == "true"
    else:
        write_enabled = write_cfg

    def _tunable_float(env_name: str, yaml_key: str) -> float:
        default = field_defaults[yaml_key].default
        return _as_float(
            _env_or(env_name, _cfg_get(tunables, yaml_key, default)),
            default,
        )

    def _tunable_int(env_name: str, yaml_key: str) -> int:
        default = field_defaults[yaml_key].default
        return _as_int(
            _env_or(env_name, _cfg_get(tunables, yaml_key, default)),
            default,
        )

    freshness_enforcement_cfg = _as_bool(
        _cfg_get(tunables, "freshness_enforcement", True), True
    )
    freshness_env = os.getenv("SELERIC_FRESHNESS_ENFORCEMENT")
    if freshness_env is not None and freshness_env.strip() != "":
        freshness_enforcement = freshness_env.strip().lower() not in ("0", "false", "no")
    else:
        freshness_enforcement = freshness_enforcement_cfg

    return Settings(
        cube_api_url=str(
            _env_or("CUBE_API_URL", _cfg_get(cube, "api_url", "http://127.0.0.1:4001"))
        ).rstrip("/"),
        seleric_api_key=_env_str("SELERIC_API_KEY"),
        cubejs_api_secret=_env_str("CUBEJS_API_SECRET"),
        pipeboard_mcp_url=str(
            _env_or(
                "PIPEBOARD_MCP_URL",
                _cfg_get(pipeboard, "mcp_url", "https://meta-ads.mcp.pipeboard.co"),
            )
        ).rstrip("/"),
        pipeboard_token=_env_str("PIPEBOARD_TOKEN"),
        meta_access_token=_env_str("META_ACCESS_TOKEN"),
        meta_app_secret=_env_str("META_APP_SECRET"),
        meta_api_version=str(
            _env_or("META_API_VERSION", _cfg_get(meta, "api_version", "v21.0"))
        ),
        meta_graph_base_url=str(
            _env_or(
                "META_GRAPH_BASE_URL",
                _cfg_get(meta, "graph_base_url", "https://graph.facebook.com"),
            )
        ).rstrip("/"),
        meta_max_budget_minor=_as_int(
            _env_or(
                "META_MAX_BUDGET_MINOR",
                _cfg_get(meta_write_policies, "maximum_budget_minor", 0),
            ),
            0,
        ),
        meta_http_timeout_seconds=_as_float(
            _env_or("META_HTTP_TIMEOUT_S", _cfg_get(meta, "http_timeout_seconds", 30.0)),
            30.0,
        ),
        meta_max_retries=_as_int(
            _env_or("META_MAX_RETRIES", _cfg_get(meta, "max_retries", 2)), 2
        ),
        meta_cred_cache_ttl_seconds=_as_int(
            _env_or("META_CRED_CACHE_TTL_S", _cfg_get(meta, "cred_cache_ttl_seconds", 300)), 300
        ),
        google_developer_token=_env_str("GOOGLE_ADS_DEVELOPER_TOKEN"),
        google_client_id=_env_str("GOOGLE_ADS_CLIENT_ID"),
        google_client_secret=_env_str("GOOGLE_ADS_CLIENT_SECRET"),
        google_login_customer_id=_env_str("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
        google_api_version=str(
            _env_or("GOOGLE_ADS_API_VERSION", _cfg_get(google, "api_version", "v21"))
        ),
        google_max_budget_micros=_as_int(
            _env_or(
                "GOOGLE_MAX_BUDGET_MICROS",
                _cfg_get(_section(google, "write_policies"), "maximum_budget_micros", 0),
            ),
            0,
        ),
        google_cred_cache_ttl_seconds=_as_int(
            _env_or("GOOGLE_CRED_CACHE_TTL_S", _cfg_get(google, "cred_cache_ttl_seconds", 300)), 300
        ),
        pg_dsn=_env_str("PG_DSN"),
        pg_host=str(_env_or("PGHOST", _env_or("POSTGRES_HOST", _cfg_get(postgres, "host", "")))),
        pg_port=str(_env_or("PGPORT", _env_or("POSTGRES_PORT", _cfg_get(postgres, "port", "5432")))),
        pg_dbname=str(_env_or("PGDATABASE", _env_or("POSTGRES_DBNAME", _cfg_get(postgres, "dbname", "")))),
        pg_user=str(_env_or("PGUSER", _env_or("POSTGRES_USER", _cfg_get(postgres, "user", "")))),
        pg_password=_env_or("PGPASSWORD", _env_str("POSTGRES_PASSWORD")),
        pg_schema=str(_cfg_get(postgres, "schema", "core")),
        pg_connect_timeout=_as_int(
            _env_or("PG_CONNECT_TIMEOUT", _cfg_get(postgres, "connect_timeout", 8)), 8
        ),
        write_enabled=write_enabled,
        mcp_service_token=_env_str("MCP_SERVICE_TOKEN"),
        approval_secret=_env_str("APPROVAL_SECRET"),
        caller_scopes=scopes,
        module=module,
        db_path=db_path,
        resolve_auto_threshold=_tunable_float(
            "SELERIC_RESOLVE_AUTO_THRESHOLD", "resolve_auto_threshold"
        ),
        resolve_ambiguous_threshold=_tunable_float(
            "SELERIC_RESOLVE_AMBIGUOUS_THRESHOLD", "resolve_ambiguous_threshold"
        ),
        resolve_runner_up_margin=_tunable_float(
            "SELERIC_RESOLVE_RUNNER_UP_MARGIN", "resolve_runner_up_margin"
        ),
        default_row_limit=_tunable_int("SELERIC_DEFAULT_ROW_LIMIT", "default_row_limit"),
        max_row_limit=_tunable_int("SELERIC_MAX_ROW_LIMIT", "max_row_limit"),
        cube_timeout_seconds=_tunable_float("SELERIC_CUBE_TIMEOUT_S", "cube_timeout_seconds"),
        result_ttl_minutes=_tunable_int("SELERIC_RESULT_TTL_MINUTES", "result_ttl_minutes"),
        idempotency_window_hours=_tunable_int(
            "SELERIC_IDEMPOTENCY_WINDOW_HOURS", "idempotency_window_hours"
        ),
        top_movers_limit=_tunable_int("SELERIC_TOP_MOVERS_LIMIT", "top_movers_limit"),
        anomaly_sigma=_tunable_float("SELERIC_ANOMALY_SIGMA", "anomaly_sigma"),
        anomaly_min_points=_tunable_int("SELERIC_ANOMALY_MIN_POINTS", "anomaly_min_points"),
        freshness_cache_ttl_seconds=_tunable_int(
            "SELERIC_FRESHNESS_CACHE_TTL_S", "freshness_cache_ttl_seconds"
        ),
        freshness_enforcement=freshness_enforcement,
        freshness_grace_days=_tunable_int(
            "SELERIC_FRESHNESS_GRACE_DAYS", "freshness_grace_days"
        ),
        default_brand_id=str(
            _env_or(
                "SELERIC_DEFAULT_BRAND_ID",
                _cfg_get(defaults, "brand_id", field_defaults["default_brand_id"].default),
            )
        ),
    )


def load_azure_settings(config_path: Path | None = None) -> AzureSettings:
    _load_env()
    azure = _section(_load_yaml_config(config_path or CONFIG_PATH), "azure")
    api_key = _env_str("AZURE_OPENAI_API_KEY")
    endpoint = str(
        _env_or("AZURE_OPENAI_ENDPOINT", _cfg_get(azure, "endpoint", ""))
    ).rstrip("/")
    deployment = str(_env_or("AZURE_DEPLOYMENT", _cfg_get(azure, "deployment", "")))
    api_version = str(
        _env_or(
            "AZURE_OPENAI_API_VERSION",
            _cfg_get(azure, "api_version", "2024-12-01-preview"),
        )
    )
    return AzureSettings(
        api_key=api_key,
        endpoint=endpoint,
        deployment=deployment,
        api_version=api_version,
    )


def load_llm_settings(config_path: Path | None = None) -> LLMSettings:
    """Load the `llm` section of config.yaml (task-tier routing + rate limits).

    Backward-compatible: if no `llm` section exists, both tiers route to the
    single `azure.deployment` model so existing single-model setups keep
    working with rate limiting applied.
    """
    _load_env()
    cfg = _load_yaml_config(config_path or CONFIG_PATH)
    llm = _section(cfg, "llm")

    fallback_default = load_azure_settings(config_path).deployment

    tiers_cfg = llm.get("tiers")
    tiers: dict[str, tuple[str, ...]] = {}
    if isinstance(tiers_cfg, dict):
        for tier, models in tiers_cfg.items():
            if isinstance(models, str):
                models = [models]
            names = tuple(str(m).strip() for m in (models or []) if str(m).strip())
            if names:
                tiers[str(tier)] = names
    if not tiers:
        # No routing configured — mirror the legacy single deployment.
        single = fallback_default or ""
        tiers = {"tools": (single,), "reasoning": (single,)}

    fallback = str(_cfg_get(llm, "fallback", "") or "").strip() or fallback_default

    limits = _section(llm, "limits")
    rpm = _as_float(_cfg_get(limits, "requests_per_minute", 20), 20.0)
    tpm = _as_float(_cfg_get(limits, "tokens_per_minute", 20000), 20000.0)
    completion_reserve = _as_int(_cfg_get(llm, "completion_reserve", 1024), 1024)

    # Wait behavior: 0 = unlimited waiting, >0 = max retry rounds
    max_wait_rounds = _as_int(_cfg_get(llm, "max_wait_rounds", 0), 0)
    max_sleep_seconds = _as_float(_cfg_get(llm, "max_sleep_seconds", 60.0), 60.0)

    model_limits: dict[str, tuple[float, float]] = {}
    per_model = _cfg_get(llm, "model_limits", {})
    if isinstance(per_model, dict):
        for name, spec in per_model.items():
            spec = spec or {}
            model_limits[str(name)] = (
                _as_float(spec.get("requests_per_minute"), rpm),
                _as_float(spec.get("tokens_per_minute"), tpm),
            )

    return LLMSettings(
        tiers=tiers,
        fallback=fallback,
        requests_per_minute=rpm,
        tokens_per_minute=tpm,
        completion_reserve=completion_reserve,
        max_wait_rounds=max_wait_rounds,
        max_sleep_seconds=max_sleep_seconds,
        model_limits=model_limits,
    )


def load_chat_settings(config_path: Path | None = None) -> ChatSettings:
    _load_env()
    chat = _section(_load_yaml_config(config_path or CONFIG_PATH), "chat")
    return ChatSettings(
        max_tool_rounds=_as_int(
            _env_or("CHAT_MAX_TOOL_ROUNDS", _cfg_get(chat, "max_tool_rounds", 1100)),
            1100,
        ),
        web_port=_as_int(
            _env_or("CHAT_WEB_PORT", _cfg_get(chat, "web_port", 8766)),
            8766,
        ),
        tool_preview_chars=_as_int(
            _env_or(
                "CHAT_WEB_TOOL_PREVIEW_CHARS",
                _cfg_get(chat, "tool_preview_chars", 4000),
            ),
            4000,
        ),
    )
