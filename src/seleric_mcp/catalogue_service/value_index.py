"""Value resolution: map the words in a question to values that exist in the data.

The catalogue resolves metrics (glossary/search) and dimensions (ids/aliases),
but a user also names *values* — "whatsapp", "bitespeed", "Suspender Boots" —
and nothing mapped those to where they live. The catalogue cannot hold them: new
channels, campaigns and products appear in the data every week and nobody should
hand-maintain a list.

So the index is learned from Cube, not declared:

* For every non-time catalogue dimension, on every view that has a date axis
  and a queryable additive metric, fetch the distinct values and their volume
  over a rolling window (one grouped Cube query per (dimension, view)).
* Dimensions whose values are numbers, booleans or opaque ids are dropped by
  looking at the values themselves — no hand-written include/exclude list.
* Rebuilt in the background after ``ttl_s``; a value that first appears in the
  data today is resolvable after the next rebuild with no catalogue edit.

Nothing here names a value, dimension or view, and there is no stopword list:
a word only counts as a partial (token/contains/fuzzy/abbreviation) match in a
dimension when it is *selective* there — it picks out at most
``max_share`` of that dimension's values. "the" hits a third of product titles
and is dropped; "whatsapp" hits one utm_medium value and is kept. Words the
catalogue already owns (metric/dimension/glossary names) resolve only on exact
matches, an abbreviation is only trusted when the full form sits in the same
dimension, and a partial match needs activity in the window.

Matching is deliberately generic (``match_value``): exact, whole-token,
substring, close spelling, and abbreviation (a 2–4 char value that starts like
the term and whose letters occur in it in order: ``wa`` → ``whatsapp``). The
index proposes candidates; interpreting them is the caller's job (the agent's
LLM, which also sees each dimension's top values), and the answer must state the
mapping it used.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from ..app.query_planner import _ist_today

if TYPE_CHECKING:
    from ..semantic_layer.cube_client import CubeClient
    from ..storage.db import Database
    from .service import CatalogueService

log = structlog.get_logger()

_NUMERIC_RE = re.compile(r"^[+-]?\d+([.,]\d+)?$")
_ID_RE = re.compile(r"^[0-9a-f]{8,}$|^\d{6,}$|^[0-9a-f-]{32,36}$")
_BOOLISH = frozenset({"true", "false", "yes", "no", "0", "1", "t", "f", "y", "n"})


def normalize(value: Any) -> str:
    """Lowercase, URL-decode (``Cross+Sell+-+WA``), punctuation → single spaces."""
    text = urllib.parse.unquote_plus(str(value if value is not None else ""))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _is_abbreviation(short: str, term: str) -> bool:
    s = short.replace(" ", "")
    t = term.replace(" ", "")
    if not (2 <= len(s) <= 4) or len(t) < 5 or s[0] != t[0]:
        return False
    it = iter(t)
    return all(ch in it for ch in s)


def match_value(term: str, value_norm: str, fuzzy_threshold: float = 0.85) -> str | None:
    """How ``value_norm`` matches ``term`` (both normalized), strongest first."""
    if not term or not value_norm:
        return None
    if term == value_norm:
        return "exact"
    tokens = value_norm.split()
    term_tokens = term.split()
    if len(term_tokens) == 1 and term in tokens:
        return "token"
    if len(term_tokens) > 1 and all(t in tokens for t in term_tokens):
        return "token"
    compact_term = term.replace(" ", "")
    if len(compact_term) >= 5 and compact_term in value_norm.replace(" ", ""):
        return "contains"
    if len(compact_term) >= 5:
        # A high ratio is impossible unless lengths are close — skip the costly
        # SequenceMatcher for everything else (the index holds 10^4–10^5 values).
        la, lb = len(term), len(value_norm)
        if 2 * min(la, lb) / (la + lb) >= fuzzy_threshold:
            sm = difflib.SequenceMatcher(None, term, value_norm)
            if sm.quick_ratio() >= fuzzy_threshold and sm.ratio() >= fuzzy_threshold:
                return "fuzzy"
    if _is_abbreviation(value_norm, term):
        return "abbreviation"
    return None


_MATCH_RANK = {"exact": 0, "token": 1, "fuzzy": 2, "contains": 3, "abbreviation": 4}


def candidate_terms(text: str, skip: frozenset[str]) -> list[str]:
    """Every 3/2/1-gram of the question's words, longest first. No stopword
    list: words that match nothing cost nothing, and words that match too
    broadly are dropped by the selectivity rule in ``ValueIndex.resolve``.
    Structural filters only — numbers, 1–2 char tokens, brand names (resolved
    separately)."""
    words = [
        w for w in normalize(text).split()
        if len(w) >= 3 and not _NUMERIC_RE.match(w) and w not in skip
    ]
    terms: list[str] = []
    for n in (3, 2, 1):
        for i in range(0, len(words) - n + 1):
            term = " ".join(words[i : i + n])
            if term not in terms:
                terms.append(term)
    return terms


def _is_opaque(value: str) -> bool:
    """Numbers, ids and booleans are not words a user types to name a value."""
    compact = normalize(value).replace(" ", "")
    return (
        not compact
        or compact == "none"
        or bool(_NUMERIC_RE.match(compact))
        or bool(_ID_RE.match(compact))
        or compact in _BOOLISH
    )


def label_values(values: dict[str, float]) -> dict[str, float]:
    """Keep only label-like values. Data-driven, per value: a dimension such as
    utm_medium mixes labels (pmax, whatsapp) with ad-set ids written into the
    UTM tag, so dropping or keeping the whole dimension would be wrong."""
    return {raw: vol for raw, vol in values.items() if not _is_opaque(raw)}


@dataclass(frozen=True)
class Target:
    dimension: str
    view: str
    member: str
    measure: str
    measure_metric: str
    date_member: str


@dataclass
class Snapshot:
    brand_id: str | None
    start: date
    end: date
    built_at: float
    # (dimension, view) -> {raw value: volume}
    values: dict[tuple[str, str], dict[str, float]] = field(default_factory=dict)
    measure_metric: dict[tuple[str, str], str] = field(default_factory=dict)
    failed: int = 0
    _by_norm: dict[str, list[tuple[tuple[str, str], str, float]]] | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "brand_id": self.brand_id,
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "values": [[d, v, vals] for (d, v), vals in self.values.items()],
                "measure_metric": [[d, v, m] for (d, v), m in self.measure_metric.items()],
                "failed": self.failed,
            }
        )

    @classmethod
    def from_json(cls, payload: str, *, built_at: float) -> Snapshot:
        data = json.loads(payload)
        return cls(
            brand_id=data.get("brand_id"),
            start=date.fromisoformat(data["start"]),
            end=date.fromisoformat(data["end"]),
            built_at=built_at,
            values={(d, v): dict(vals) for d, v, vals in data.get("values", [])},
            measure_metric={(d, v): m for d, v, m in data.get("measure_metric", [])},
            failed=int(data.get("failed", 0)),
        )

    def by_norm(self) -> dict[str, list[tuple[tuple[str, str], str, float]]]:
        """normalized value -> [((dimension, view), raw value, volume)], built once."""
        if self._by_norm is None:
            index: dict[str, list[tuple[tuple[str, str], str, float]]] = {}
            for key, values in self.values.items():
                for raw, volume in values.items():
                    index.setdefault(normalize(raw), []).append((key, raw, volume))
            self._by_norm = index
        return self._by_norm


class ValueIndex:
    def __init__(
        self,
        catalogue: CatalogueService,
        cube: CubeClient,
        *,
        default_brand_id: str | None = None,
        window_days: int = 180,
        row_cap: int = 1000,
        ttl_s: float = 6 * 3600,
        concurrency: int = 6,
        fuzzy_threshold: float = 0.85,
        max_share: float = 0.2,
        db: Database | None = None,
    ) -> None:
        self.catalogue = catalogue
        self.cube = cube
        self.default_brand_id = default_brand_id
        self.window_days = window_days
        self.row_cap = row_cap
        self.ttl_s = ttl_s
        self.concurrency = concurrency
        self.fuzzy_threshold = fuzzy_threshold
        self.max_share = max_share
        self.db = db
        self._snapshots: dict[str | None, Snapshot] = {}
        self._builds: dict[str | None, asyncio.Task] = {}
        # One Cube concurrency budget shared by every brand's build, so warming
        # all brands after a restart doesn't multiply the load on Cube.
        self._sem: asyncio.Semaphore | None = None
        self._load_persisted()

    # ---------- persistence ----------

    def _load_persisted(self) -> None:
        """Serve the last built index from the first request after a restart.
        A snapshot built against a different catalogue version is still loaded
        (values don't change with the catalogue) but marked stale so the next
        warm() rebuilds it."""
        if self.db is None:
            return
        try:
            rows = self.db.fetchall(
                "SELECT brand_key, catalogue_version, built_at, payload_json FROM value_index_snapshots"
            )
        except Exception as exc:
            log.warning("value_index_load_failed", error=repr(exc))
            return
        for row in rows:
            try:
                built_at = float(row["built_at"])
                if row["catalogue_version"] != self.catalogue.cat.version:
                    built_at = 0.0
                snap = Snapshot.from_json(row["payload_json"], built_at=built_at)
            except Exception as exc:
                log.warning("value_index_snapshot_unreadable", brand_key=row["brand_key"], error=repr(exc))
                continue
            self._snapshots[row["brand_key"] or None] = snap
        if rows:
            log.info("value_index_loaded", brands=[r["brand_key"] or None for r in rows])

    def _save(self, snap: Snapshot) -> None:
        if self.db is None:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO value_index_snapshots (brand_key, catalogue_version, built_at, payload_json) "
            "VALUES (?, ?, ?, ?)",
            (snap.brand_id or "", self.catalogue.cat.version, snap.built_at, snap.to_json()),
        )

    # ---------- build ----------

    def targets(self, agg_types: dict[str, str] | None = None) -> list[Target]:
        """One target per (dimension, view). Volume is counted with a catalogue
        metric on that view, preferring one Cube reports as a count
        (``aggType`` count/countDistinct, from Cube /meta) so volumes read as
        "how many", not revenue; any additive metric otherwise."""
        cat = self.catalogue.cat
        agg_types = agg_types or {}
        measure_for_view: dict[str, tuple[str, str]] = {}
        candidates = sorted(
            (m for m in cat.metrics.values() if m.is_queryable and m.aggregation == "additive"),
            key=lambda m: (
                agg_types.get(m.cube_mapping.measure) not in ("count", "countDistinct"),
                m.id,
            ),
        )
        for m in candidates:
            measure_for_view.setdefault(m.cube_mapping.view, (m.cube_mapping.measure, m.id))
        out: list[Target] = []
        for dim in cat.dimensions.values():
            if dim.is_time or dim.id == "brand_id":
                continue
            for view, member in dim.views.items():
                view_def = cat.views.get(view)
                if view_def is None or not view_def.date_dimension or view not in measure_for_view:
                    continue
                measure, metric_id = measure_for_view[view]
                out.append(
                    Target(
                        dimension=dim.id,
                        view=view,
                        member=member,
                        measure=measure,
                        measure_metric=metric_id,
                        date_member=f"{view}.{view_def.date_dimension}",
                    )
                )
        return out

    async def _load_target(self, target: Target, brand_id: str | None, start: date, end: date) -> dict[str, float] | None:
        query: dict[str, Any] = {
            "measures": [target.measure],
            "dimensions": [target.member],
            "timeDimensions": [
                {"dimension": target.date_member, "dateRange": [start.isoformat(), end.isoformat()]}
            ],
            "order": {target.measure: "desc"},
            "limit": self.row_cap,
        }
        brand_dim = self.catalogue.cat.dimensions.get("brand_id")
        brand_member = brand_dim.views.get(target.view) if brand_dim else None
        if brand_id and brand_member:
            query["filters"] = [{"member": brand_member, "operator": "equals", "values": [brand_id]}]
        result = await self.cube.load(query)
        values: dict[str, float] = {}
        for row in result.data:
            raw = row.get(target.member)
            if raw is None or str(raw).strip() == "":
                continue
            try:
                volume = float(row.get(target.measure) or 0)
            except (TypeError, ValueError):
                volume = 0.0
            values[str(raw)] = values.get(str(raw), 0.0) + volume
        return values

    async def build(self, brand_id: str | None) -> Snapshot:
        end = _ist_today()
        start = end - timedelta(days=self.window_days)
        snap = Snapshot(brand_id=brand_id, start=start, end=end, built_at=time.time())
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.concurrency)
        sem = self._sem
        started = time.monotonic()

        async def one(t: Target) -> None:
            async with sem:
                try:
                    values = await self._load_target(t, brand_id, start, end)
                except Exception as exc:  # one bad view must not sink the index
                    snap.failed += 1
                    log.warning("value_index_target_failed", dimension=t.dimension, view=t.view, error=repr(exc))
                    return
            labels = label_values(values or {})
            if labels:
                snap.values[(t.dimension, t.view)] = labels
                snap.measure_metric[(t.dimension, t.view)] = t.measure_metric

        try:
            meta = await self.cube.meta()
            agg_types = {
                m["name"]: str(m.get("aggType") or "")
                for c in meta.get("cubes", [])
                for m in c.get("measures", [])
            }
        except Exception as exc:
            log.warning("value_index_meta_failed", error=repr(exc))
            agg_types = {}
        targets = self.targets(agg_types)
        await asyncio.gather(*(one(t) for t in targets))
        log.info(
            "value_index_built",
            brand_id=brand_id,
            targets=len(targets),
            kept=len(snap.values),
            failed=snap.failed,
            distinct_values=sum(len(v) for v in snap.values.values()),
            elapsed_s=round(time.monotonic() - started, 2),
        )
        return snap

    async def _build_and_store(self, brand_id: str | None) -> Snapshot:
        try:
            snap = await self.build(brand_id)
            self._snapshots[brand_id] = snap
            try:
                await asyncio.to_thread(self._save, snap)
            except Exception as exc:  # persistence is an optimisation, never fatal
                log.warning("value_index_save_failed", brand_id=brand_id, error=repr(exc))
            return snap
        finally:
            self._builds.pop(brand_id, None)

    def warm(self, brand_id: str | None) -> None:
        """Start a background build if the brand's index is missing or stale.
        Never blocks; a no-op outside a running event loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        snap = self._snapshots.get(brand_id)
        fresh = snap is not None and (time.time() - snap.built_at) < self.ttl_s
        if not fresh and brand_id not in self._builds:
            self._builds[brand_id] = asyncio.create_task(self._build_and_store(brand_id))

    def warm_all(self) -> None:
        """warm() every active brand (the default first)."""
        self.warm(self.default_brand_id)
        for brand in self.catalogue.list_brands():
            self.warm(brand.id)

    async def locate_live(
        self, brand_id: str | None, wanted: list[str], *, timeout_s: float = 10.0
    ) -> list[dict[str, Any]] | None:
        """Where do these exact values occur *right now*? One small Cube query
        per indexed label dimension, bounded by ``timeout_s``. Found values are
        merged into the snapshot, so a value that appeared after the last
        rebuild is known from its first question. None = could not check."""
        snap = self._snapshots.get(brand_id)
        if snap is None:
            return None
        wanted_norm = {normalize(w) for w in wanted}
        by_key = {(t.dimension, t.view): t for t in self.targets()}
        keys = [k for k in snap.values if k in by_key]
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.concurrency)
        sem = self._sem
        found: list[dict[str, Any]] = []

        async def probe(key: tuple[str, str]) -> None:
            t = by_key[key]
            query: dict[str, Any] = {
                "measures": [t.measure],
                "dimensions": [t.member],
                "filters": [{"member": t.member, "operator": "equals", "values": list(wanted)}],
                "timeDimensions": [
                    {"dimension": t.date_member, "dateRange": [snap.start.isoformat(), _ist_today().isoformat()]}
                ],
                "limit": 20,
            }
            brand_dim = self.catalogue.cat.dimensions.get("brand_id")
            brand_member = brand_dim.views.get(t.view) if brand_dim else None
            if brand_id and brand_member:
                query["filters"].append({"member": brand_member, "operator": "equals", "values": [brand_id]})
            async with sem:
                result = await self.cube.load(query)
            hits = {
                str(row.get(t.member)): float(row.get(t.measure) or 0)
                for row in result.data
                if row.get(t.member) is not None and normalize(row.get(t.member)) in wanted_norm
            }
            if hits:
                found.append({"dimension": t.dimension, "view": t.view, "values": sorted(hits)})
                merged = dict(snap.values.get(key, {}))
                merged.update(hits)
                snap.values[key] = merged
                snap._by_norm = None

        try:
            await asyncio.wait_for(
                asyncio.gather(*(probe(k) for k in keys), return_exceptions=True), timeout=timeout_s
            )
        except (TimeoutError, asyncio.TimeoutError):
            log.warning("value_index_live_probe_timeout", values=wanted, checked=len(keys))
            return None
        return found

    async def verify_filter_values(
        self, brand_id: str | None, view: str, filters: list[tuple[str, list[str]]]
    ) -> list[dict[str, Any]]:
        """Filters that produced a zero/empty result because the value does not
        occur in that dimension (Cube answers such a filter with a 0 row).

        The caller only asks for zero/empty results — a filter that returned
        real numbers proves its value exists. A value in the snapshot for that
        dimension is accepted as is; any other value is checked against Cube
        live before anything is claimed, so a value newer than the last rebuild
        is never reported missing. ids/numbers are skipped (labels only)."""
        snap = self._snapshots.get(brand_id)
        if snap is None:
            return []
        problems: list[dict[str, Any]] = []
        for dimension, wanted in filters:
            if not wanted or all(_is_opaque(w) for w in wanted):
                continue
            wanted_norm = {normalize(w) for w in wanted}
            known = snap.values.get((dimension, view)) or {}
            if wanted_norm & {normalize(v) for v in known}:
                continue
            found = await self.locate_live(brand_id, wanted)
            if found is None:
                continue  # could not check — make no claim
            if any(f["dimension"] == dimension and f["view"] == view for f in found):
                continue  # it exists here; the zero is real
            problems.append(
                {"dimension": dimension, "view": view, "values": list(wanted), "found_in": found[:6]}
            )
        return problems

    async def get(self, brand_id: str | None, *, wait_s: float) -> Snapshot | None:
        """Current snapshot for the brand. Missing → build, waiting up to wait_s.
        Stale → serve the old one and rebuild in the background."""
        snap = self._snapshots.get(brand_id)
        fresh = snap is not None and (time.time() - snap.built_at) < self.ttl_s
        if not fresh and brand_id not in self._builds:
            self._builds[brand_id] = asyncio.create_task(self._build_and_store(brand_id))
        if snap is not None:
            return snap
        try:
            return await asyncio.wait_for(asyncio.shield(self._builds[brand_id]), timeout=wait_s)
        except (TimeoutError, asyncio.TimeoutError):
            return None

    # ---------- resolve ----------

    def _vocabulary(self) -> frozenset[str]:
        """Words the catalogue already owns (metric/dimension/glossary names)."""
        cat = self.catalogue.cat
        words: set[str] = set()
        texts: list[str] = []
        for m in cat.metrics.values():
            texts += [m.id, m.display_name]
        for d in cat.dimensions.values():
            texts += [d.id, d.display_name, *d.aliases]
        for g in cat.glossary:
            texts.append(g.term)
        for t in texts:
            words.update(normalize(t).split())
        return frozenset(words)

    def _brand_words(self) -> frozenset[str]:
        words: set[str] = set()
        for b in self.catalogue.list_brands(include_test=True):
            for form in (b.name, b.code or "", *b.aliases):
                words.update(normalize(form).split())
        return frozenset(words)

    def brand_in_text(self, text: str) -> str | None:
        norm = f" {normalize(text)} "
        for b in self.catalogue.list_brands():
            for form in (b.name, b.code or "", *b.aliases):
                f = normalize(form)
                if f and f" {f} " in norm:
                    return b.id
        return None

    def _metrics_for(self, dimension: str, view: str, limit: int = 5) -> list[str]:
        cat = self.catalogue.cat
        return [
            m.id
            for m in sorted(cat.metrics.values(), key=lambda m: m.id)
            if m.is_queryable and m.cube_mapping.view == view
        ][:limit] if dimension in cat.dimensions else []

    def resolve(
        self,
        text: str,
        snap: Snapshot,
        *,
        max_dimensions: int = 6,
        max_values: int = 8,
        top_values: int = 8,
    ) -> dict[str, Any]:
        vocab = self._vocabulary()
        terms = candidate_terms(text, skip=self._brand_words())
        by_norm = snap.by_norm()

        resolved: list[dict[str, Any]] = []
        unmatched: list[str] = []
        claimed: set[tuple[tuple[str, str], str]] = set()
        for term in terms:
            is_vocab = all(w in vocab for w in term.split())
            multi_word = len(term.split()) > 1
            hits: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for value_norm, entries in by_norm.items():
                how = match_value(term, value_norm, self.fuzzy_threshold)
                if how is None:
                    continue
                # Words the catalogue owns ("orders", "products") only name a
                # value when they ARE the value, never inside a URL or title.
                if is_vocab and how != "exact":
                    continue
                if multi_word and how == "abbreviation":
                    continue
                for key, raw, volume in entries:
                    if (key, raw) in claimed:  # a longer n-gram already owns it
                        continue
                    # A partial match on a value with no activity in the window
                    # is not what the user is asking about.
                    if how != "exact" and volume <= 0:
                        continue
                    hits.setdefault(key, []).append(
                        {
                            "value": raw,
                            "volume": volume,
                            "match": how,
                            # Share of the value's words the term accounts for:
                            # "pawtech" is half of "PawTech Cleaner" but a quarter
                            # of "TH-336-PAWTECH-7JULY". Comparable across views,
                            # unlike volume (each view counts a different metric).
                            "coverage": round(len(term.split()) / max(1, len(value_norm.split())), 3),
                        }
                    )
            # Abbreviations are only trusted next to the full form: "wa" counts
            # in a dimension that also holds "whatsapp"; "MH" in state codes has
            # no "months" beside it and is dropped.
            for key in list(hits):
                if not any(h["match"] != "abbreviation" for h in hits[key]):
                    del hits[key]
            # Selectivity: a partial match that covers a large share of a
            # dimension's values says nothing about which value was meant.
            for key in list(hits):
                partial = [h for h in hits[key] if h["match"] != "exact"]
                size = len(snap.values[key])
                if partial and size >= 10 and len(partial) / size > self.max_share:
                    hits[key] = [h for h in hits[key] if h["match"] == "exact"]
                if not hits[key]:
                    del hits[key]
            if not hits:
                if len(term.split()) == 1:
                    unmatched.append(term)
                continue
            dims: list[dict[str, Any]] = []
            for key, matches in hits.items():
                matches.sort(key=lambda h: (_MATCH_RANK[h["match"]], -h["coverage"], -h["volume"]))
                for h in matches:
                    claimed.add((key, h["value"]))
                dimension, view = key
                all_values = snap.values[key]
                dims.append(
                    {
                        "dimension": dimension,
                        "view": view,
                        "volume_metric": snap.measure_metric.get(key),
                        "values": matches[:max_values],
                        "top_values": [
                            {"value": v, "volume": vol}
                            for v, vol in sorted(all_values.items(), key=lambda kv: -kv[1])[:top_values]
                        ],
                        "metrics": self._metrics_for(dimension, view),
                        "_rank": (
                            _MATCH_RANK[matches[0]["match"]],
                            -max(h["coverage"] for h in matches),
                            -sum(h["volume"] for h in matches),
                        ),
                    }
                )
            dims.sort(key=lambda d: d["_rank"])
            # One entry per dimension: the same dimension on several views
            # (campaign_name on 5 ad/session views) must not crowd out a
            # different kind of match (the product title) from the shortlist.
            grouped: dict[str, dict[str, Any]] = {}
            for d in dims:
                d.pop("_rank")
                g = grouped.get(d["dimension"])
                if g is None:
                    grouped[d["dimension"]] = {**d, "views": [d["view"]]}
                    continue
                g["views"].append(d["view"])
                seen = {v["value"] for v in g["values"]}
                g["values"] += [v for v in d["values"] if v["value"] not in seen]
                g["values"] = sorted(
                    g["values"], key=lambda h: (_MATCH_RANK[h["match"]], -h["coverage"], -h["volume"])
                )[:max_values]
                g["metrics"] = list(dict.fromkeys(g["metrics"] + d["metrics"]))[:8]
            dims = list(grouped.values())
            best = dims[0]["values"][0]["match"]
            resolved.append(
                {
                    "term": term,
                    "catalogue_vocabulary": is_vocab,
                    "best_match": best,
                    "dimensions": dims[:max_dimensions],
                }
            )
        # Most telling terms first: exact matches on words the catalogue does
        # not own, then by how much of the matched value the term is.
        resolved.sort(
            key=lambda t: (
                _MATCH_RANK[t["best_match"]],
                t["catalogue_vocabulary"],
                -max(v["coverage"] for d in t["dimensions"] for v in d["values"]),
            )
        )
        return {
            "status": "ok",
            "brand_id": snap.brand_id,
            "window": {"start": snap.start.isoformat(), "end": snap.end.isoformat()},
            "index_built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(snap.built_at)),
            "terms": resolved,
            "unmatched_terms": [t for t in unmatched if not any(t in r["term"].split() for r in resolved)],
        }
