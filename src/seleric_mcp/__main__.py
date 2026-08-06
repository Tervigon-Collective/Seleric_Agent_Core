"""CLI entrypoint.

  seleric-mcp --transport stdio            # local IDE agents (Claude Code, Cursor)
  seleric-mcp --transport http --port 8765 # remote hosts (Claude.ai, ChatGPT)
  seleric-mcp --transport http --reload    # local HTTP with auto-reload

Both transports register the identical tool surface.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

import structlog

from .config import load_settings
from .observability.logging import configure_logging

_ROOT = Path(__file__).resolve().parents[2]
_RELOAD_DIRS = [
    str(_ROOT / "src"),
    str(_ROOT / "catalogue"),
    str(_ROOT / "scripts"),
    str(_ROOT),
]
_RELOAD_EXCLUDES = [
    ".venv",
    "var",
    "logs",
    ".pytest_cache",
    ".git",
    "**/__pycache__",
]


def create_http_app():
    """Uvicorn factory — rebuilt on each --reload so catalogue/code edits apply."""
    configure_logging(logging.INFO)
    settings = load_settings()

    from .gateway.auth import BearerTokenMiddleware
    from .gateway.server import build_server

    if not settings.mcp_service_token:
        raise SystemExit(
            "MCP_SERVICE_TOKEN must be set for the http transport "
            "(stdio is the only unauthenticated mode)."
        )

    mcp = build_server(settings)
    skip_drift = os.environ.get("SELERIC_SKIP_DRIFT_CHECK", "0") == "1"
    if not skip_drift:
        from .catalogue_service.validate import validate_against_cube
        from .semantic_layer.cube_client import CubeClient

        async def _drift_check() -> dict:
            tmp = CubeClient(settings)
            try:
                return await validate_against_cube(mcp._seleric_ctx.catalogue, tmp)  # type: ignore[attr-defined]
            finally:
                await tmp.aclose()

        drift = asyncio.run(_drift_check())
        structlog.get_logger().info("catalogue_drift_check", **drift)

    app = mcp.streamable_http_app()

    # Mount the dashboard BI-analyst agent (/agent/ask, /agent/health) onto the
    # same app so it shares the bearer-token middleware. The agent loop, session
    # memory and scope enforcement live in Base_Agent — the dashboard proxies here.
    from .gateway.analyst import mount_analyst_routes

    mount_analyst_routes(app, mcp)
    return BearerTokenMiddleware(app, settings.mcp_service_token)


def main() -> None:
    parser = argparse.ArgumentParser(prog="seleric-mcp")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--skip-drift-check", action="store_true")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="HTTP only: auto-reload on src/catalogue/scripts/config changes",
    )
    args = parser.parse_args()

    configure_logging(logging.INFO)
    logger = structlog.get_logger()
    settings = load_settings()

    from .gateway.server import build_server

    if args.transport == "stdio":
        mcp = build_server(settings)
        ctx = mcp._seleric_ctx  # type: ignore[attr-defined]

        if not args.skip_drift_check:
            from .catalogue_service.validate import validate_against_cube
            from .semantic_layer.cube_client import CubeClient

            async def _drift_check() -> dict:
                tmp = CubeClient(settings)
                try:
                    return await validate_against_cube(ctx.catalogue, tmp)
                finally:
                    await tmp.aclose()

            drift = asyncio.run(_drift_check())
            logger.info("catalogue_drift_check", **drift)

        mcp.run(transport="stdio")
        return

    import uvicorn

    os.environ["SELERIC_SKIP_DRIFT_CHECK"] = "1" if args.skip_drift_check else "0"

    if args.reload:
        # Factory + reload so catalogue YAML and Python edits restart the process.
        if args.skip_drift_check:
            # Drift on every file-save is slow; keep skip across reloader children.
            os.environ["SELERIC_SKIP_DRIFT_CHECK"] = "1"
        uvicorn.run(
            "seleric_mcp.__main__:create_http_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=True,
            reload_dirs=_RELOAD_DIRS,
            reload_excludes=_RELOAD_EXCLUDES,
            reload_includes=["*.py", "*.yaml", "*.yml", "*.md", "config.yaml", ".env", ".env.local"],
        )
        return

    # Non-reload path (production / one-shot): build once in-process.
    app = create_http_app()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
