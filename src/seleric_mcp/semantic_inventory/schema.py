"""Typed records for the SEM-00 semantic surface inventory."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class PhysicalObject:
    """One object in the ClickHouse ``serve`` database (or another serving db).

    ``role`` is a single declared public semantic role (SEM-00 §4.4); ``roles``
    carries any additional operational roles. ``role_evidence`` records *why*
    the classifier chose it so a reviewer can challenge the decision.
    """

    name: str  # "serve.ad_delivery_daily"
    database: str
    engine: str
    columns: dict[str, str] = field(default_factory=dict)  # column -> ClickHouse type
    refs: list[str] = field(default_factory=list)  # upstream objects referenced in the DDL
    writes: str | None = None  # MaterializedView target
    role: str = "unclassified"
    roles: list[str] = field(default_factory=list)
    role_evidence: str = ""
    grain_keys: list[str] = field(default_factory=list)
    time_dimensions: list[str] = field(default_factory=list)
    consumers: list[str] = field(default_factory=list)  # Cube views/measures served from here


@dataclass(frozen=True)
class CubeMember:
    """A measure or dimension inside the Cube model."""

    kind: str  # "measure" | "dimension"
    cube: str
    name: str
    sql: str = ""
    type: str = ""
    public: bool = True
    sql_table: str = ""  # the physical table the defining cube reads
    description: str = ""


@dataclass(frozen=True)
class CubeSurface:
    """Normalized Cube v2 model: cubes, public views and their members."""

    cubes: dict[str, dict] = field(default_factory=dict)  # cube name -> {sql_table, public, joins, ...}
    views: dict[str, dict] = field(default_factory=dict)  # view name -> {description, cubes: [...]}
    members: dict[str, CubeMember] = field(default_factory=dict)  # "view.measure" -> member
    physical_sources: dict[str, list[str]] = field(default_factory=dict)  # member -> [sql_table, ...]


@dataclass(frozen=True)
class MetricRecord:
    """A catalogue metric (catalogue_v2) reduced to its semantic decisions."""

    id: str
    display_name: str
    category: str
    status: str
    version: str
    aggregation: str
    unit: str
    grain: str
    date_basis: str
    view: str
    measure: str
    formula: str
    depends_on: list[str] = field(default_factory=list)
    ratio_numerator: str | None = None
    ratio_denominator: str | None = None
    ratio_multiplier: float | None = None
    bindings: list[dict] = field(default_factory=list)
    supported_dimensions: list[str] = field(default_factory=list)
    supported_filters: list[str] = field(default_factory=list)
    valid_for: dict[str, list[str]] = field(default_factory=dict)
    excluded_dimensions: dict[str, str] = field(default_factory=dict)
    replaces: list[str] = field(default_factory=list)
    available: bool = True
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class Inventory:
    """The assembled SEM-00 inventory and its reports."""

    catalogue_version: str = ""
    physical_views: list[PhysicalObject] = field(default_factory=list)
    metrics: list[MetricRecord] = field(default_factory=list)
    ownership: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    lineage: list[dict] = field(default_factory=list)
    consumer_dependencies: list[dict] = field(default_factory=list)
    cube: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "catalogue_version": self.catalogue_version,
            "physical_views": [asdict(o) for o in self.physical_views],
            "metrics": [asdict(m) for m in self.metrics],
            "ownership": self.ownership,
            "conflicts": self.conflicts,
            "lineage": self.lineage,
            "consumer_dependencies": self.consumer_dependencies,
            "cube": self.cube,
        }
