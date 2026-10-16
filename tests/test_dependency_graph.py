"""The metric dependency graph builds clean and keeps its invariants (scripts/build_dependency_graph.py)."""

import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built():
    spec = importlib.util.spec_from_file_location("bdg", ROOT / "scripts" / "build_dependency_graph.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    g, where = mod.build()
    return mod, g, mod.to_json(g, where)


def test_builds_without_errors(built):
    _, g, _ = built
    assert g.errors == []


def test_every_metric_is_a_node(built):
    _, g, _ = built
    assert len(g.nodes) == len(list((ROOT / "catalogue_v2" / "metrics").glob("*.yaml")))


def test_compositions_join_additive_metrics(built):
    _, g, _ = built
    for e in g.edges:
        if e["kind"] == "composition":
            assert g.nodes[e["from"]]["aggregation"] == "additive", e
            assert g.nodes[e["to"]]["aggregation"] == "additive", e


def test_no_causal_edge_inside_a_same_quantity_group(built):
    _, g, data = built
    group = {n["id"]: set(n["same_quantity_group"]) for n in data["nodes"]}
    for e in g.edges:
        if e["kind"] in ("mechanism", "feedback"):
            assert e["to"] not in group[e["from"]], e


def test_allocated_metrics_never_drive(built):
    _, g, _ = built
    for e in g.edges:
        if e["kind"] in ("mechanism", "feedback"):
            assert not g.nodes[e["from"]]["allocated"] and not g.nodes[e["to"]]["allocated"], e


def test_mechanism_rates_are_ratio_metrics(built):
    _, g, _ = built
    for e in g.edges:
        if e.get("rate"):
            assert g.nodes[e["rate"]]["aggregation"] == "ratio", e


def test_committed_json_is_current(built):
    _, _, data = built
    committed = json.loads((ROOT / "doc" / "dependency_graph" / "dependency_graph.json").read_text())
    assert committed == json.loads(json.dumps(data, ensure_ascii=False)), "re-run scripts/build_dependency_graph.py"
