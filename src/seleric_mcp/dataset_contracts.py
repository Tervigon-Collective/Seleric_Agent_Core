#!/usr/bin/env python
"""Dataset contract loader for SEM-16: enforces certified grains and policies in query execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "catalogue_v2_src" / "datasets.yaml"


@dataclass
class DatasetContract:
    name: str
    domain: str
    role: str
    description: str
    physical_bindings: dict[str, str]  # binding_name -> table
    declared_keys: list[str]
    date_policy_default: str
    date_policy_meaning: str
    slice_dimension: str | None = None
    supported_platforms: list[str] = field(default_factory=list)
    mixed_basis: bool = False
    certification_status: Literal["certified", "partial", "pending_verification", "failed"] = "pending_verification"
    certified_on: str | None = None
    evidence_ref: str | None = None


def load_dataset_contracts() -> dict[str, DatasetContract]:
    """Load dataset contracts from catalogue_v2_src/datasets.yaml."""
    raw = yaml.safe_load(DATASETS.read_text())
    contracts = {}
    for name, ds in raw.items():
        grain = ds.get("grain", {})
        date_policy = ds.get("date_policy", {})
        cert = ds.get("certification", {})
        contracts[name] = DatasetContract(
            name=name,
            domain=ds.get("domain", ""),
            role=ds.get("role", ""),
            description=ds.get("description", ""),
            physical_bindings=ds.get("physical_bindings", {}),
            declared_keys=grain.get("declared_keys", []),
            date_policy_default=date_policy.get("default", ""),
            date_policy_meaning=date_policy.get("meaning", ""),
            slice_dimension=ds.get("slice_dimension"),
            supported_platforms=ds.get("supported_platforms", []),
            mixed_basis=ds.get("mixed_basis", False),
            certification_status=cert.get("status", "pending_verification"),
            certified_on=cert.get("certified_on"),
            evidence_ref=cert.get("evidence"),
        )
    return contracts


def get_contract_for_view(view: str, contracts: dict[str, DatasetContract] | None = None) -> list[DatasetContract]:
    """Return contracts that have a binding pointing to this view."""
    contracts = contracts or load_dataset_contracts()
    return [c for c in contracts.values() if any(v == view for v in c.physical_bindings.values())]


def get_binding_grain(view: str, contracts: dict[str, DatasetContract] | None = None) -> list[str] | None:
    """Get the declared keys (grain) for a view, if any certified contract covers it."""
    contracts = contracts or load_dataset_contracts()
    for c in contracts.values():
        for bname, table in c.physical_bindings.items():
            if table == view and c.certification_status == "certified":
                return c.declared_keys
    return None


def validate_grain(view: str, dimensions: list[str], filters: list[str], contracts: dict[str, DatasetContract] | None = None) -> list[str]:
    """Validate that requested dimensions/filters are compatible with the certified grain.
    Returns warnings (not errors) for advisory issues.
    """
    contracts = contracts or load_dataset_contracts()
    warnings = []
    for c in contracts.values():
        if view not in c.physical_bindings.values():
            continue
        if c.certification_status != "certified":
            continue
        # Check if any requested dimension is not in declared keys and not a measure
        # This is advisory - the query may still work but grain changes
        grain_set = set(c.declared_keys)
        for d in dimensions:
            if d not in grain_set and d not in ("brand_id",):  # brand_id is usually implicit
                warnings.append(f"Dimension '{d}' not in certified grain of {c.name} ({view}); result grain may differ from {c.name}'s declared grain {c.declared_keys}.")
    return warnings


if __name__ == "__main__":
    contracts = load_dataset_contracts()
    for name, c in contracts.items():
        print(f"{name}: status={c.certification_status}, grain={c.declared_keys}, bindings={c.physical_bindings}")