"""PDP / listing catalogue entries carry the agent policy: a direction for every metric, copied from catalogue_src."""

import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
PDP_VIEWS = {"pdp_visits", "pdp_section_reach", "pdp_interaction_log", "listing_card_events"}


def _load(p):
    return yaml.safe_load((ROOT / p).read_text())


def pdp_metrics(metrics_dir=ROOT / "catalogue" / "metrics"):
    out = {}
    for f in sorted(metrics_dir.glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        if (d.get("cube_mapping") or {}).get("view") in PDP_VIEWS:
            out[d["id"]] = d
    return out


def policy_problems(metrics, policy):
    """Metrics without a direction, or whose file disagrees with agent_policy.yaml (the hand-authored source)."""
    bad = []
    for mid, d in metrics.items():
        want = (policy.get(mid) or {}).get("direction_bad")
        if want is None:
            bad.append(f"{mid}: no direction_bad in agent_policy.yaml")
        elif d.get("direction_bad") != want:
            bad.append(f"{mid}: file says {d.get('direction_bad')!r}, policy says {want!r}")
    return bad


def test_every_pdp_metric_has_a_direction_and_the_file_matches_policy():
    metrics = pdp_metrics()
    assert len(metrics) >= 24
    assert policy_problems(metrics, _load("catalogue_src/agent_policy.yaml")["metrics"]) == []


def test_policy_check_catches_a_missing_and_a_flipped_direction():
    metrics = {"a": {"id": "a", "direction_bad": "up"}, "b": {"id": "b", "direction_bad": "down"}, "c": {"id": "c"}}
    policy = {"a": {"direction_bad": "down"}, "b": {"direction_bad": "down"}}
    assert policy_problems(metrics, policy) == ["a: file says 'up', policy says 'down'", "c: no direction_bad in agent_policy.yaml"]


def test_policy_has_no_entry_for_a_pdp_metric_that_does_not_exist():
    policy = _load("catalogue_src/agent_policy.yaml")["metrics"]
    ids = {p.stem for p in (ROOT / "catalogue" / "metrics").glob("*.yaml")}
    assert [m for m in policy if m not in ids] == []


@pytest.mark.parametrize("view", sorted(PDP_VIEWS))
def test_pdp_views_have_the_web_layer_tier_and_a_catalogue_entry(view):
    tiers = _load("catalogue_src/causal_priors.yaml")["view_tiers"]
    assert tiers[view] == tiers["web_sessions"]
    assert view in {v["name"] for v in _load("catalogue/views.yaml")["views"]}
