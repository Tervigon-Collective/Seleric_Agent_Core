"""Load the versioned YAML catalogue into typed in-memory definitions.

The YAML files under catalogue/ are the authoritative registry (reviewed like
code). catalogue_version is a short content hash so every response can pin the
exact registry it was answered from.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class Formula(BaseModel):
    human_readable: str
    authoritative_source: Literal["cube"] = "cube"
    # Catalogue metric ids this derived metric is composed from (agent/ontology
    # disambiguation). Prefer real catalogue ids over free-text tokens.
    depends_on: list[str] = Field(default_factory=list)


class CubeMapping(BaseModel):
    view: str
    measure: str
    measure_pct: str | None = None
    # Fully-qualified time dimension (e.g. "commerce_orders.event_date") that
    # date-range filters must apply to for THIS metric, overriding the view's
    # default date_dimension. Event-axis metrics (returns/cancels) declare it;
    # placement-axis metrics leave it unset.
    time_dimension: str | None = None


class RatioComponents(BaseModel):
    numerator: str
    denominator: str


class AccessPolicy(BaseModel):
    roles_allowed: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=lambda: ["metrics:read"])


class Freshness(BaseModel):
    source: str
    expected_cadence: str


class MetricDef(BaseModel):
    id: str
    display_name: str
    category: str
    status: Literal["approved", "certified", "draft", "broken"] = "approved"

    @property
    def is_queryable(self) -> bool:
        return self.status in ("approved", "certified")
    description: str
    formula: Formula
    cube_mapping: CubeMapping
    aggregation: Literal["additive", "ratio"]
    ratio_components: RatioComponents | None = None
    companion_measures: list[str] = Field(default_factory=list)
    unit: str
    currency_default: str | None = None
    grain: str
    supported_dimensions: list[str] = Field(default_factory=list)
    supported_filters: list[str] = Field(default_factory=list)
    data_owner: str
    access_policy: AccessPolicy = Field(default_factory=AccessPolicy)
    examples: list[dict] = Field(default_factory=list)
    validation_tests: list[str] = Field(default_factory=list)
    deprecated_aliases: list[str] = Field(default_factory=list)


class DimensionDef(BaseModel):
    id: str
    display_name: str
    description: str = ""
    is_time: bool = False
    views: dict[str, str]  # view name -> qualified cube member
    aliases: list[str] = Field(default_factory=list)  # NL synonyms (province, state, …)
    allowed_values: list[str] | None = None  # declared only for small, stable
    # enum-like dimensions (verified against source SQL, not guessed) — lets
    # equals/notEquals filters be case/typo-corrected instead of silently
    # matching zero rows. None means "no known enum" — filter values pass
    # through unvalidated, exactly as before this field existed.


class GlossaryTerm(BaseModel):
    term: str
    canonical_id: str | None = None
    definition: str | None = None


class ViewDef(BaseModel):
    name: str
    title: str
    date_dimension: str | None = None
    # Intraday timestamp axis (bare cube member, e.g. "order_created_at_ist")
    # used for sub-daily granularity (hour). The default date_dimension is a
    # DATE column, so bucketing it by hour would collapse every order to
    # midnight; sub-daily queries must group on this timestamp instead.
    datetime_dimension: str | None = None
    freshness: Freshness


class BusinessRule(BaseModel):
    id: str
    description: str
    blocking: bool


class ActionContractDef(BaseModel):
    id: str
    display_name: str
    domain: str
    status: Literal["approved", "draft"] = "approved"
    description: str
    executor: Literal["pipeboard", "backend_api"]
    executor_action_type: str
    payload_schema: str
    scopes_required: list[str]
    risk_level: Literal["low", "medium", "high"]
    confirmation_ttl_seconds: int = 300
    business_rules: list[BusinessRule] = Field(default_factory=list)
    preview: dict = Field(default_factory=dict)
    data_owner: str


class Deprecation(BaseModel):
    old: str
    new: str
    reason: str


class ModuleDef(BaseModel):
    """A dashboard module's data-access scope. Resolves (via ontology domains
    + optional extra_views) to a set of allowed cube views, and from there to
    the catalogue metrics on those views. Declared in catalogue/modules.yaml."""

    id: str
    display_name: str
    description: str = ""
    domains: list[str] = Field(default_factory=list)
    extra_views: list[str] = Field(default_factory=list)


class BrandDef(BaseModel):
    id: str
    name: str
    code: str | None = None
    status: Literal["active", "test", "inactive"] = "active"
    aliases: list[str] = Field(default_factory=list)
    # Data-coverage caveat surfaced with every brand resolution. A tenant can be
    # present in some serve relations and absent from others (e.g. paid media but
    # no commerce), which makes any P&L or per-order metric wrong rather than
    # merely empty. Stated here so the agent warns instead of answering.
    scope_note: str | None = None


class BrandRegistry(BaseModel):
    default_brand_id: str = "20"
    brands: list[BrandDef] = Field(default_factory=list)


class OpenMetadataDataProduct(BaseModel):
    name: str
    domain: str
    owner_team: str
    primary_serve_table: str
    contract: str
    secondary_contracts: list[str] = Field(default_factory=list)
    cube_views: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    notes: str | None = None


class OpenMetadataViewLink(BaseModel):
    data_product: str
    serve_table: str
    gold_inputs: list[str] = Field(default_factory=list)
    contract: str | None = None


class OpenMetadataMetricLink(BaseModel):
    om_name: str | None = None
    glossary: list[str] = Field(default_factory=list)
    category: str | None = None
    cube_view: str | None = None
    contract: str | None = None


class OpenMetadataContract(BaseModel):
    serve_table: str
    data_product: str
    domain: str
    grain: list[str] = Field(default_factory=list)
    time_dimension: str | None = None
    currency: str = "INR"
    required_columns: list[str] = Field(default_factory=list)
    quality_tests: list[str] = Field(default_factory=list)
    attribution_boundary: str | None = None
    notes: str | None = None
    program: str | None = None
    pii_rule: str | None = None
    composition_rule: str | None = None


class OpenMetadataOntology(BaseModel):
    domains: dict = Field(default_factory=dict)
    entity_clusters: dict = Field(default_factory=dict)
    attribution_boundary: dict = Field(default_factory=dict)
    # Explicit reasons for catalogue metrics that are not in an entity cluster.
    # Shape: {default?: str, by_metric?: {id: reason}, by_view?: {view: reason},
    #         by_category?: {category: reason}}
    unclustered: dict = Field(default_factory=dict)


class OpenMetadataRegistry(BaseModel):
    instance: dict = Field(default_factory=dict)
    agent_ready_tag: str = "DataProduct.AgentReady"
    release_status_tag: str = "ReleaseStatus.Certified"
    currency: str = "INR"
    data_products: list[OpenMetadataDataProduct] = Field(default_factory=list)
    views: dict[str, OpenMetadataViewLink] = Field(default_factory=dict)
    metrics: dict[str, OpenMetadataMetricLink] = Field(default_factory=list)
    contracts: dict[str, OpenMetadataContract] = Field(default_factory=dict)
    ontology: OpenMetadataOntology | None = None
    glossaries: list[dict] = Field(default_factory=list)


class Catalogue(BaseModel):
    version: str
    metrics: dict[str, MetricDef]
    dimensions: dict[str, DimensionDef]
    glossary: list[GlossaryTerm]
    views: dict[str, ViewDef]
    actions: dict[str, ActionContractDef]
    deprecations: list[Deprecation]
    brands: BrandRegistry | None = None
    openmetadata: OpenMetadataRegistry | None = None
    modules: dict[str, ModuleDef] = Field(default_factory=dict)


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_catalogue(catalogue_dir: Path) -> Catalogue:
    hasher = hashlib.sha256()
    yaml_files = sorted(catalogue_dir.rglob("*.yaml"))
    if not yaml_files:
        raise FileNotFoundError(f"No catalogue YAML found under {catalogue_dir}")
    for p in yaml_files:
        hasher.update(p.read_bytes())
    version = hasher.hexdigest()[:12]

    metrics: dict[str, MetricDef] = {}
    for p in sorted((catalogue_dir / "metrics").glob("*.yaml")):
        m = MetricDef.model_validate(_read_yaml(p))
        metrics[m.id] = m

    dimensions: dict[str, DimensionDef] = {}
    for p in sorted((catalogue_dir / "dimensions").glob("*.yaml")):
        for raw in _read_yaml(p).get("dimensions", []):
            d = DimensionDef.model_validate(raw)
            dimensions[d.id] = d

    glossary = [
        GlossaryTerm.model_validate(raw)
        for raw in _read_yaml(catalogue_dir / "glossary" / "terms.yaml").get("terms", [])
    ]

    views: dict[str, ViewDef] = {}
    for raw in _read_yaml(catalogue_dir / "views.yaml").get("views", []):
        v = ViewDef.model_validate(raw)
        views[v.name] = v

    actions: dict[str, ActionContractDef] = {}
    actions_dir = catalogue_dir / "actions"
    if actions_dir.exists():
        for p in sorted(actions_dir.glob("*.yaml")):
            a = ActionContractDef.model_validate(_read_yaml(p))
            actions[a.id] = a

    deprecations = [
        Deprecation.model_validate(raw)
        for raw in _read_yaml(catalogue_dir / "deprecations.yaml").get("deprecations", [])
    ]

    brands: BrandRegistry | None = None
    brands_path = catalogue_dir / "brands.yaml"
    if brands_path.exists():
        brands = BrandRegistry.model_validate(_read_yaml(brands_path))

    modules: dict[str, ModuleDef] = {}
    modules_path = catalogue_dir / "modules.yaml"
    if modules_path.exists():
        for mid, spec in (_read_yaml(modules_path).get("modules") or {}).items():
            spec = dict(spec or {})
            spec["id"] = mid
            modules[mid] = ModuleDef.model_validate(spec)

    om_registry: OpenMetadataRegistry | None = None
    om_path = catalogue_dir / "openmetadata" / "registry.yaml"
    if om_path.exists():
        om_raw = _read_yaml(om_path)
        metrics_path = catalogue_dir / "openmetadata" / "metrics.yaml"
        if metrics_path.exists():
            metrics_doc = _read_yaml(metrics_path)
            om_raw["metrics"] = metrics_doc.get("metrics", {})
        contracts_path = catalogue_dir / "openmetadata" / "contracts.yaml"
        if contracts_path.exists():
            om_raw["contracts"] = _read_yaml(contracts_path).get("contracts", {})
        ontology_path = catalogue_dir / "openmetadata" / "ontology.yaml"
        if ontology_path.exists():
            om_raw["ontology"] = _read_yaml(ontology_path)
        om_registry = OpenMetadataRegistry.model_validate(om_raw)

    # Overlay the generated crosswalk. Everything in it is DERIVED from the systems
    # that own the fact (ClickHouse lineage, the Cube model, the OpenMetadata specs),
    # so it wins over any hand-authored restatement. Entries the crosswalk does not
    # know about are preserved rather than dropped: a Cube view no data product
    # claims yet must keep working, and scripts/reconcile_layers.py reports it as
    # still hand-maintained so the exception list shrinks instead of hiding.
    _apply_crosswalk(catalogue_dir, om_registry, views)

    cat = Catalogue(
        version=version,
        metrics=metrics,
        dimensions=dimensions,
        glossary=glossary,
        views=views,
        actions=actions,
        deprecations=deprecations,
        brands=brands,
        openmetadata=om_registry,
        modules=modules,
    )
    _check_integrity(cat)
    return cat



def _apply_crosswalk(
    catalogue_dir: Path, om: "OpenMetadataRegistry | None", views: dict[str, "ViewDef"]
) -> dict | None:
    """Overlay catalogue/openmetadata/crosswalk.generated.yaml onto the catalogue.

    Returns the crosswalk document (or None when absent, so a deployment without
    it behaves exactly as before).
    """
    path = catalogue_dir / "openmetadata" / "crosswalk.generated.yaml"
    if not path.exists():
        return None
    cw = _read_yaml(path)
    cw_views = cw.get("views") or {}
    cw_domains = cw.get("domains") or {}

    # 1. Date axes come from the port's contract / the Cube view, never from a
    #    second hand-maintained copy.
    for name, spec in cw_views.items():
        v = views.get(name)
        if v is None:
            continue
        if spec.get("date_dimension"):
            v.date_dimension = spec["date_dimension"]
        if spec.get("datetime_dimension"):
            v.datetime_dimension = spec["datetime_dimension"]

    if om is None:
        return cw

    # 1a. Data products are derived from the OpenMetadata specs. A product declared
    #     there but not restated in the hand-written registry must still resolve, or
    #     every view it owns fails the integrity check.
    known = {p.name for p in om.data_products}
    for gen in cw.get("data_products") or []:
        if gen["name"] in known:
            continue
        om.data_products.append(OpenMetadataDataProduct.model_validate({
            "name": gen["name"],
            "domain": gen.get("domain") or "",
            "owner_team": gen.get("owner_team") or "",
            "primary_serve_table": gen.get("primary_serve_table") or "",
            "contract": (gen.get("contracts") or [""])[0] or "",
            "secondary_contracts": list((gen.get("contracts") or [])[1:]),
            "cube_views": list(gen.get("cube_views") or []),
        }))

    # 1b. Contract summaries are derived from mage-ai/openmetadata/contracts/.
    #     Generated wins; anything hand-authored that the generator does not know
    #     about is kept so no existing reference dangles.
    for cname, cspec in (cw.get("contracts") or {}).items():
        payload = {k: v for k, v in cspec.items() if k != "contract_file"}
        if not payload.get("data_product") or not payload.get("domain"):
            continue  # incomplete spec - leave whatever the hand file said
        om.contracts[cname] = OpenMetadataContract.model_validate(payload)

    if om.ontology is None:
        return cw

    # 2. A domain's products and cube views are derived from the OM product specs.
    #    Union rather than replace: views no product claims yet stay reachable.
    for dname, dspec in (om.ontology.domains or {}).items():
        gen = cw_domains.get(dname)
        if not gen:
            continue
        if gen.get("owner_team"):
            dspec["owner_team"] = gen["owner_team"]
        for key in ("data_products", "cube_views"):
            derived = list(gen.get(key) or [])
            existing = list(dspec.get(key) or [])
            dspec[key] = derived + [x for x in existing if x not in derived]
        dspec["_ungoverned_cube_views"] = [
            x for x in (dspec.get("cube_views") or []) if x not in (gen.get("cube_views") or [])
        ]

    # 3. Per-view provenance (owning product, serve table, verified gold lineage,
    #    contract) is derived. This is what the agent shows a client, so it must be
    #    the real lineage, not a declared one.
    for name, spec in cw_views.items():
        if not spec.get("data_product"):
            continue
        link = om.views.get(name)
        payload = {
            "data_product": spec["data_product"],
            "serve_table": spec.get("serve_table") or (link.serve_table if link else ""),
            "gold_inputs": list(spec.get("gold_inputs") or []),
            "contract": spec.get("contract") or (link.contract if link else None),
        }
        om.views[name] = OpenMetadataViewLink.model_validate(payload)
    return cw


def _check_integrity(cat: Catalogue) -> None:
    """Fail fast on internal inconsistencies (bad refs between YAML files)."""
    problems: list[str] = []

    # Display names must be unique (case-insensitive) so agents/ontology never
    # collide on English labels that map to different metric identities.
    by_display: dict[str, str] = {}
    for m in cat.metrics.values():
        key = m.display_name.strip().casefold()
        if not key:
            problems.append(f"metric {m.id}: empty display_name")
        elif key in by_display:
            problems.append(
                f"duplicate display_name {m.display_name!r}: {by_display[key]} and {m.id}"
            )
        else:
            by_display[key] = m.id

    for m in cat.metrics.values():
        if m.cube_mapping.view not in cat.views:
            problems.append(f"metric {m.id}: unknown view {m.cube_mapping.view}")
        if m.cube_mapping.time_dimension and not m.cube_mapping.time_dimension.startswith(
            f"{m.cube_mapping.view}."
        ):
            problems.append(
                f"metric {m.id}: time_dimension {m.cube_mapping.time_dimension} "
                f"is not on view {m.cube_mapping.view}"
            )
        for dim_id in m.supported_dimensions:
            dim = cat.dimensions.get(dim_id)
            if dim is None:
                problems.append(f"metric {m.id}: unknown dimension {dim_id}")
            elif m.cube_mapping.view not in dim.views:
                problems.append(
                    f"metric {m.id}: dimension {dim_id} has no mapping for view {m.cube_mapping.view}"
                )
        if m.aggregation == "ratio" and m.ratio_components is None:
            hr = (m.formula.human_readable or "").strip().upper()
            # AVG(...) cube rollups are ratios in the catalogue sense but are not
            # decomposable into additive numerator/denominator catalogue metrics.
            if not hr.startswith("AVG("):
                problems.append(
                    f"metric {m.id}: aggregation=ratio requires ratio_components "
                    f"(numerator/denominator) so agents can decompose the formula"
                )
        for dep in m.formula.depends_on:
            if dep not in cat.metrics:
                problems.append(f"metric {m.id}: formula.depends_on unknown metric '{dep}'")
        for companion in m.companion_measures:
            if companion not in cat.metrics:
                problems.append(f"metric {m.id}: companion_measures unknown metric '{companion}'")

    # Glossary terms are indexed case-insensitively — conflicting targets confuse agents.
    gloss_by_norm: dict[str, tuple[str, str | None]] = {}
    for t in cat.glossary:
        if t.canonical_id is not None and t.canonical_id not in cat.metrics:
            problems.append(f"glossary term '{t.term}': unknown canonical_id {t.canonical_id}")
        norm = t.term.strip().lower()
        if norm in gloss_by_norm:
            prev_term, prev_id = gloss_by_norm[norm]
            if prev_id != t.canonical_id:
                problems.append(
                    f"glossary term collision '{t.term}' vs '{prev_term}': "
                    f"{prev_id} vs {t.canonical_id}"
                )
        else:
            gloss_by_norm[norm] = (t.term, t.canonical_id)
    if cat.openmetadata:
        for view_name, link in cat.openmetadata.views.items():
            if view_name not in cat.views:
                problems.append(f"openmetadata.views.{view_name}: unknown catalogue view")
            if link.data_product not in {dp.name for dp in cat.openmetadata.data_products}:
                problems.append(
                    f"openmetadata.views.{view_name}: unknown data_product {link.data_product}"
                )
        for metric_id in cat.openmetadata.metrics:
            if metric_id not in cat.metrics:
                problems.append(f"openmetadata.metrics.{metric_id}: unknown catalogue metric")
        if len(cat.openmetadata.metrics) != len(cat.metrics):
            problems.append(
                f"openmetadata.metrics: expected {len(cat.metrics)} entries, "
                f"got {len(cat.openmetadata.metrics)}"
            )
        for contract_id, dp_names in _contract_dp_refs(cat).items():
            if contract_id not in cat.openmetadata.contracts:
                problems.append(f"openmetadata: missing contract definition for {contract_id}")
            elif cat.openmetadata.contracts[contract_id].data_product not in dp_names:
                problems.append(
                    f"openmetadata.contracts.{contract_id}: data_product mismatch"
                )
    if cat.modules:
        onto = cat.openmetadata.ontology if cat.openmetadata else None
        domain_specs: dict = onto.domains if onto else {}
        for mod in cat.modules.values():
            resolved_any = bool(mod.extra_views)
            for domain in mod.domains:
                if domain not in domain_specs:
                    problems.append(f"module {mod.id}: unknown ontology domain '{domain}'")
                    continue
                for view in domain_specs[domain].get("cube_views", []) or []:
                    resolved_any = True
                    if view not in cat.views:
                        problems.append(
                            f"module {mod.id}: domain '{domain}' references unknown view '{view}'"
                        )
            for view in mod.extra_views:
                if view not in cat.views:
                    problems.append(f"module {mod.id}: unknown extra_view '{view}'")
            if not resolved_any:
                problems.append(f"module {mod.id}: resolves to no cube views")
    if problems:
        raise ValueError("Catalogue integrity check failed:\n" + "\n".join(problems))


def _contract_dp_refs(cat: Catalogue) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for dp in cat.openmetadata.data_products:  # type: ignore[union-attr]
        refs.setdefault(dp.contract, set()).add(dp.name)
    for link in cat.openmetadata.views.values():  # type: ignore[union-attr]
        if link.contract:
            dp = link.data_product
            refs.setdefault(link.contract, set()).add(dp)
    return refs
