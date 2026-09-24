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
        self._snapshots: dict[str | None, Snapshot] = {}
        self._builds: dict[str | None, asyncio.Task] = {}

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
        sem = asyncio.Semaphore(self.concurrency)
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
            return snap
        finally:
            self._builds.pop(brand_id, None)

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
                    hits.setdefault(key, []).append({"value": raw, "volume": volume, "match": how})
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
                matches.sort(key=lambda h: (_MATCH_RANK[h["match"]], -h["volume"]))
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
                        "_rank": (_MATCH_RANK[matches[0]["match"]], -sum(h["volume"] for h in matches)),
                    }
                )
            dims.sort(key=lambda d: d["_rank"])
            for d in dims:
                d.pop("_rank")
            best = dims[0]["values"][0]["match"]
            resolved.append(
                {
                    "term": term,
                    "catalogue_vocabulary": is_vocab,
                    "best_match": best,
                    "dimensions": dims[:max_dimensions],
                }
            )
        return {
            "status": "ok",
            "brand_id": snap.brand_id,
            "window": {"start": snap.start.isoformat(), "end": snap.end.isoformat()},
            "index_built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(snap.built_at)),
            "terms": resolved,
            "unmatched_terms": [t for t in unmatched if not any(t in r["term"].split() for r in resolved)],
        }
