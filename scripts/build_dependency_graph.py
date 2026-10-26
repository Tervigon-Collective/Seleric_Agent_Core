"""Build the metric dependency graph: one typed graph of every catalogue v2 metric and how they relate.

Sources, each with its own standard of evidence (the `basis` of every edge says which one it came from):

    catalogue/metrics/*.yaml        formula.composition, ratio_components, depends_on        (definitional)
    catalogue/concepts/*.yaml       date / scope axes of one concept -> same quantity twins  (definitional)
    catalogue_src/causal_priors.yaml mechanisms, feedback, co-measures, subsets, allocations, tiers (reviewed prior)
    catalogue/hierarchies.yaml, dimensions/core.yaml                                        (where-graph)

Outputs doc/dependency_graph/dependency_graph.json (for the agent's diagnosis engine) and DEPENDENCY_GRAPH.md
(for people). Exit 1 if a validation fails: unknown ids, a causal edge between two measurements of the same
quantity, a lag-0 mechanism against the funnel order, a lag-0 cycle, or a mechanism on an allocated metric.

    uv run python scripts/build_dependency_graph.py [--check]   # --check: validate only, write nothing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CAT = ROOT / "catalogue"
SRC = ROOT / "catalogue_src"
OUT = ROOT / "doc" / "dependency_graph"

# Concept axes that change only how a quantity is dated or at what grain it is counted, not what it is.
_TWIN_AXES = {"date", "scope"}
_TWIN_KIND = {"date": "date_basis", "scope": "grain"}

# Edge kinds that say "these two are the same events": no causal edge may join two nodes linked by one.
SAME_QUANTITY = {"twin", "co_measure", "allocation"}
CAUSAL = {"mechanism", "feedback"}


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def load_metrics() -> dict[str, dict]:
    out = {}
    for f in sorted((CAT / "metrics").glob("*.yaml")):
        d = _load_yaml(f)
        out[d["id"]] = d
    return out


def _date_axis(m: dict) -> str:
    hit = re.search(r"Date axis: (\w+)", m.get("description", ""))
    return hit.group(1) if hit else ""


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self._seen: set[tuple] = set()
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def edge(self, src: str, dst: str, kind: str, basis: str, confidence: str, **attrs) -> None:
        if src == dst:  # two ids of one metric (a base and its event-date alias) once they are the same node
            return
        for end in (src, dst):
            if end not in self.nodes:
                self.errors.append(f"{kind} edge {src} -> {dst} ({basis}): unknown metric {end!r}")
                return
        key = (src, dst, kind, attrs.get("role"))
        if key in self._seen:
            return
        self._seen.add(key)
        self.edges.append({"from": src, "to": dst, "kind": kind, "basis": basis, "confidence": confidence,
                           **{k: v for k, v in attrs.items() if v not in (None, "", [])}})


def _canon(node, to_base: dict[str, str]):
    if isinstance(node, str):
        return to_base.get(node, node)
    if isinstance(node, list):
        return [_canon(x, to_base) for x in node]
    if isinstance(node, dict):
        return {to_base.get(k, k) if isinstance(k, str) else k: _canon(v, to_base) for k, v in node.items()}
    return node


def build() -> tuple[Graph, dict]:
    metrics = load_metrics()
    to_base = {a["id"]: a["metric"] for a in _load_yaml(CAT / "metric_aliases.yaml").get("metric_aliases", [])}
    # The priors and concepts were written with the retired event-date ids (pnl_net_profit ...); an alias is its base metric
    # on another date axis, so it is the same node here.
    concepts = _canon([_load_yaml(f) for f in sorted((CAT / "concepts").glob("*.yaml"))], to_base)
    priors = _canon(_load_yaml(SRC / "causal_priors.yaml"), to_base)
    views = {v["name"]: v for v in _load_yaml(CAT / "views.yaml").get("views", [])}
    hierarchies = _load_yaml(CAT / "hierarchies.yaml").get("hierarchies", {})
    dimensions = _load_yaml(CAT / "dimensions" / "core.yaml").get("dimensions", [])
    g = Graph()

    # ---------------------------------------------------------------- nodes
    view_tiers = priors.get("view_tiers", {})
    overrides = priors.get("tier_overrides", {})
    allocated = {a["allocated"] for a in priors.get("allocations", [])}
    for mid, m in metrics.items():
        view = m["cube_mapping"]["view"]
        rc = m.get("ratio_components") or {}
        g.nodes[mid] = {
            "id": mid,
            "label": m.get("display_name", mid),
            "view": view,
            "category": m.get("category"),
            "aggregation": m.get("aggregation"),
            "unit": m.get("unit"),
            "grain": m.get("grain"),
            "date_axis": _date_axis(m) or views.get(view, {}).get("date_dimension", ""),
            "status": m.get("status"),
            "tier": overrides.get(mid, view_tiers.get(view)),
            "allocated": mid in allocated,
            "formula": m.get("formula", {}).get("human_readable", ""),
            "ratio": {"numerator": rc.get("numerator"), "denominator": rc.get("denominator")} if rc else None,
        }
    for mid in overrides:
        if mid not in metrics:
            g.errors.append(f"tier_overrides: unknown metric {mid!r}")

    # ---------------------------------------------------------------- definitional: catalogue formulas
    for mid, m in metrics.items():
        f = m.get("formula") or {}
        covered: set[str] = set()
        for c in f.get("composition") or []:
            g.edge(c["metric"], mid, "composition", "catalogue:formula.composition", "definitional",
                   sign=int(c["sign"]), verified_by="scripts/verify_compositions.py")
            covered.add(c["metric"])
        rc = m.get("ratio_components") or {}
        for role in ("numerator", "denominator"):
            if rc.get(role):
                g.edge(rc[role], mid, "ratio_component", "catalogue:ratio_components", "definitional",
                       role=role, verified_by="scripts/verify_ratio_components.py")
                covered.add(rc[role])
        for dep in f.get("depends_on") or []:
            if dep not in covered:
                # A declared input whose role in the formula the catalogue does not state (often one side of a
                # ratio whose other side is not a catalogue metric, e.g. conversion_rate <- sessions).
                g.edge(dep, mid, "declared_input", "catalogue:formula.depends_on", "definitional_partial")

    # ---------------------------------------------------------------- definitional: concept twins
    for c in concepts:
        groups: dict[tuple, list[tuple[dict, str]]] = defaultdict(list)
        for r in c.get("resolves", []):
            when = r.get("when") or {}
            key = tuple(sorted((k, v) for k, v in when.items() if k not in _TWIN_AXES))
            groups[key].append((when, r["metric"]))
        for members in groups.values():
            ids = []
            for when, mid in members:
                if mid not in ids:
                    ids.append(mid)
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    wa = next(w for w, x in members if x == a)
                    wb = next(w for w, x in members if x == b)
                    differs = sorted(k for k in _TWIN_AXES if wa.get(k) != wb.get(k)) or ["scope"]
                    g.edge(a, b, "twin", f"concepts:{c['id']}", "definitional",
                           twin_of=[_TWIN_KIND[k] for k in differs])

    for rule in priors.get("date_twin_prefixes", []):
        o, fdate = rule["order_date"], rule["finance_date"]
        for mid in metrics:
            if mid.startswith(o) and not mid.startswith(fdate):
                other = fdate + mid[len(o):]
                if other in metrics and other != mid:
                    g.edge(mid, other, "twin", "causal_priors:date_twin_prefixes", "definitional",
                           twin_of=["date_basis"])

    # ---------------------------------------------------------------- reviewed priors
    for e in priors.get("co_measures", []):
        g.edge(e["a"], e["b"], "co_measure", "causal_priors:co_measures", "reviewed",
               systems=e.get("systems"), expected=e.get("expected"))
    for e in priors.get("subsets", []):
        g.edge(e["part"], e["whole"], "subset", "causal_priors:subsets", "reviewed", note=e.get("note"))
    for e in priors.get("allocations", []):
        g.edge(e["source"], e["allocated"], "allocation", "causal_priors:allocations", "reviewed", key=e.get("key"))
    for kind in ("mechanisms", "feedback"):
        for e in priors.get(kind, []):
            g.edge(e["from"], e["to"], kind.rstrip("s") if kind == "mechanisms" else "feedback",
                   f"causal_priors:{kind}", e.get("confidence", "moderate"),
                   sign=e.get("sign"), lag_days=e.get("lag_days"), rate=e.get("rate"), note=e.get("note"))
            if e.get("rate") and e["rate"] not in metrics:
                g.errors.append(f"{kind} {e['from']} -> {e['to']}: unknown rate metric {e['rate']!r}")

    # A twin (same quantity on another date basis or grain) sits at the same funnel tier as its overridden sibling.
    twins = _components(g, {"twin", "co_measure"})
    tier_of_group: dict[int, int] = {}
    for mid, t in overrides.items():
        if mid in g.nodes:
            tier_of_group.setdefault(twins[mid], t)
    for mid, node in g.nodes.items():
        if mid not in overrides and twins[mid] in tier_of_group:
            node["tier"] = tier_of_group[twins[mid]]

    validate(g)
    where = build_where(hierarchies, dimensions)
    where["tiers"] = {int(k): v for k, v in (priors.get("tiers") or {}).items()}
    return g, where


# ---------------------------------------------------------------------------- validation
def _components(g: Graph, kinds: set[str]) -> dict[str, int]:
    parent = {n: n for n in g.nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in g.edges:
        if e["kind"] in kinds:
            parent[find(e["from"])] = find(e["to"])
    roots: dict[str, int] = {}
    return {n: roots.setdefault(find(n), len(roots)) for n in g.nodes}


def validate(g: Graph) -> None:
    same = _components(g, {"twin", "co_measure"})
    for e in g.edges:
        if e["kind"] not in CAUSAL:
            continue
        a, b = e["from"], e["to"]
        if same[a] == same[b]:
            g.errors.append(f"{e['kind']} {a} -> {b}: both measure the same quantity (twin / co-measure group)")
        if g.nodes[a]["allocated"] or g.nodes[b]["allocated"]:
            g.errors.append(f"{e['kind']} {a} -> {b}: allocated metrics cannot carry a causal edge")
        lag = e.get("lag_days") or [0, 0]
        if e["kind"] == "feedback" and lag[0] < 1:
            g.errors.append(f"feedback {a} -> {b}: feedback must be lagged (min lag >= 1 day)")
        ta, tb = g.nodes[a]["tier"], g.nodes[b]["tier"]
        if e["kind"] == "mechanism" and lag[0] == 0 and ta is not None and tb is not None and ta > tb:
            g.errors.append(f"mechanism {a} (tier {ta}) -> {b} (tier {tb}): lag-0 edge against the funnel order")
    # lag-0 mechanism graph must be acyclic
    adj: dict[str, list[str]] = defaultdict(list)
    for e in g.edges:
        if e["kind"] == "mechanism" and (e.get("lag_days") or [0])[0] == 0:
            adj[e["from"]].append(e["to"])
    state: dict[str, int] = {}

    def dfs(n: str, path: list[str]) -> None:
        state[n] = 1
        for m in adj[n]:
            if state.get(m) == 1:
                g.errors.append("lag-0 mechanism cycle: " + " -> ".join(path + [n, m]))
            elif m not in state:
                dfs(m, path + [n])
        state[n] = 2

    for n in list(adj):
        if n not in state:
            dfs(n, [])
    for n, node in g.nodes.items():
        if node["tier"] is None:
            g.warnings.append(f"{n}: no tier (view {node['view']} has no view_tiers entry)")


# ---------------------------------------------------------------------------- where-graph
def build_where(hierarchies: dict, dimensions: list[dict]) -> dict:
    dims = []
    for d in dimensions:
        if d.get("is_time"):
            continue
        dims.append({"id": d["id"], "label": d.get("display_name", d["id"]), "views": sorted(d.get("views", {})),
                     "family": d.get("family")})
    return {
        "hierarchies": {k: {"levels": h["levels"], "views": h.get("views", [])} for k, h in hierarchies.items()},
        "conformed_dimensions": sorted([d for d in dims if len(d["views"]) >= 3], key=lambda d: -len(d["views"])),
        "dimension_count": len(dims),
    }


# ---------------------------------------------------------------------------- outputs
def to_json(g: Graph, where: dict) -> dict:
    same = _components(g, {"twin", "co_measure"})
    groups: dict[int, list[str]] = defaultdict(list)
    for n, k in same.items():
        groups[k].append(n)
    for n, k in same.items():
        g.nodes[n]["same_quantity_group"] = sorted(groups[k]) if len(groups[k]) > 1 else []
    counts: dict[str, int] = defaultdict(int)
    for e in g.edges:
        counts[e["kind"]] += 1
    return {
        "version": 1,
        "generated_by": "scripts/build_dependency_graph.py",
        "edge_kinds": {
            "composition": "to = Σ sign·from (catalogue formula, verified daily on Cube)",
            "ratio_component": "to = numerator ÷ denominator (catalogue ratio_components)",
            "declared_input": "to is computed from `from`, role not stated (catalogue depends_on only)",
            "twin": "same quantity on another date basis or grain (concept axes); never both in one causal model",
            "co_measure": "same events counted by another system; a divergence is a data observation, not a cause",
            "subset": "from ⊂ to (part of the same events)",
            "allocation": "to is a model share of `from`, keyed on revenue; never a driver of the same entity's revenue",
            "mechanism": "business prior: a move in `from` drives `to` (direction certain, size for the data to show)",
            "feedback": "lagged reaction of a lever to results (budget / bidding)",
        },
        "tiers": where.pop("tiers", {}),
        "counts": dict(sorted(counts.items())),
        "nodes": list(g.nodes.values()),
        "edges": g.edges,
        "where": where,
        "validation": {"errors": g.errors, "warnings": g.warnings},
    }


def _mm_id(x: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", x)


def _mermaid(edges: list[dict], nodes: dict[str, dict], style: str = "LR") -> str:
    lines = [f"flowchart {style}"]
    used = sorted({e["from"] for e in edges} | {e["to"] for e in edges})
    for n in used:
        lines.append(f'    {_mm_id(n)}["{nodes[n]["label"]}"]')
    for e in edges:
        if e["kind"] == "composition":
            lab = "+" if e["sign"] > 0 else "−"
            lines.append(f'    {_mm_id(e["from"])} -- "{lab}" --> {_mm_id(e["to"])}')
        elif e["kind"] in CAUSAL:
            bits = [e.get("sign", "")]
            if e.get("rate"):
                bits.append(nodes[e["rate"]]["label"])
            lag = e.get("lag_days") or [0, 0]
            if lag != [0, 0]:
                bits.append(f"{lag[0]}–{lag[1]}d")
            arrow = "-.->" if e["kind"] == "feedback" or e.get("confidence") == "moderate" else "-->"
            lines.append(f'    {_mm_id(e["from"])} {arrow}|"{" · ".join(b for b in bits if b)}"| {_mm_id(e["to"])}')
        else:
            lines.append(f'    {_mm_id(e["from"])} --> {_mm_id(e["to"])}')
    return "\n".join(lines)


def _composition_tree(g: Graph, root: str) -> list[dict]:
    comp = [e for e in g.edges if e["kind"] == "composition"]
    out, todo, seen = [], [root], set()
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        for e in comp:
            if e["to"] == n:
                out.append(e)
                todo.append(e["from"])
    return out


def to_markdown(g: Graph, data: dict) -> str:
    N = g.nodes
    edges = g.edges
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        by_kind[e["kind"]].append(e)
    mech = by_kind["mechanism"] + by_kind["feedback"]
    connected = {e["from"] for e in edges} | {e["to"] for e in edges}
    isolated = sorted(n for n in N if n not in connected)
    md: list[str] = []
    w = md.append
    w("# Seleric metric dependency graph\n")
    w("GENERATED by `scripts/build_dependency_graph.py` from `catalogue/` and `catalogue_src/causal_priors.yaml`. "
      "Edit those, not this file. Machine-readable copy: `dependency_graph.json`.\n")
    w(f"**{len(N)} metrics, {len(edges)} edges.** " + ", ".join(f"{k}: {v}" for k, v in data['counts'].items()) + ".\n")
    w("## How far each edge can be trusted\n")
    w("| Layer | Edge kinds | Source | Trust |\n|---|---|---|---|")
    w("| Accounting | composition, ratio_component | catalogue formulas | **Exact.** Checked on real rows by `verify_compositions.py` / `verify_ratio_components.py`; use without question |")
    w("| Declared input | declared_input | catalogue `depends_on` | Exact that the input is used; its role (numerator? factor?) is not stated |")
    w("| Same quantity | twin, co_measure, subset, allocation | concepts axes, reviewed priors | **Exact for twins** (same quantity, other date basis / grain); co-measures are reviewed and should be checked numerically. These are never causes of each other |")
    w("| Business mechanism | mechanism, feedback | reviewed priors | **Direction** is business knowledge (`structural` > `strong` > `moderate`); whether and how much it moved in a given case is for the data (DoWhy, discovery) |\n")
    w("Rates sit **on edges**: CTR is the impressions → clicks mechanism, conversion rate is sessions → orders. A rate that "
      "changed means the mechanism changed (creative, site, price), not that a separate node moved.\n")

    w("## 1. Business mechanism spine (what drives what)\n")
    w("Solid arrows: structural or strong. Dotted: moderate or lagged feedback. Labels: sign · rate on the edge · lag.\n")
    w("```mermaid\n" + _mermaid(mech, N, "TB") + "\n```\n")
    w("| From | To | Sign | Rate on the edge | Lag (days) | Confidence | Why |\n|---|---|---|---|---|---|---|")
    for e in mech:
        lag = e.get("lag_days") or [0, 0]
        rate = N[e["rate"]]["label"] if e.get("rate") else ""
        kind = " (feedback)" if e["kind"] == "feedback" else ""
        w(f"| {N[e['from']]['label']} | {N[e['to']]['label']} | {e.get('sign','')} | {rate} | {lag[0]}–{lag[1]} | "
          f"{e['confidence']}{kind} | {e.get('note','')} |")
    w("")

    w("## 2. Accounting trees (exact)\n")
    for root, title in [("net_profit", "Order-date P&L: net profit"), ("pnl_net_profit", "Finance P&L (event date): net profit"),
                        ("gross_profit", "Order-date gross profit"), ("product_net_profit", "Product P&L (order date): net profit"),
                        ("net_sales", "Commerce net sales"), ("total_sales", "Total sales incl. tax"), ("units_sold", "Net units")]:
        tree = _composition_tree(g, root)
        if tree:
            w(f"### {title}\n")
            w("```mermaid\n" + _mermaid(tree, N, "RL") + "\n```\n")

    w("## 3. Ratios and what they are built from\n")
    w("| Ratio | Numerator | Denominator | View |\n|---|---|---|---|")
    for n in sorted(N):
        r = N[n]["ratio"]
        if r:
            w(f"| {N[n]['label']} (`{n}`) | `{r['numerator']}` | `{r['denominator']}` | {N[n]['view']} |")
    partial = sorted({e["to"] for e in by_kind["declared_input"]})
    w(f"\n**{len(partial)} ratios / metrics state an input but not the full formula** (the other side is not a catalogue "
      "metric, e.g. purchasing sessions for conversion rate). The engine can use them as rates on an edge but cannot "
      "decompose them exactly: " + ", ".join(f"`{p}`" for p in partial) + ".\n")

    w("## 4. Same quantity: never explain one with the other\n")
    w("Each group below is one quantity seen on different date bases, grains or systems. Inside a group a divergence "
      "is a **data** observation (late sync, tracking loss, cohort immaturity), never a cause.\n")
    groups = sorted({tuple(N[n]["same_quantity_group"]) for n in N if N[n]["same_quantity_group"]}, key=lambda t: t[0])
    w("| Group | Members |\n|---|---|")
    for i, grp in enumerate(groups, 1):
        w(f"| {i} | " + ", ".join(f"`{x}`" for x in grp) + " |")
    w("\n### Co-measures (two systems, same events)\n")
    w("| A | B | Systems | Expected |\n|---|---|---|---|")
    for e in by_kind["co_measure"]:
        w(f"| `{e['from']}` | `{e['to']}` | {' / '.join(e.get('systems', []))} | {e.get('expected','')} |")
    w("\n### Allocations (model output, circular as drivers)\n")
    w("| Allocated | From | Split by |\n|---|---|---|")
    for e in by_kind["allocation"]:
        w(f"| `{e['to']}` | `{e['from']}` | {e.get('key','')} |")
    w("\n### Subsets\n")
    w("| Part | Whole |\n|---|---|")
    for e in by_kind["subset"]:
        w(f"| `{e['from']}` | `{e['to']}` |")
    w("")

    w("## 5. Funnel tiers (the order discovery must respect)\n")
    w("A lag-0 causal edge may only point from a lower tier to a higher one; lagged feedback may point back.\n")
    tiers: dict[int, list[str]] = defaultdict(list)
    for n, node in N.items():
        if node["aggregation"] == "additive" and node["tier"] is not None:
            tiers[node["tier"]].append(n)
    w("| Tier | Additive metrics |\n|---|---|")
    for t in sorted(tiers):
        w(f"| {t} | " + ", ".join(f"`{x}`" for x in sorted(tiers[t])) + " |")
    w("")

    w("## 6. Where-graph: dimensions shared across views\n")
    w("Dimensions shared by many views are where a change can be followed from one domain to another (a campaign's "
      "spend, its sessions and its orders).\n")
    w("| Hierarchy | Levels (coarse → fine) | Views |\n|---|---|---|")
    for k, h in data["where"]["hierarchies"].items():
        w(f"| {k} | {' → '.join(h['levels'])} | {len(h['views'])} |")
    w("\n| Conformed dimension | Views |\n|---|---|")
    for d in data["where"]["conformed_dimensions"]:
        w(f"| `{d['id']}` | {', '.join(d['views'])} |")
    w("")

    w("## 7. Gaps and checks\n")
    broken = sorted(n for n in N if N[n]["status"] == "broken")
    w(f"- Broken in the catalogue (do not use as evidence): {', '.join(f'`{b}`' for b in broken) or 'none'}.")
    w(f"- Metrics with no edge at all ({len(isolated)}): {', '.join(f'`{x}`' for x in isolated) or 'none'}. They can "
      "still be scanned for anomalies but the engine has no prior on how they relate.")
    w(f"- Validation: {len(g.errors)} errors, {len(g.warnings)} warnings.")
    for x in g.errors + g.warnings:
        w(f"  - {x}")
    return "\n".join(md) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate only")
    args = ap.parse_args()
    g, where = build()
    data = to_json(g, where)
    for x in g.warnings:
        print(f"WARN {x}")
    for x in g.errors:
        print(f"FAIL {x}")
    print(f"{len(g.nodes)} metrics, {len(g.edges)} edges: {data['counts']}")
    if not args.check:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "dependency_graph.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        (OUT / "DEPENDENCY_GRAPH.md").write_text(to_markdown(g, data))
        page = (ROOT / "scripts" / "dependency_graph_explorer.html.tmpl").read_text()
        slim = {**data, "nodes": [{k: v for k, v in n.items() if k != "ratio"} for n in data["nodes"]]}
        (OUT / "explorer.html").write_text(page.replace("/*DATA*/null", json.dumps(slim, ensure_ascii=False)))
        print(f"wrote {OUT.relative_to(ROOT)}/dependency_graph.json, DEPENDENCY_GRAPH.md and explorer.html")
    return 1 if g.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
