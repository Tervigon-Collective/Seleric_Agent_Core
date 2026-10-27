"""What a user types about product-page and listing-card behaviour lands on the PDP metrics, not an ads or event-count look-alike."""

import pathlib

import pytest

from seleric_mcp.catalogue_service.loader import load_catalogue
from seleric_mcp.catalogue_service.service import CatalogueService, ResolvedConcept


@pytest.fixture(scope="module")
def catalogue():
    """The real shipped catalogue (the shared conftest fixture is a v1 fixture set)."""
    return CatalogueService(load_catalogue(pathlib.Path(__file__).resolve().parents[1] / "catalogue"))


@pytest.mark.parametrize("text,axes,metric", [
    ("pdp behaviour", {"measure": "engaged_rate"}, "pdp_engaged_rate"),
    ("product page behaviour", {"measure": "add_to_cart_rate"}, "pdp_add_to_cart_rate"),
    ("product page behaviour", None, "pdp_page_views"),
    ("pdp section reach", None, "section_reach_rate"),
    ("listing card interactions", None, "listing_interactions"),
    ("collection card clicks", None, "listing_interactions"),
    ("listing card buy now", {"measure": "buy_now_clicks"}, "listing_buy_now_clicks"),
])
def test_phrase_resolves_to_the_pdp_metric(catalogue, text, axes, metric):
    r = catalogue.resolve_concept(text, axes)
    assert isinstance(r, ResolvedConcept), r
    assert r.metric_id == metric


def test_the_old_event_based_pdp_phrases_keep_their_meaning(catalogue):
    assert catalogue.resolve_concept("pdp views", None).metric_id == "product_view_events"
