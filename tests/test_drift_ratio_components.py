"""The drift check must resolve v2 ratio components (catalogue metric ids) to Cube members."""

from __future__ import annotations

from seleric_mcp.catalogue_service.loader import load_catalogue
from seleric_mcp.catalogue_service.service import CatalogueService
from seleric_mcp.catalogue_service.validate import validate_against_cube
from seleric_mcp.config import PROJECT_ROOT


class _Cube:
    def __init__(self, members: set[str]) -> None:
        self._members = members

    async def meta(self) -> dict:
        cubes: dict[str, dict] = {}
        for name in self._members:
            cube, _, member = name.partition(".")
            cubes.setdefault(cube, {"name": cube, "measures": [], "dimensions": []})["measures"].append({"name": name})
        return {"cubes": list(cubes.values())}


async def test_ratio_components_are_checked_as_cube_members_not_metric_ids() -> None:
    service = CatalogueService(load_catalogue(PROJECT_ROOT / "catalogue_v2"))
    ratio = next(m for m in service.cat.metrics.values() if m.ratio_components and m.ratio_component_members)
    members = {ratio.cube_mapping.measure}
    for pair in ratio.ratio_component_members.values():
        members.update(pair)
    for dim_id in ratio.supported_dimensions:
        dim = service.cat.dimensions.get(dim_id)
        if dim and ratio.cube_mapping.view in dim.views:
            members.add(dim.views[ratio.cube_mapping.view])
    drift = await validate_against_cube(service, _Cube(members))  # type: ignore[arg-type]
    assert ratio.id not in drift["broken"]


async def test_ratio_with_a_missing_component_member_is_still_broken() -> None:
    service = CatalogueService(load_catalogue(PROJECT_ROOT / "catalogue_v2"))
    ratio = next(m for m in service.cat.metrics.values() if m.ratio_components and m.ratio_component_members)
    drift = await validate_against_cube(service, _Cube({ratio.cube_mapping.measure}))  # type: ignore[arg-type]
    assert ratio.id in drift["broken"]
