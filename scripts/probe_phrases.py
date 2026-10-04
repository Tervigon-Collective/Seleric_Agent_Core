"""Phrases a user might type for a metric — seeds the semantic v2 resolution gate (scripts/v2_gates.py)
and the inventory (scripts/build_metric_inventory_v2.py), on top of glossary terms, concept aliases and
registry aliases. Moved from the retired v1 scripts/build_metric_inventory.py."""

PROBE_PHRASES = ["revenue","sales","net sales","gross sales","total sales","profit","net profit",
    "gross profit","contribution margin","margin","net margin","roas","meta roas","google roas","mer",
    "cac","ltv","aov","orders","returns","return rate","rto","cogs","ad spend","meta spend","spend",
    "conversion rate","cpa","cod orders","amazon sales","new customers","repeat rate",
    "profit by channel","net profit meta","revenue by city","net revenue","gmv"]
