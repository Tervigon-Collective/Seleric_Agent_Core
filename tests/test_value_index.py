"""Value resolution learned from Cube (catalogue_service/value_index.py).

The index must resolve a user's word to the value(s) the data actually uses —
without any value, dimension or view named in code — and must not drown the
answer in accidental matches.
"""

from __future__ import annotations

import time
from datetime import date

import pytest

from seleric_mcp.catalogue_service.value_index import (
    Snapshot,
    ValueIndex,
    candidate_terms,
    label_values,
    match_value,
    normalize,
)
from seleric_mcp.semantic_layer.cube_client import CubeResult


def test_normalize_decodes_url_encoding_and_punctuation():
    assert normalize("Cross+Sell+-+WA") == "cross sell wa"
    assert normalize("Cross%20Sell%20-%20WA") == "cross sell wa"


@pytest.mark.parametrize(
    ("term", "value", "expected"),
    [
        ("whatsapp", "whatsapp", "exact"),
        ("suspender boots", "pawveralls suspender boots", "token"),
        ("whatsapp", "whatsappblast", "contains"),
        ("suspendr boots", "suspender boots", "fuzzy"),
        ("whatsapp", "wa", "abbreviation"),
        ("months", "mh", "abbreviation"),  # candidate only; resolve() drops it (no full form)
        ("whatsapp", "email", None),
    ],
)
def test_match_value(term, value, expected):
    assert match_value(term, value) == expected


def test_candidate_terms_have_no_stopword_list_only_structural_filters():
    terms = candidate_terms("orders from the whatsapp channel last 3 months", skip=frozenset())
    assert "whatsapp" in terms and "the" in terms  # no hand-written stopwords
    assert "3" not in terms  # numbers are structural, not words
    assert terms.index("from the whatsapp") < terms.index("whatsapp")  # longest first


def test_label_values_filters_ids_per_value_not_per_dimension():
    # utm_medium really mixes labels with Meta ad-set ids written into the tag.
    raw = {"pmax": 10.0, "120248089961790783": 9.0, "whatsapp": 3.0, "true": 1.0, "wa": 1.0}
    assert label_values(raw) == {"pmax": 10.0, "whatsapp": 3.0, "wa": 1.0}


def _index(catalogue) -> ValueIndex:
    return ValueIndex(catalogue, cube=None, default_brand_id="20")  # type: ignore[arg-type]


def _snap(values: dict[tuple[str, str], dict[str, float]]) -> Snapshot:
    return Snapshot(
        brand_id="20",
        start=date(2026, 3, 1),
        end=date(2026, 9, 1),
        built_at=time.time(),
        values=values,
        measure_metric={k: "attributed_orders" for k in values},
    )


def test_resolve_finds_the_value_and_its_abbreviation_in_the_same_dimension(catalogue):
    snap = _snap(
        {
            ("lt_utm_medium", "order_attribution"): {"whatsapp": 33, "wa": 13, "cpc": 500, "email": 40},
            ("shipping_state_code", "order_attribution"): {"MH": 900, "WB": 80, "KA": 70},
        }
    )
    out = _index(catalogue).resolve("how many orders from whatsapp", snap)
    whatsapp = next(t for t in out["terms"] if t["term"] == "whatsapp")
    dims = {d["dimension"]: d for d in whatsapp["dimensions"]}
    assert set(dims) == {"lt_utm_medium"}  # WB/MH-style codes never ride along
    got = {v["value"]: v["match"] for v in dims["lt_utm_medium"]["values"]}
    assert got == {"whatsapp": "exact", "wa": "abbreviation"}
    assert whatsapp["best_match"] == "exact"


def test_abbreviation_without_the_full_form_is_dropped(catalogue):
    snap = _snap({("shipping_state_code", "order_attribution"): {"MH": 900, "MN": 8, "KA": 70}})
    out = _index(catalogue).resolve("orders last months", snap)
    assert out["terms"] == []


def test_catalogue_words_only_match_exact_values(catalogue):
    # "orders" is catalogue vocabulary: a URL containing it is not what was asked.
    snap = _snap({("landing_page_path", "session_funnel"): {"/apps/customer-os/orders": 30, "/cart": 90}})
    out = _index(catalogue).resolve("orders yesterday", snap)
    assert out["terms"] == []


def test_partial_match_needs_activity_in_the_window(catalogue):
    snap = _snap({("landing_page_path", "session_funnel"): {"/collections/give-back": 0, "/cart": 90}})
    out = _index(catalogue).resolve("give me the funnel", snap)
    assert out["terms"] == []


def test_unselective_partial_matches_are_dropped(catalogue):
    # a word inside most of a dimension's values says nothing about which one.
    titles = {f"Boots model {i}": 10 for i in range(20)}
    snap = _snap({("product_title", "product_performance"): titles})
    out = _index(catalogue).resolve("boots sales", snap)
    assert out["terms"] == []


def test_longer_phrase_claims_values_before_its_words(catalogue):
    snap = _snap(
        {("product_title", "product_performance"): {"Pawveralls Suspender Boots": 50, "AirTrek Boots": 5, "Mat": 1, **{f"Toy {i}": 1 for i in range(10)}}}
    )
    out = _index(catalogue).resolve("suspender boots sales", snap)
    phrase = next(t for t in out["terms"] if t["term"] == "suspender boots")
    assert [v["value"] for v in phrase["dimensions"][0]["values"]] == ["Pawveralls Suspender Boots"]
    boots = [t for t in out["terms"] if t["term"] == "boots"]
    assert all(v["value"] != "Pawveralls Suspender Boots" for t in boots for d in t["dimensions"] for v in d["values"])


class _MetaCube:
    """Cube double exposing /meta agg types and grouped rows per member."""

    def __init__(self, rows_by_member: dict[str, list[dict]], agg_types: dict[str, str]):
        self.rows_by_member = rows_by_member
        self.agg_types = agg_types
        self.queries: list[dict] = []

    async def meta(self) -> dict:
        return {"cubes": [{"name": "x", "measures": [{"name": n, "aggType": t} for n, t in self.agg_types.items()]}]}

    async def load(self, query: dict) -> CubeResult:
        self.queries.append(query)
        member = query["dimensions"][0]
        return CubeResult(data=self.rows_by_member.get(member, []), raw={})


async def test_build_counts_volume_with_a_count_measure_and_keeps_label_values(catalogue):
    oa = catalogue.cat.metrics
    count_measure = oa["attributed_orders"].cube_mapping.measure
    cube = _MetaCube(
        {
            "order_attribution.lt_utm_medium": [
                {"order_attribution.lt_utm_medium": "whatsapp", count_measure: "33"},
                {"order_attribution.lt_utm_medium": "120248089961790783", count_measure: "90"},
            ]
        },
        {m.cube_mapping.measure: ("countDistinct" if m.id == "attributed_orders" else "sum") for m in oa.values()},
    )
    index = ValueIndex(catalogue, cube, default_brand_id="20")  # type: ignore[arg-type]
    snap = await index.build("20")
    key = ("lt_utm_medium", "order_attribution")
    assert snap.values[key] == {"whatsapp": 33.0}
    assert snap.measure_metric[key] == "attributed_orders"
    q = next(q for q in cube.queries if q["dimensions"] == ["order_attribution.lt_utm_medium"])
    assert q["measures"] == [count_measure]
    assert q["filters"][0]["values"] == ["20"]  # brand-scoped


async def test_get_returns_none_while_warming_then_serves_the_snapshot(catalogue):
    class _SlowCube(_MetaCube):
        async def load(self, query: dict) -> CubeResult:
            import asyncio

            await asyncio.sleep(0.05)
            return await super().load(query)

    index = ValueIndex(catalogue, _SlowCube({}, {}), default_brand_id="20")  # type: ignore[arg-type]
    assert await index.get("20", wait_s=0.0) is None  # warming
    snap = await index.get("20", wait_s=30.0)
    assert snap is not None and snap.brand_id == "20"
