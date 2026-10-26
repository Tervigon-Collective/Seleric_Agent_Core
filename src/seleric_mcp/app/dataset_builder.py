"""build_dataset_query: aligned, persisted tables from catalogue metrics.

One request names catalogue metrics, catalogue dimensions, a time range and a grain (hour | day). The builder
reuses QueryPlanner.run for everything the planner already decides (metric resolution, bindings, date axis,
dimension scope, brand scope, Cube execution) and then aligns the per-view parts on (time bucket + dimensions).
The planner itself never joins parts; the builder does, under conditions read from the catalogue only:

* every part must carry the time axis for the requested grain (the planner refuses views without one), and
* every requested dimension must be present in every non-empty part (the planner validates each dimension on
  each view, so a dimension that is not conformed across the views refuses the whole request).

Intraday correctness: buckets are IST. A cell with no row is a real 0 (additive measure, bucket at or before the
source's last observed bucket) or missing / null (after it, or a non-additive measure). The current partial hour is
left out of an hourly dataset unless asked for.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field

from .dataset_store import DatasetError, DatasetStore, StoredDataset
from .models import FilterSpec, PlanError, QueryRequest, TimeRange
from ..config import PROJECT_ROOT
from .query_planner import QueryPlanner, resolve_time_range

IST = ZoneInfo("Asia/Kolkata")
BUCKET = "bucket"
PREVIEW_ROWS = 20
GET_DEFAULT_LIMIT = 1000
GET_MAX_LIMIT = 5000
GRAINS = ("hour", "day")

NOT_MODELLED = (
    "Amazon ads and Amazon orders are not modelled in the semantic layer yet; a dataset covers Shopify orders, "
    "Meta and Google ads, web sessions/events and returns only."
)


HOURLY_NOTES_FILE = PROJECT_ROOT / "catalogue_src" / "hourly_notes.yaml"


def load_hourly_notes(path=HOURLY_NOTES_FILE) -> dict[str, str]:
    """view -> a caveat that applies to its sub-daily buckets (hand-maintained, optional file)."""
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {str(k): str(v) for k, v in data.items()}


class DatasetSpec(BaseModel):
    measures: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    time_range: TimeRange
    grain: str = "day"
    date_axis: str | None = None
    include_partial_hour: bool = False


@dataclass
class _Part:
    view: str
    metric_ids: list[str]
    query_id: str
    rows: list[dict]
    measure_cols: list[str]
    time_member: str
    watermark: str | None
    watermark_basis: str
    axis: dict[str, str]
    freshness: dict
    warnings: list[str]
    row_count: int = 0
    dims_seen: set[str] = field(default_factory=set)


def _num(v):
    """Cube returns measures as strings; keep None, turn numerics into int/float."""
    if v is None or isinstance(v, bool | int | float):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f.is_integer() and "." not in str(v) and "e" not in str(v).lower() else f


def _bucket(value, grain: str) -> str:
    dt = datetime.fromisoformat(str(value))
    return dt.strftime("%Y-%m-%d %H:00") if grain == "hour" else dt.strftime("%Y-%m-%d")


def _spine(start: date, end: date, grain: str, upto: str) -> list[str]:
    out: list[str] = []
    d = start
    while d <= end:
        if grain == "day":
            out.append(d.isoformat())
        else:
            out.extend(f"{d.isoformat()} {h:02d}:00" for h in range(24))
        d += timedelta(days=1)
    return [b for b in out if b <= upto]


def _now_bucket(grain: str) -> str:
    now = datetime.now(IST)
    return now.strftime("%Y-%m-%d %H:00") if grain == "hour" else now.strftime("%Y-%m-%d")


class DatasetBuilder:
    def __init__(
        self,
        planner: QueryPlanner,
        store: DatasetStore,
        default_brand_id: str | None = None,
        latest_date: Callable[[str, str], Awaitable[str | None]] | None = None,
        hourly_notes: dict[str, str] | None = None,
        part_row_cap: int = 50_000,
    ):
        self.planner = planner
        self.store = store
        self.default_brand_id = default_brand_id or ""
        # (view, brand_key) -> the newest date the view holds for that brand; None when unknown
        self.latest_date = latest_date
        self.hourly_notes = load_hourly_notes() if hourly_notes is None else hourly_notes
        self.part_row_cap = part_row_cap

    # ---------------------------------------------------------------- scope

    def brand_key(self, filters: list[FilterSpec] | list[dict] | None) -> str:
        """The brand a build is scoped to: an explicit brand_id equals filter, else the default brand."""
        for f in filters or []:
            spec = f if isinstance(f, FilterSpec) else FilterSpec.model_validate(f)
            if self.planner.catalogue.resolve_dimension_id(spec.dimension) == "brand_id" and spec.values:
                return str(spec.values[0])
        return self.default_brand_id

    # ---------------------------------------------------------------- build

    async def build(self, spec: DatasetSpec, *, module_key: str, name: str | None) -> dict:
        if spec.grain not in GRAINS:
            raise DatasetError("invalid_spec", f"grain must be one of {', '.join(GRAINS)}; got {spec.grain!r}.")
        request = QueryRequest(
            measures=spec.measures,
            dimensions=spec.dimensions,
            filters=spec.filters,
            time_range=spec.time_range,
            granularity=spec.grain,  # type: ignore[arg-type]
            date_axis=spec.date_axis,  # type: ignore[arg-type]
        )
        result = await self.planner.run(request)
        parts_raw = result["parts"] if result.get("composed") else [result]
        dims = [self.planner.catalogue.resolve_dimension_id(d) or d for d in spec.dimensions]
        parts = [self._part(p, dims, spec.grain) for p in parts_raw]
        self._check_alignment(parts, dims)
        start, end = resolve_time_range(spec.time_range)

        current = _now_bucket(spec.grain)
        notes: list[str] = []
        partial_bucket = None
        brand = self.brand_key(spec.filters)
        range_end = end.isoformat() if spec.grain == "day" else f"{end.isoformat()} 23:00"
        for p in parts:
            observed = [r[BUCKET] for r in p.rows if r[BUCKET] < current]
            p.watermark = max(observed) if observed else None
            # A source that already holds data after the range has landed the whole range: a bucket with no row in it is a
            # real zero. Sparse sources (orders: 1-4 an hour) cannot be judged from their own last row.
            later = await self.latest_date(p.view, brand) if self.latest_date else None
            if later and str(later)[:10] > end.isoformat():
                p.watermark, p.watermark_basis = range_end, "the source holds later data"
        reaches_now = end.isoformat() >= current[:10]
        if spec.grain == "hour":
            if reaches_now:
                partial_bucket = current
                if spec.include_partial_hour:
                    notes.append(f"The current hour ({current} IST) is partial and is included.")
                else:
                    notes.append(f"The current hour ({current} IST) is partial and is left out; pass include_partial_hour to keep it.")
            if not spec.include_partial_hour:
                for p in parts:
                    p.rows = [r for r in p.rows if r[BUCKET] < current]
        elif reaches_now:
            partial_bucket = current
            notes.append(f"The current day ({current}) is partial: its figures are 'so far'.")

        last = end.isoformat() if spec.grain == "day" else f"{end.isoformat()} 23:00"
        keep_partial = spec.grain == "day" or spec.include_partial_hour
        buckets = [b for b in _spine(start, end, spec.grain, last) if (b <= current if keep_partial else b < current)]
        rows, schema = self._align(parts, dims, buckets)
        notes.extend(self._source_notes(parts))

        sources = [
            {
                "view": p.view,
                "metric_ids": p.metric_ids,
                "query_id": p.query_id,
                "time_axis": p.time_member,
                "rows_returned": p.row_count,
                "latest_complete_bucket": p.watermark,
                "watermark_basis": p.watermark_basis,
                "freshness": p.freshness,
                "warnings": p.warnings,
            }
            for p in parts
        ]
        scope_brand = self.brand_key(spec.filters)
        provenance = {
            "kind": "dataset",
            "timezone": "Asia/Kolkata",
            "grain": spec.grain,
            "time_range": {"start": start.isoformat(), "end": end.isoformat()},
            "partial_bucket": partial_bucket,
            "sources": sources,
            "part_query_ids": [p.query_id for p in parts],
            "notes": notes,
            "limitations": [NOT_MODELLED, *(self.hourly_notes[p.view] for p in parts if spec.grain == "hour" and p.view in self.hourly_notes)],
            "catalogue_version": self.planner.catalogue.version,
        }
        stored = self.store.save(
            name=name,
            brand_key=scope_brand,
            module_key=module_key,
            request={**spec.model_dump(mode="json"), "dimensions": dims},
            schema=schema,
            rows=rows,
            provenance=provenance,
        )
        provenance["query_id"] = stored.handle
        return {
            "query_id": stored.handle,
            "handle": stored.handle,
            "name": name,
            "grain": spec.grain,
            "timezone": "Asia/Kolkata",
            "row_count": len(rows),
            "columns": schema,
            "preview": rows[:PREVIEW_ROWS],
            "stats": _stats(rows, schema),
            "notes": notes,
            "limitations": provenance["limitations"],
            "partial_bucket": partial_bucket,
            "sources": sources,
            "provenance": provenance,
            "how_to_use": (
                "Load all rows with action=get (offset/limit pages), narrow with action=slice, remove with action=delete. "
                "A null cell is missing (after the source's last observed bucket, or a non-additive measure with no rows); "
                "0 is a real zero."
            ),
        }

    # ---------------------------------------------------------------- parts

    def _part(self, p: dict, dims: list[str], grain: str) -> _Part:
        prov = p["provenance"]
        cq = prov.get("cube_query") or {}
        tds = cq.get("timeDimensions") or []
        if not tds or not tds[0].get("granularity"):
            raise DatasetError(
                "unalignable",
                f"View '{prov.get('cube_view')}' (metrics {prov.get('metric_ids')}) has no {grain} time axis, so it "
                "cannot be aligned with the other metrics. Ask for those metrics separately, or use grain=day.",
                view=prov.get("cube_view"),
                metric_ids=prov.get("metric_ids"),
            )
        time_member = tds[0]["dimension"]
        time_col = f"{time_member.split('.', 1)[1]}.{tds[0]['granularity']}"
        plain_time_col = time_member.split(".", 1)[1]
        metric_ids = list(prov.get("metric_ids") or [])
        raw_rows = p.get("rows") or []
        if len(raw_rows) >= self.part_row_cap:
            raise DatasetError(
                "dataset_too_large",
                f"View '{prov.get('cube_view')}' returned {len(raw_rows)} rows, the most one Cube load returns, so the part "
                "may be cut off. Narrow the time range, use grain day, drop a dimension or add a filter.",
                view=prov.get("cube_view"),
                part_row_cap=self.part_row_cap,
            )
        measure_cols: list[str] = []
        rows: list[dict] = []
        dims_seen: set[str] = set()
        for r in raw_rows:
            if time_col not in r or r[time_col] is None:
                continue
            row = {BUCKET: _bucket(r[time_col], grain), "_m": {}}
            for d in dims:
                if d in r:
                    dims_seen.add(d)
                row[d] = r.get(d)
            for k, v in r.items():
                if k in (time_col, plain_time_col) or k in dims:
                    continue
                if k not in measure_cols:
                    measure_cols.append(k)
                row["_m"][k] = _num(v)
            rows.append(row)
        axis_applied = p.get("date_axis_applied") or {}
        axis = {mid: str(axis_applied.get(mid, plain_time_col)) for mid in metric_ids}
        return _Part(
            view=prov.get("cube_view", ""),
            metric_ids=metric_ids,
            query_id=p.get("query_id", ""),
            rows=rows,
            measure_cols=measure_cols,
            time_member=time_member,
            watermark=None,
            watermark_basis="last observed bucket",
            axis=axis,
            freshness=prov.get("freshness") or {},
            warnings=list(p.get("warnings") or []),
            row_count=len(raw_rows),
            dims_seen=dims_seen,
        )

    def _check_alignment(self, parts: list[_Part], dims: list[str]) -> None:
        for p in parts:
            if not p.rows:
                continue
            missing = [d for d in dims if d not in p.dims_seen]
            if missing:
                raise DatasetError(
                    "unalignable",
                    f"Dimension(s) {missing} are not available on view '{p.view}' (metrics {p.metric_ids}), so that "
                    "part cannot be aligned with the rest. Use only dimensions every metric supports, or build "
                    "separate datasets.",
                    view=p.view,
                    metric_ids=p.metric_ids,
                    dimensions=missing,
                )

    def _source_notes(self, parts: list[_Part]) -> list[str]:
        notes = []
        axes = {a for p in parts for a in p.axis.values()}
        if len(axes) > 1:
            notes.append(
                "Columns come from different date clocks (" + ", ".join(sorted(axes)) + "); each column's axis is in "
                "the schema. They share one calendar/hour key but measure different events."
            )
        for p in parts:
            if p.watermark is None:
                notes.append(f"View '{p.view}' returned no complete buckets in this range.")
        return notes

    # ---------------------------------------------------------------- align

    def _align(self, parts: list[_Part], dims: list[str], buckets: list[str]):
        cat = self.planner.catalogue
        schema: list[dict] = [{"name": BUCKET, "role": "time", "timezone": "Asia/Kolkata"}]
        schema += [{"name": d, "role": "dimension"} for d in dims]
        # column -> (part index, additive?)
        col_owner: dict[str, int] = {}
        additive: dict[str, bool] = {}
        for i, p in enumerate(parts):
            for c in p.measure_cols:
                if c in col_owner:
                    continue
                col_owner[c] = i
                m = cat.get_metric(c)
                additive[c] = bool(m is not None and m.aggregation == "additive")
                schema.append(
                    {
                        "name": c,
                        "role": "measure",
                        "metric_id": c if m is not None else None,
                        "source_view": p.view,
                        "axis": p.axis.get(c) or (next(iter(p.axis.values()), None)),
                        "unit": getattr(m, "unit", None) if m is not None else None,
                        "aggregation": getattr(m, "aggregation", None) if m is not None else None,
                        "missing_as": "0 when the source has data at or after the bucket, else null"
                        if additive[c]
                        else "null",
                    }
                )
        merged: dict[tuple, dict] = {}
        for p in parts:
            for r in p.rows:
                key = (r[BUCKET], *[r[d] for d in dims])
                row = merged.setdefault(key, {BUCKET: r[BUCKET], **{d: r[d] for d in dims}})
                for c, v in r["_m"].items():
                    row[c] = v
        if not dims:
            for b in buckets:
                merged.setdefault((b,), {BUCKET: b})
        rows = []
        for key in sorted(merged, key=lambda k: tuple("" if x is None else str(x) for x in k)):
            row = merged[key]
            for c, i in col_owner.items():
                if c in row:
                    continue
                wm = parts[i].watermark
                row[c] = 0 if (additive[c] and wm is not None and row[BUCKET] <= wm) else None
            rows.append(row)
        return rows, schema


# ---------------------------------------------------------------- reads on a stored dataset


def _stats(rows: list[dict], schema: list[dict]) -> dict:
    out: dict = {}
    for c in schema:
        if c["role"] != "measure":
            continue
        vals = [r[c["name"]] for r in rows if isinstance(r.get(c["name"]), int | float)]
        n_null = sum(1 for r in rows if r.get(c["name"]) is None)
        s: dict = {"non_null": len(vals), "null": n_null}
        if vals:
            s.update(min=min(vals), max=max(vals), mean=sum(vals) / len(vals))
            if c.get("aggregation") == "additive":
                s["sum"] = sum(vals)
        out[c["name"]] = s
    return out


def get_page(ds: StoredDataset, *, columns: list[str] | None, offset: int, limit: int | None) -> dict:
    names = [c["name"] for c in ds.schema]
    if columns:
        bad = [c for c in columns if c not in names]
        if bad:
            raise DatasetError("invalid_spec", f"Unknown column(s) {bad}; the dataset has {names}.", columns=names)
        keep = [c for c in names if c in columns or c == BUCKET]
    else:
        keep = names
    lim = min(max(limit or GET_DEFAULT_LIMIT, 1), GET_MAX_LIMIT)
    off = max(offset, 0)
    page = [{k: r.get(k) for k in keep} for r in ds.rows[off : off + lim]]
    nxt = off + lim if off + lim < ds.row_count else None
    return {
        "query_id": ds.handle,
        "handle": ds.handle,
        "name": ds.name,
        "row_count": ds.row_count,
        "offset": off,
        "returned": len(page),
        "next_offset": nxt,
        "columns": [c for c in ds.schema if c["name"] in keep],
        "rows": page,
        "provenance": ds.provenance,
    }


def slice_rows(
    ds: StoredDataset,
    *,
    columns: list[str] | None,
    window: dict | None,
    where: list[dict] | None,
) -> tuple[list[dict], list[dict]]:
    """(rows, schema) of a subset: columns kept, bucket window (inclusive, prefix-comparable), equality filters
    on dimension columns."""
    names = [c["name"] for c in ds.schema]
    roles = {c["name"]: c["role"] for c in ds.schema}
    rows = ds.rows
    if window:
        lo = str(window.get("start") or "")
        hi = str(window.get("end") or "￿")
        # a date-only end covers the whole day; compare on the prefix of each bucket
        rows = [r for r in rows if r[BUCKET][: len(lo)] >= lo and r[BUCKET][: len(hi)] <= hi]
    for w in where or []:
        col = w.get("column")
        if roles.get(col) != "dimension":
            raise DatasetError("invalid_spec", f"'where' filters apply to dimension columns; {col!r} is not one of them.", columns=names)
        vals = {str(v) for v in (w.get("values") or [])}
        rows = [r for r in rows if str(r.get(col)) in vals]
    if columns:
        bad = [c for c in columns if c not in names]
        if bad:
            raise DatasetError("invalid_spec", f"Unknown column(s) {bad}; the dataset has {names}.", columns=names)
        keep = [c for c in names if c in columns or c == BUCKET]
    else:
        keep = names
    schema = [c for c in ds.schema if c["name"] in keep]
    return [{k: r.get(k) for k in keep} for r in rows], schema


def summary(ds: StoredDataset) -> dict:
    return {
        "handle": ds.handle,
        "name": ds.name,
        "row_count": ds.row_count,
        "columns": ds.schema,
        "created_at": ds.created_at,
        "grain": ds.request.get("grain"),
        "stats": _stats(ds.rows, ds.schema),
        "preview": ds.rows[:PREVIEW_ROWS],
        "provenance": ds.provenance,
    }
