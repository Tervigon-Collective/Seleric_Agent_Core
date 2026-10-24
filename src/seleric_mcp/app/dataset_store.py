"""Dataset store: aligned tables built by build_dataset_query, kept in Core's DB until deleted.

Unlike ResultStore (1h TTL, query_id reference for drilldown), a dataset is addressed by its own handle and
lives until delete. Every read, list and delete is scoped by (brand_key, module_key): the caller is always the
shared service token, so scoping has to be explicit here. A handle outside the caller's scope is
indistinguishable from a missing one.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from ..storage.db import Database, utcnow_iso


class DatasetError(Exception):
    """A refusal with a stable ``code`` the caller can branch on."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra

    def to_payload(self) -> dict:
        return {"error": str(self), "code": self.code, **self.extra}


@dataclass
class StoredDataset:
    handle: str
    name: str | None
    brand_key: str
    module_key: str
    request: dict
    schema: list[dict]
    rows: list[dict]
    provenance: dict
    row_count: int
    size_bytes: int
    created_at: str


class DatasetStore:
    def __init__(self, db: Database, *, max_rows: int, max_bytes_per_brand: int):
        self._db = db
        self._max_rows = max_rows
        self._max_bytes = max_bytes_per_brand

    @property
    def max_rows(self) -> int:
        return self._max_rows

    def save(
        self,
        *,
        name: str | None,
        brand_key: str,
        module_key: str,
        request: dict,
        schema: list[dict],
        rows: list[dict],
        provenance: dict,
    ) -> StoredDataset:
        if len(rows) > self._max_rows:
            raise DatasetError(
                "dataset_too_large",
                f"The dataset has {len(rows)} rows; the cap is {self._max_rows}. Narrow the time range, "
                "use a coarser grain (day instead of hour), drop a dimension, or add a filter.",
                row_count=len(rows),
                max_rows=self._max_rows,
            )
        rows_json = json.dumps(rows, default=str, separators=(",", ":"))
        size = len(rows_json.encode("utf-8"))
        used = self._scope_bytes(brand_key)
        if used + size > self._max_bytes:
            raise DatasetError(
                "dataset_store_full",
                f"Saved datasets for this brand use {used} bytes and this one needs {size}; the cap is "
                f"{self._max_bytes}. Delete datasets you no longer need (action=delete), then retry.",
                used_bytes=used,
                needed_bytes=size,
                max_bytes=self._max_bytes,
            )
        handle = "ds_" + uuid.uuid4().hex[:12]
        created = utcnow_iso()
        self._db.execute(
            """INSERT INTO datasets
               (handle, name, brand_key, module_key, request_json, schema_json, rows_json,
                provenance_json, row_count, size_bytes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                handle,
                name,
                brand_key,
                module_key,
                json.dumps(request, default=str),
                json.dumps(schema),
                rows_json,
                json.dumps(provenance, default=str),
                len(rows),
                size,
                created,
            ),
        )
        return StoredDataset(handle, name, brand_key, module_key, request, schema, rows, provenance, len(rows), size, created)

    def get(self, handle: str, *, brand_key: str | None, module_key: str) -> StoredDataset:
        """The dataset, or DatasetError unknown_handle (also when it belongs to another scope).

        ``brand_key`` None means the caller did not name a brand: the stored brand then applies as built."""
        row = self._db.fetchone("SELECT * FROM datasets WHERE handle = ?", (handle,))
        if row is None or row["module_key"] != module_key or (brand_key is not None and row["brand_key"] != brand_key):
            raise DatasetError(
                "unknown_handle",
                f"No dataset {handle!r} in this scope. It may have been deleted, or built under another brand "
                "or module. action=list shows the datasets you can use.",
                handle=handle,
            )
        return StoredDataset(
            row["handle"],
            row["name"],
            row["brand_key"],
            row["module_key"],
            json.loads(row["request_json"]),
            json.loads(row["schema_json"]),
            json.loads(row["rows_json"]),
            json.loads(row["provenance_json"]),
            row["row_count"],
            row["size_bytes"],
            row["created_at"],
        )

    def list(self, *, brand_key: str | None, module_key: str) -> list[dict]:
        sql = "SELECT handle, name, brand_key, row_count, size_bytes, created_at, request_json FROM datasets WHERE module_key = ?"
        params: tuple = (module_key,)
        if brand_key is not None:
            sql += " AND brand_key = ?"
            params = (module_key, brand_key)
        out = []
        for r in self._db.fetchall(sql + " ORDER BY created_at DESC", params):
            req = json.loads(r["request_json"])
            out.append(
                {
                    "handle": r["handle"],
                    "name": r["name"],
                    "brand": r["brand_key"] or None,
                    "row_count": r["row_count"],
                    "size_bytes": r["size_bytes"],
                    "created_at": r["created_at"],
                    "measures": req.get("measures"),
                    "dimensions": req.get("dimensions"),
                    "grain": req.get("grain"),
                    "time_range": req.get("time_range"),
                }
            )
        return out

    def delete(self, handle: str, *, brand_key: str | None, module_key: str) -> None:
        self.get(handle, brand_key=brand_key, module_key=module_key)  # scope check; raises unknown_handle
        self._db.execute("DELETE FROM datasets WHERE handle = ?", (handle,))

    def _scope_bytes(self, brand_key: str) -> int:
        row = self._db.fetchone("SELECT COALESCE(SUM(size_bytes), 0) AS n FROM datasets WHERE brand_key = ?", (brand_key,))
        return int(row["n"]) if row else 0
