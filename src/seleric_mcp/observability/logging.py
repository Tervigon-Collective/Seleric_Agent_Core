"""Structured JSON logging. Every tool call gets a trace_id bound to the
logger context so downstream log lines correlate.

IMPORTANT for stdio transport: logs must go to stderr — stdout is the MCP
protocol channel.
"""

from __future__ import annotations

import logging
import sys
import uuid

import structlog


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(stream=sys.stderr, level=level, format="%(message)s")
    # httpx logs the full request URL at INFO; keep it at WARNING so query
    # params (e.g. appsecret_proof) never land in logs. Bearer tokens are sent
    # as headers (see ads/meta/client.py), which httpx does not log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
        cache_logger_on_first_use=True,
    )


def new_trace_id() -> str:
    return "tr_" + uuid.uuid4().hex[:12]


def log_call(tool: str, **fields: object) -> str:
    """Module-level tool-call logger (mirrors the ``_log_call`` closure in
    gateway/server.py) so tool modules defined outside ``build_server`` can log
    consistently. Returns the generated trace_id."""
    trace_id = new_trace_id()
    structlog.get_logger().info("tool_call", tool=tool, trace_id=trace_id, **fields)
    return trace_id
