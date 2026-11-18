from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from seleric_mcp.app.result_store import ResultStore
from seleric_mcp.catalogue_service.loader import load_catalogue
from seleric_mcp.catalogue_service.service import CatalogueService
from seleric_mcp.config import PROJECT_ROOT, Settings
from seleric_mcp.observability.audit import AuditLog
from seleric_mcp.semantic_layer.cube_client import CubeResult
from seleric_mcp.storage.db import Database

CATALOGUE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "catalogue"


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


@pytest.fixture(scope="session")
def catalogue():
    return CatalogueService(load_catalogue(CATALOGUE_DIR))


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        # v1 catalogue pinned: these unit tests exercise gateway / planner mechanics on v1 fixture
        # ids; the semantic v2 surface (default catalogue_dir) is covered by test_semantic_v2.py.
        catalogue_dir=CATALOGUE_DIR,
        cube_api_url="http://cube.test",
        seleric_api_key="test-key",
        cubejs_api_secret="",
        write_enabled=False,
        mcp_service_token="svc-token",
        approval_secret="approval-secret-for-tests-0123456789",
        caller_scopes=frozenset({"metrics:read", "meta_ads:read"}),
        db_path=tmp_path / "test.db",
    )


class FakeCube:
    """Programmable Cube client double. Routes on the first measure/dimension's
    view prefix; records every query it receives."""

    def __init__(self):
        self.responses: list[list[dict]] = []
        self.by_prefix: dict[str, list[dict]] = {}
        self.queries: list[dict] = []
        self.fail = False

    async def load(self, query: dict) -> CubeResult:
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("cube down")
        members = query.get("measures") or query.get("dimensions") or [""]
        prefix = members[0].split(".")[0]
        if prefix in self.by_prefix:
            data = self.by_prefix[prefix]
        elif self.responses:
            data = self.responses.pop(0)
        else:
            data = []
        return CubeResult(data=data, raw={"data": data, "lastRefreshTime": "2026-07-10T04:00:00Z"})

    async def meta(self) -> dict:
        return {"cubes": []}


@pytest.fixture()
def fake_cube():
    return FakeCube()


@pytest.fixture()
def result_store(db):
    return ResultStore(db)


@pytest.fixture()
def audit(db):
    return AuditLog(db)
