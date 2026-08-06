"""Unit checks for the dashboard analyst service's pure helpers: rich-block
parsing (chart/table/choices) and server-side scope injection. The LLM loop
itself needs Azure + Cube, so it is exercised via the live smoke suite, not here.
"""

from seleric_mcp.gateway import analyst as A


def test_parse_rich_blocks_strips_chart_and_table():
    raw = (
        "Net revenue up.\n\n"
        "```chart\n"
        '{"type":"line","x":"date","series":[{"key":"nr","label":"NR"}],'
        '"rows":[{"date":"2026-07-01","nr":100}]}\n'
        "```\n\n"
        "```table\n"
        '{"columns":[{"key":"c","label":"Ch"},{"key":"nr","format":"inr","align":"right"}],'
        '"rows":[{"c":"ig","nr":100}],"primary":"nr"}\n'
        "```\n"
        "Period: last 7d"
    )
    out = A.parse_rich_blocks(raw)
    assert out["chart"]["type"] == "line"
    assert out["table"]["primary"] == "nr"
    assert "```" not in out["answer"]
    assert "Net revenue up." in out["answer"]


def test_malformed_block_is_stripped_not_rendered():
    raw = "Answer.\n```table\n{not valid json}\n```"
    out = A.parse_rich_blocks(raw)
    assert "table" not in out
    assert "```" not in out["answer"]


def test_inject_scope_forces_module_and_brand_filter():
    a = A.inject_scope("metrics_query", {"measures": ["x"]}, module="commerce", brand_id="20")
    assert a["module"] == "commerce"
    assert any(f["dimension"] == "brand_id" for f in a["filters"])

    d = A.inject_scope("metrics_drilldown", {}, module="commerce", brand_id="20")
    assert any(f["dimension"] == "brand_id" for f in d["additional_filters"])

    # brand filter is not duplicated when already present
    a2 = A.inject_scope(
        "metrics_query",
        {"measures": ["x"], "filters": [{"dimension": "brand_id", "operator": "equals", "values": ["7"]}]},
        module="commerce",
        brand_id="20",
    )
    assert sum(1 for f in a2["filters"] if f["dimension"] == "brand_id") == 1


def test_choices_capped_and_cleaned():
    _, choices = A._extract_choices(
        'Pick.\n```choices\n{"options":[{"label":"A","value":"do A"},{"label":"B"}]}\n```'
    )
    assert choices == [{"label": "A", "value": "do A"}, {"label": "B", "value": "B"}]
