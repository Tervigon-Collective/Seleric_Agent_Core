"""Tests for the dashboard analyst persona prompt (gateway/prompts.py) and its
registration as the `dashboard_analyst` MCP prompt (gateway/server.py). The
dashboard's in-page chat fetches this prompt, so it must exist, carry the
no-hallucination guard, and interpolate the current module/brand scope.
"""

from __future__ import annotations

from seleric_mcp.gateway import prompts as prompt_templates
from seleric_mcp.gateway.server import build_server


def test_dashboard_analyst_prompt_composes_guard_persona_and_scope():
    out = prompt_templates.dashboard_analyst_prompt(
        module_label="Funnel Analytics", brand_label="Tilting Heads"
    )
    assert out.strip()
    # Reuses the standing no-hallucination guard verbatim.
    assert "Never invent numbers" in out
    # Layers the conversational BI persona.
    assert "CONVERSATIONAL BI ANALYST" in out
    assert "chain of thought" in out
    # Interpolates the current scope.
    assert "Funnel Analytics" in out
    assert "Tilting Heads" in out
    assert "item_count" in out
    assert "greater_than" in out


def test_dashboard_analyst_prompt_blank_scope_falls_back():
    out = prompt_templates.dashboard_analyst_prompt()
    assert "the current dashboard module" in out
    assert "the current brand" in out


def test_dashboard_analyst_prompt_is_registered(settings):
    mcp = build_server(settings)
    prompt = mcp._prompt_manager.get_prompt("dashboard_analyst")
    assert prompt is not None
    # Renders with a known module id resolved to its display name.
    rendered = prompt.fn(module="webanalytics", brand_id="20")
    assert "CONVERSATIONAL BI ANALYST" in rendered
    assert "Funnel Analytics" in rendered
