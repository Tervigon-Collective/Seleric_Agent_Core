#!/usr/bin/env python3
"""Cross-layer reconciliation: gold -> serve -> Cube -> OpenMetadata -> agent catalogue.

Answers three questions in one pass:
  1. COVERAGE   which gold tables never reach the serve database, and which serve
                relations never reach Cube.
  2. RECONCILE  where OpenMetadata domains / data products / ontology disagree with
                the Cube views and the agent catalogue.
  3. SEMANTICS  which exposed objects lack the descriptions an agent needs to
                navigate them.

Read-only. Exit 1 if any BLOCKER-severity finding is present (CI gate).

  py scripts/reconcile_layers.py                # human report
  py scripts/reconcile_layers.py --json         # machine report
  py scripts/reconcile_layers.py --section coverage
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import yaml

CORE = Path(__file__).resolve().parent.parent
CUBE = Path(os.environ.get("SELERIC_CUBE_DIR", "/opt/seleric/mage-ai/infra/cube"))
OM = Path(os.environ.get("SELERIC_OM_DIR", "/opt/seleric/mage-ai/openmetadata"))

# gold objects that are infrastructure or intermediate by design and are not
# expected to have a serve port. Everything else must be exposed or waived.
GOLD_EXEMPT_PREFIXES = ("int_", "raw_", "_ch_")
GOLD_EXEMPT = {"_ch_sync_snapshot_state"}

# The gold sync does full-refresh as "write to temp -> atomic RENAME -> drop old",
# so a run landing mid-sync sees short-lived scratch tables. Observed live:
# gold.fct_meta_ads_daily_sync_tmp_12511 (<table>_sync_tmp_<pid>). Ignore them, or
# the report is non-deterministic and flaps in CI.
SCRATCH_RE = re.compile(
    r"(^\.inner)|(^__)|(_sync_tmp_\d+$)|(_tmp_\d+$)|(_tmp$)|(^tmp_)"
    r"|(_new$)|(_old$)|(_swap$)|(_bak$)|(_backup$)"
)

# Deliberate, reviewed exceptions. Key is "CODE:subject"; value is the reason.
# A waived finding is reported as WAIVED and never gates CI — so every accepted
# gap stays visible and attributable instead of silently disappearing.
WAIVERS_FILE = CORE / "catalogue" / "reconciliation_waivers.yaml"


def load_waivers() -> dict[str, str]:
    if not WAIVERS_FILE.exists():
        return {}
    doc = yaml.safe_load(WAIVERS_FILE.read_text()) or {}
    return {k: str(v) for k, v in (doc.get("waivers") or {}).items()}


# ---------------------------------------------------------------- ClickHouse
def _ch_env() -> tuple[str, str, str]:
    env: dict[str, str] = {}
    envfile = CUBE / ".env"
    if envfile.exists():
        for line in envfile.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    env.update({k: v for k, v in os.environ.items() if k.startswith("CUBEJS_DB_")})
    host = env.get("CUBEJS_DB_HOST", "127.0.0.1")
    port = env.get("CUBEJS_DB_PORT", "8123")
    return (
        f"http://{host}:{port}/",
        env.get("CUBEJS_DB_USER", "default"),
        env.get("CUBEJS_DB_PASS", ""),
    )


def ch(sql: str) -> list[list[str]]:
    base, user, pw = _ch_env()
    url = base + "?" + urllib.parse.urlencode({"user": user, "password": pw})
    req = urllib.request.Request(url, data=sql.encode(), method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - fixed internal host
        body = r.read().decode()
    return [ln.split("\t") for ln in body.splitlines() if ln]


# ------------------------------------------------------------------- loaders
def load_warehouse() -> dict:
    gold = [
        r[0]
        for r in ch("SELECT name FROM system.tables WHERE database='gold' ORDER BY name FORMAT TSV")
        if not SCRATCH_RE.search(r[0])
    ]
    serve = [
        r[0]
        for r in ch("SELECT name FROM system.tables WHERE database='serve' ORDER BY name FORMAT TSV")
        if not SCRATCH_RE.search(r[0])
    ]
    ddl = {
        r[0]: r[1]
        for r in ch(
            "SELECT name, replaceAll(create_table_query, '\\n', ' ') FROM system.tables "
            "WHERE database='serve' FORMAT TSV"
        )
    }
    cols: dict[str, list[str]] = defaultdict(list)
    for db, tbl, col in ch(
        "SELECT database, table, name FROM system.columns "
        "WHERE database IN ('gold','serve') ORDER BY database, table, position FORMAT TSV"
    ):
        cols[f"{db}.{tbl}"].append(col)

    # direct gold references per serve relation, then transitive through serve->serve
    direct_gold: dict[str, set[str]] = {}
    serve_deps: dict[str, set[str]] = {}
    for name, sql in ddl.items():
        direct_gold[name] = set(re.findall(r"gold\.`?([A-Za-z0-9_]+)`?", sql)) & set(gold)
        serve_deps[name] = (set(re.findall(r"serve\.`?([A-Za-z0-9_]+)`?", sql)) & set(serve)) - {name}

    def closure(name: str) -> set[str]:
        """Gold tables reachable from a serve relation through serve->serve deps.

        Iterative with an explicit stack: sharing one `seen` set across sibling
        branches would prune reachable subtrees, and set iteration order varies
        per process, so a recursive version returns different answers run to run.
        """
        out: set[str] = set()
        seen: set[str] = set()
        stack = [name]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            out |= direct_gold.get(cur, set())
            stack.extend(serve_deps.get(cur, ()))
        return out

    return {
        "gold": gold,
        "serve": serve,
        "columns": dict(cols),
        "gold_direct": direct_gold,
        "gold_closure": {n: closure(n) for n in serve},
        "serve_deps": serve_deps,
    }


def load_cube() -> dict:
    cubes: dict[str, dict] = {}
    for f in sorted((CUBE / "model" / "cubes").glob("*.yml")):
        doc = yaml.safe_load(f.read_text()) or {}
        for c in doc.get("cubes", []) or []:
            raw = f.read_text()
            tables = set(re.findall(r"serve\.`?([a-z0-9_]+)`?", raw))
            c["_serve_tables"] = tables
            c["_file"] = str(f.relative_to(CUBE))
            cubes[c["name"]] = c
    views: dict[str, dict] = {}
    vf = CUBE / "model" / "views" / "serve_views.yml"
    doc = yaml.safe_load(vf.read_text()) or {}
    for v in doc.get("views", []) or []:
        v["_cubes"] = {
            (c.get("join_path") or "").split(".")[0]
            for c in (v.get("cubes") or [])
            if c.get("join_path")
        }
        views[v["name"]] = v
    return {"cubes": cubes, "views": views, "live_members": cube_live_members()}


def load_om() -> dict:
    reg = yaml.safe_load((OM / "product_registry.yml").read_text())
    products = {p["name"]: p for p in reg.get("products", [])}
    contracts = {f.name: yaml.safe_load(f.read_text()) for f in sorted((OM / "contracts").glob("*.yml"))}
    domains = {}
    for f in sorted((OM / "domains").glob("*.yml")):
        d = yaml.safe_load(f.read_text()) or {}
        payload = d.get("domain", d)
        domains[payload.get("name", f.stem)] = payload
    return {
        "registry": reg,
        "products": products,
        "contracts": contracts,
        "domains": domains,
        "declared_domains": set(reg.get("domains", [])),
    }


def check_crosswalk(r: Report, cat: dict) -> None:
    """The generated crosswalk must be current, and no hand file may contradict it."""
    import subprocess

    gen = CORE / "scripts" / "sync_catalogue_from_sources.py"
    cw_path = CORE / "catalogue" / "openmetadata" / "crosswalk.generated.yaml"
    if not cw_path.exists():
        r.add("reconciliation", "BLOCKER", "CROSSWALK_MISSING", "crosswalk.generated.yaml",
              "the derived crosswalk has never been generated, so the agent is running entirely "
              "on hand-maintained restatements of Cube / OpenMetadata / ClickHouse",
              f"py {gen.relative_to(CORE)}")
        return
    proc = subprocess.run(  # noqa: S603 - fixed local script
        [sys.executable, str(gen), "--check"], capture_output=True, text=True, cwd=str(CORE)
    )
    if proc.returncode != 0:
        r.add("reconciliation", "BLOCKER", "CROSSWALK_STALE", "crosswalk.generated.yaml",
              "the crosswalk no longer matches its sources — a Cube view, contract, product or "
              "serve-view SQL changed and the agent is serving the previous shape",
              f"py {gen.relative_to(CORE)}")

    cw = yaml.safe_load(cw_path.read_text()) or {}
    cw_domains = cw.get("domains") or {}
    cw_views = cw.get("views") or {}

    # Hand files may still carry entries the generator cannot derive (a Cube view
    # no data product claims). Those are the migration backlog - report them so the
    # list shrinks, rather than letting them look authoritative forever.
    for dname, dspec in (cat["ontology"].get("domains") or {}).items():
        derived = set((cw_domains.get(dname) or {}).get("cube_views") or [])
        for v in dspec.get("cube_views") or []:
            if v not in derived:
                r.add("reconciliation", "WARN", "VIEW_HAND_MAINTAINED", f"{dname} -> {v}",
                      "this view is mapped to its domain only by hand, because no OpenMetadata "
                      "data product claims it — the one mapping the generator cannot derive",
                      "give the view an owning data product, then delete the hand entry")

    # A hand-authored value that disagrees with the derived one is now inert at
    # runtime (generated wins) but still misleads a reader.
    for v in cat["views"].values():
        gen_v = cw_views.get(v["name"]) or {}
        for field in ("date_dimension", "datetime_dimension"):
            hand, derived = v.get(field), gen_v.get(field)
            if derived and hand and hand != derived:
                r.add("reconciliation", "WARN", "DERIVED_FIELD_CONTRADICTED", f"{v['name']}.{field}",
                      f"catalogue/views.yaml says '{hand}'; the contract/Cube model derive "
                      f"'{derived}'. The derived value wins at runtime",
                      "delete the hand-authored value, or fix the contract if it is wrong")


def load_catalogue() -> dict:
    ont = yaml.safe_load((CORE / "catalogue/openmetadata/ontology.yaml").read_text())
    core_reg = yaml.safe_load((CORE / "catalogue/openmetadata/registry.yaml").read_text())
    views = yaml.safe_load((CORE / "catalogue/views.yaml").read_text()) or {}
    modules = yaml.safe_load((CORE / "catalogue/modules.yaml").read_text()) or {}
    dims = yaml.safe_load((CORE / "catalogue/dimensions/core.yaml").read_text()) or {}
    metrics = {}
    for f in sorted((CORE / "catalogue/metrics").glob("*.yaml")):
        m = yaml.safe_load(f.read_text()) or {}
        m["_file"] = f.name
        metrics[m.get("id", f.stem)] = m
    # Mirror loader._apply_crosswalk: generated domain -> cube_views wins, hand
    # entries the generator cannot derive are preserved. Validating the raw file
    # instead would report drift the runtime does not actually have.
    cw_path = CORE / "catalogue" / "openmetadata" / "crosswalk.generated.yaml"
    cw_domains = (yaml.safe_load(cw_path.read_text()) or {}).get("domains", {}) if cw_path.exists() else {}
    effective: dict[str, dict] = {}
    for dname, dspec in (ont.get("domains") or {}).items():
        merged = dict(dspec)
        gen = cw_domains.get(dname) or {}
        for key in ("data_products", "cube_views"):
            derived = list(gen.get(key) or [])
            merged[key] = derived + [x for x in (dspec.get(key) or []) if x not in derived]
        effective[dname] = merged
    for dname, gen in cw_domains.items():
        effective.setdefault(dname, dict(gen))

    return {
        "ontology": ont,
        "effective_domains": effective,
        "registry": core_reg,
        "views": {v["name"]: v for v in (views.get("views") or [])},
        "modules": (modules.get("modules") or {}),
        "dimensions": {d["id"]: d for d in (dims.get("dimensions") or dims.get("core") or [])}
        if isinstance(dims, dict)
        else {},
        "metrics": metrics,
    }



def _dim_key_names(dim_table: str) -> set[str]:
    """Plausible join keys for a gold dimension, derived from its own name.
    Keeps the denormalisation check on real fact->dimension edges instead of any
    two tables that happen to share a column called *_id."""
    parts = dim_table.removeprefix("dim_").split("_")
    roots = {"_".join(parts)}
    roots |= set(parts)
    if len(parts) >= 2:
        roots.add("_".join(parts[-2:]))
    singular = {rt[:-1] if rt.endswith("s") and not rt.endswith("ss") else rt for rt in roots}
    return {f"{rt}_{suffix}" for rt in roots | singular for suffix in ("id", "key")}


def _member_names(entry: dict) -> set[str]:
    """Names a view exposes for one `cubes:` entry. `includes: "*"` means the
    cube's whole surface, which we cannot enumerate from the view alone."""
    inc = entry.get("includes")
    if inc in ("*", None):
        return set()
    out: set[str] = set()
    for item in inc:
        if isinstance(item, dict):
            out.add(item.get("alias") or item.get("name"))
        else:
            out.add(item)
    for item in entry.get("excludes") or []:
        out.discard(item)
    return {m for m in out if m}


def cube_live_members() -> dict[str, set[str]] | None:
    """view name -> members Cube actually exposes, from its /meta endpoint.

    Authoritative: a view that includes a whole cube with `includes: "*"` cannot
    be resolved from the YAML alone, and that is exactly where a catalogue metric
    can point at a member Cube does not have. Returns None if Cube is unreachable,
    in which case we fall back to the YAML and skip wildcard views.
    """
    url = os.environ.get("SELERIC_CUBE_API", "http://127.0.0.1:4001") + "/cubejs-api/v1/meta"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:  # noqa: S310 - fixed internal host
            meta = json.loads(r.read().decode())
    except Exception:
        return None
    out: dict[str, set[str]] = {}
    for c in meta.get("cubes", []):
        names = {
            m["name"].split(".")[-1]
            for m in (c.get("measures") or []) + (c.get("dimensions") or []) + (c.get("segments") or [])
        }
        out[c["name"]] = names
    return out


def view_members(cb: dict, live: dict[str, set[str]] | None = None) -> dict[str, set[str] | None]:
    """view name -> exposed member names, or None when the surface is unknown."""
    out: dict[str, set[str] | None] = {}
    for vname, v in cb["views"].items():
        if live is not None and vname in live:
            out[vname] = live[vname]
            continue
        members: set[str] = set()
        wildcard = False
        for entry in v.get("cubes") or []:
            if entry.get("includes") in ("*", None):
                wildcard = True
            members |= _member_names(entry)
        out[vname] = None if wildcard else members
    return out


# ------------------------------------------------------------------ findings
class Report:
    def __init__(self, waivers: dict[str, str] | None = None) -> None:
        self.findings: list[dict] = []
        self.waivers = waivers or {}

    def add(self, section: str, severity: str, code: str, subject: str, detail: str, fix: str = "") -> None:
        reason = self.waivers.get(f"{code}:{subject}")
        self.findings.append(
            {
                "section": section,
                "severity": "WAIVED" if reason else severity,
                "code": code,
                "subject": subject,
                "detail": detail,
                "fix": fix,
                "waiver": reason,
            }
        )


# ----------------------------------------------------------- 1. gold -> serve
def check_coverage(r: Report, wh: dict, cb: dict, cat: dict) -> None:
    exposed = set().union(*wh["gold_closure"].values()) if wh["gold_closure"] else set()
    for g in wh["gold"]:
        if g in GOLD_EXEMPT or g.startswith(GOLD_EXEMPT_PREFIXES):
            continue
        if g in exposed:
            continue
        kind = "fact" if g.startswith("fct_") else "dimension" if g.startswith("dim_") else "mart"
        sev = "BLOCKER" if g.startswith(("fct_", "mart_")) else "WARN"
        r.add(
            "coverage",
            sev,
            "GOLD_NOT_SERVED",
            f"gold.{g}",
            f"{kind} with {len(wh['columns'].get('gold.' + g, []))} columns has no serve port "
            f"(directly or transitively) — unreachable from Cube and the agent",
            "add a serve relation, or add to GOLD_EXEMPT with a waiver comment",
        )

    cube_tables = set().union(*(c["_serve_tables"] for c in cb["cubes"].values())) if cb["cubes"] else set()
    for s in wh["serve"]:
        if s not in cube_tables:
            r.add(
                "coverage",
                "BLOCKER",
                "SERVE_NOT_MODELLED",
                f"serve.{s}",
                "serve relation has no Cube cube — built but not queryable through the semantic layer",
                "add model/cubes/serve_<name>.yml or drop the relation",
            )

    view_cubes = set().union(*(v["_cubes"] for v in cb["views"].values())) if cb["views"] else set()
    for name, c in cb["cubes"].items():
        if name not in view_cubes:
            r.add(
                "coverage",
                "WARN",
                "CUBE_NOT_IN_VIEW",
                name,
                "cube is not included in any certified Cube view — invisible to the agent surface",
                f"add a view entry in model/views/serve_views.yml ({c['_file']})",
            )

    # denormalisation: a serve relation built on facts that key into a gold
    # dimension should carry that dimension's descriptive attributes, so the
    # agent can filter and group without a join it cannot express.
    GENERIC_KEYS = {"brand_id", "company_id", "shop_domain", "dt"}
    dim_cols = {g: set(wh["columns"].get(f"gold.{g}", [])) for g in wh["gold"] if g.startswith("dim_")}
    for s_name in wh["serve"]:
        scols = set(wh["columns"].get(f"serve.{s_name}", []))
        closure = wh["gold_closure"].get(s_name, set())
        fact_cols: set[str] = set()
        for g in closure:
            if g.startswith("fct_") or g.startswith("mart_"):
                fact_cols |= set(wh["columns"].get(f"gold.{g}", []))
        if not fact_cols:
            continue
        for dname, dcols in dim_cols.items():
            if dname in closure:
                continue  # already joined in
            if ("amazon" in dname) != ("amazon" in s_name):
                continue  # platform-scoped dimension against another platform's relation
            # the key must survive into the serve relation: that is what makes the
            # missing attributes a denormalisation gap rather than a grain mismatch.
            keys = {
                c
                for c in dcols & fact_cols & scols
                if c in _dim_key_names(dname) and c not in GENERIC_KEYS
            }
            if not keys:
                continue
            descriptive = {
                c
                for c in dcols - scols
                if not c.startswith("_")
                and not c.endswith(("_id", "_key", "_at", "_json", "_hash", "_ids"))
                and c not in GENERIC_KEYS
                and c != "platform"
            }
            if len(descriptive) >= 4:
                r.add(
                    "coverage",
                    "WARN",
                    "NOT_DENORMALISED",
                    f"serve.{s_name} -> gold.{dname}",
                    f"keys on {sorted(keys)} but carries none of the dimension's "
                    f"{len(descriptive)} descriptive attributes: {sorted(descriptive)[:8]}",
                    f"denormalise gold.{dname} into serve.{s_name}, or waive if out of grain",
                )


# -------------------------------------------------- 2. OpenMetadata reconcile
def check_reconciliation(r: Report, wh: dict, cb: dict, om: dict, cat: dict) -> None:
    ont_domains = cat["effective_domains"]
    cube_views = set(cb["views"])
    serve_set = set(wh["serve"])

    # 2a. domain sets agree
    for d in om["declared_domains"] - set(ont_domains):
        r.add("reconciliation", "BLOCKER", "DOMAIN_MISSING_IN_ONTOLOGY", d,
              "OpenMetadata declares this domain; catalogue/openmetadata/ontology.yaml does not",
              "add the domain to ontology.yaml with its cube_views and grain")
    for d in set(ont_domains) - om["declared_domains"]:
        r.add("reconciliation", "BLOCKER", "DOMAIN_NOT_IN_OM", d,
              "ontology.yaml declares this domain; the OpenMetadata registry does not",
              f"add {OM}/domains/*.yml + regenerate product_registry.yml")

    # 2b. data products agree between OM registry and the agent-side registry
    core_products = {p["name"]: p for p in cat["registry"].get("data_products", [])}
    for p in set(core_products) - set(om["products"]):
        r.add("reconciliation", "BLOCKER", "PRODUCT_NOT_IN_OM", p,
              "agent registry exposes this data product but OpenMetadata does not define it — "
              "ungoverned surface (no owner, no contract, no DQ)",
              f"add {OM}/products/<name>.yml and regenerate product_registry.yml")
    for p in set(om["products"]) - set(core_products):
        r.add("reconciliation", "WARN", "PRODUCT_NOT_EXPOSED", p,
              "OpenMetadata governs this data product but the agent registry does not expose it",
              "add to catalogue/openmetadata/registry.yaml data_products")

    for name, p in om["products"].items():
        # 2c. output ports exist in the warehouse
        for port in p.get("output_ports", []) or []:
            tbl = port.split(".", 1)[-1]
            if not port.startswith("serve.") or tbl not in serve_set:
                r.add("reconciliation", "BLOCKER", "PORT_MISSING", f"{name} -> {port}",
                      "declared output port does not exist in the serve database",
                      "create the relation or correct the product spec")
        for port in p.get("input_ports", []) or []:
            tbl = port.split(".", 1)[-1]
            if port.startswith("gold.") and tbl not in set(wh["gold"]):
                r.add("reconciliation", "BLOCKER", "PORT_MISSING", f"{name} -> {port}",
                      "declared input port does not exist in the gold database", "correct the product spec")
        # 2d. cube_views exist
        for v in p.get("cube_views", []) or []:
            if v not in cube_views:
                r.add("reconciliation", "BLOCKER", "CUBE_VIEW_MISSING", f"{name} -> {v}",
                      "data product claims a Cube view that serve_views.yml does not define",
                      "add the view or correct the product spec")
        # 2e. every output port has a contract
        if not (p.get("contract_files") or []):
            r.add("reconciliation", "BLOCKER", "NO_CONTRACT", name,
                  "data product has no data contract — schema/quality is unenforced",
                  f"add {OM}/contracts/<port>.yml")

    # 2f. ontology cube_views resolve, and every certified Cube view has a domain
    owned: dict[str, list[str]] = defaultdict(list)
    for dom, spec in ont_domains.items():
        for v in spec.get("cube_views", []) or []:
            owned[v].append(dom)
            if v not in cube_views:
                r.add("reconciliation", "BLOCKER", "ONTOLOGY_VIEW_MISSING", f"{dom} -> {v}",
                      "ontology maps a domain to a Cube view that does not exist",
                      "correct ontology.yaml or add the view")
    for v in cube_views:
        if v not in owned:
            r.add("reconciliation", "BLOCKER", "VIEW_UNOWNED", v,
                  "Cube view belongs to no ontology domain — the agent cannot scope it to a module "
                  "and it inherits no owner",
                  "add the view to a domain's cube_views in ontology.yaml")
        elif len(owned[v]) > 1:
            r.add("reconciliation", "WARN", "VIEW_MULTI_OWNED", v,
                  f"claimed by several domains: {owned[v]}", "assign a single owning domain")

    # 2g. every certified Cube view is claimed by an OpenMetadata data product
    claimed: dict[str, list[str]] = defaultdict(list)
    for name, prod in om["products"].items():
        for v in prod.get("cube_views", []) or []:
            claimed[v].append(name)
    for v in cube_views:
        if v not in claimed:
            r.add("reconciliation", "BLOCKER", "VIEW_NOT_IN_ANY_PRODUCT", v,
                  "Cube view is served to clients but no OpenMetadata data product claims it — "
                  "no certification, no contract, no declared owner behind the numbers",
                  "add it to an existing products/*.yml cube_views, or define a new data product")
        elif len(claimed[v]) > 1:
            r.add("reconciliation", "WARN", "VIEW_MULTI_PRODUCT", v,
                  f"claimed by several data products: {sorted(claimed[v])}",
                  "give the view one owning product")

    # 2h. OM product cube_views vs ontology domain cube_views
    for name, p in om["products"].items():
        dom = p.get("domain")
        dom_views = set((ont_domains.get(dom) or {}).get("cube_views", []) or [])
        for v in p.get("cube_views", []) or []:
            if v in cube_views and dom in ont_domains and v not in dom_views:
                r.add("reconciliation", "WARN", "VIEW_DOMAIN_DRIFT", f"{v}",
                      f"OM product {name} places it in domain {dom}, but ontology.yaml does not list "
                      f"it under {dom}",
                      f"add {v} to ontology.yaml domains.{dom}.cube_views")

    # 2i. the agent-side registry declares a gold -> serve lineage per view.
    # Verify it against what the warehouse actually does, so the lineage a client
    # is shown is the lineage that produced the number.
    for vname, link in (cat["registry"].get("views") or {}).items():
        if vname not in cube_views:
            r.add("reconciliation", "BLOCKER", "REGISTRY_VIEW_MISSING", vname,
                  "registry.yaml links a Cube view that serve_views.yml does not define",
                  "remove the link or add the view")
            continue
        serve_tbl = (link.get("serve_table") or "").split(".")[-1]
        if serve_tbl and serve_tbl not in serve_set:
            r.add("reconciliation", "BLOCKER", "REGISTRY_SERVE_TABLE_MISSING", vname,
                  f"registry.yaml points at serve.{serve_tbl}, which does not exist",
                  "correct serve_table in catalogue/openmetadata/registry.yaml")
            continue
        actual = wh["gold_closure"].get(serve_tbl, set())
        declared = {g.split(".")[-1] for g in (link.get("gold_inputs") or []) if ".gold." in g}
        for g in declared - actual:
            r.add("reconciliation", "WARN", "LINEAGE_OVERSTATED", f"{vname} -> gold.{g}",
                  f"registry.yaml claims serve.{serve_tbl} is built from gold.{g}, but its SQL "
                  f"does not reference it",
                  "correct gold_inputs in catalogue/openmetadata/registry.yaml")
        for g in actual - declared:
            r.add("reconciliation", "WARN", "LINEAGE_UNDECLARED", f"{vname} -> gold.{g}",
                  f"serve.{serve_tbl} reads gold.{g} but registry.yaml does not declare it — "
                  f"the lineage shown to a client is incomplete",
                  "add it to gold_inputs in catalogue/openmetadata/registry.yaml")

    # 2j. modules resolve to real domains
    for mod, spec in cat["modules"].items():
        for d in spec.get("domains", []) or []:
            if d not in ont_domains:
                r.add("reconciliation", "BLOCKER", "MODULE_BAD_DOMAIN", f"{mod} -> {d}",
                      "module references a domain that ontology.yaml does not define", "fix modules.yaml")

    # 2k. catalogue metrics land on real Cube views/members
    vmembers = view_members(cb, cb.get("live_members"))
    for mid, m in cat["metrics"].items():
        if m.get("status") == "broken":
            continue  # already parked and reported as broken; not a second finding
        cm = m.get("cube_mapping") or {}
        v = cm.get("view")
        if not v:
            r.add("semantics", "BLOCKER", "METRIC_NO_MAPPING", mid,
                  "catalogue metric has no cube_mapping — unanswerable", f"map it in {m['_file']}")
            continue
        if v not in cube_views:
            r.add("reconciliation", "BLOCKER", "METRIC_BAD_VIEW", mid,
                  f"cube_mapping.view '{v}' is not a Cube view", f"fix {m['_file']}")
            continue
        # Check the primary member AND the ratio components: a ratio
        # metric whose own measure exists can still name a numerator Cube does not
        # have, which is how six broken metrics reached production unnoticed.
        refs = {"measure": cm.get("measure") or cm.get("dimension")}
        rc = m.get("ratio_components") or {}
        refs["ratio numerator"] = rc.get("numerator")
        refs["ratio denominator"] = rc.get("denominator")
        # companion_measures holds catalogue metric ids, not Cube members — the
        # loader already validates those, so they are deliberately not checked here.
        known = vmembers.get(v)
        if not known:
            continue
        for role, ref in refs.items():
            if not ref:
                continue
            member = ref.split(".")[-1]
            if member not in known:
                r.add("reconciliation", "BLOCKER", "METRIC_MEMBER_MISSING", f"{mid} ({role})",
                      f"'{ref}' is not exposed by Cube view {v}",
                      f"add it to the view's includes, or fix {m['_file']}")

    # 2l. every certified view is registered agent-side with a date axis + freshness
    for v in cube_views:
        cv = cat["views"].get(v)
        if not cv:
            r.add("reconciliation", "BLOCKER", "VIEW_NOT_IN_CATALOGUE", v,
                  "Cube view is not declared in catalogue/views.yaml — no date axis, no freshness gate",
                  "add it to catalogue/views.yaml")
            continue
        # An explicit `date_dimension: null` is a deliberate declaration that the
        # view has no time member (master data). A missing key is an omission.
        if "date_dimension" not in cv:
            r.add("semantics", "BLOCKER", "VIEW_NO_DATE_AXIS", v,
                  "no date_dimension declared — time filters cannot be applied safely",
                  "set date_dimension in catalogue/views.yaml (explicit null for master data "
                  "with no time member)")
        if not cv.get("freshness"):
            r.add("semantics", "WARN", "VIEW_NO_FRESHNESS", v,
                  "no freshness block — the staleness gate cannot fail closed for this view",
                  "add freshness.source + expected_cadence")


# ------------------------------------------------------- 3. semantic quality
MIN_VIEW_DESC = 80
MIN_METRIC_DESC = 60


def check_semantics(r: Report, wh: dict, cb: dict, om: dict, cat: dict) -> None:
    for name, v in cb["views"].items():
        desc = (v.get("description") or "").strip()
        if not desc:
            r.add("semantics", "BLOCKER", "VIEW_NO_DESCRIPTION", name,
                  "Cube view has no description — the agent has nothing to route on",
                  "add description + title in serve_views.yml")
        elif len(desc) < MIN_VIEW_DESC:
            r.add("semantics", "WARN", "VIEW_THIN_DESCRIPTION", name,
                  f"description is {len(desc)} chars; state grain, date axis, scope and boundaries",
                  "expand the description in serve_views.yml")
        if not (v.get("title") or "").strip():
            r.add("semantics", "WARN", "VIEW_NO_TITLE", name, "no human-readable title",
                  "add title in serve_views.yml")

    for cname, c in cb["cubes"].items():
        for kind in ("measures", "dimensions"):
            for m in c.get(kind) or []:
                if not (m.get("description") or "").strip():
                    r.add("semantics", "WARN", "MEMBER_NO_DESCRIPTION", f"{cname}.{m.get('name')}",
                          f"Cube {kind[:-1]} has no description",
                          f"describe it in {c['_file']}")

    for mid, m in cat["metrics"].items():
        desc = (m.get("description") or "").strip()
        if len(desc) < MIN_METRIC_DESC:
            r.add("semantics", "BLOCKER" if not desc else "WARN", "METRIC_THIN_DESCRIPTION", mid,
                  f"description is {len(desc)} chars — must state basis, scope, date axis and what it "
                  f"is NOT, so the agent does not substitute a sibling metric",
                  f"expand description in {m['_file']}")
        if not (m.get("formula") or {}).get("human_readable"):
            r.add("semantics", "WARN", "METRIC_NO_FORMULA", mid,
                  "no human-readable formula — the agent cannot explain the number",
                  f"add formula.human_readable in {m['_file']}")
        if not m.get("grain"):
            r.add("semantics", "WARN", "METRIC_NO_GRAIN", mid, "no grain declared",
                  f"add grain in {m['_file']}")
        if not m.get("examples"):
            r.add("semantics", "WARN", "METRIC_NO_EXAMPLE", mid,
                  "no worked example — few-shot routing quality degrades",
                  f"add examples in {m['_file']}")

    # metrics must be reachable from at least one module, or they are dead weight
    ont_domains = cat["effective_domains"]
    module_views: set[str] = set()
    for spec in cat["modules"].values():
        for d in spec.get("domains", []) or []:
            module_views |= set((ont_domains.get(d) or {}).get("cube_views", []) or [])
        module_views |= set(spec.get("extra_views", []) or [])
    for mid, m in cat["metrics"].items():
        v = (m.get("cube_mapping") or {}).get("view")
        if v and v in cb["views"] and v not in module_views:
            r.add("semantics", "WARN", "METRIC_UNREACHABLE_BY_MODULE", mid,
                  f"maps to view '{v}' which no module resolves to — unreachable in a module-scoped "
                  f"deployment",
                  "add the view to a domain in ontology.yaml or a module's extra_views")

    # dimensions declared in the agent catalogue must exist on the views they claim
    vmembers = view_members(cb, cb.get("live_members"))
    for did, d in (cat["dimensions"] or {}).items():
        if not (d.get("description") or "").strip():
            r.add("semantics", "WARN", "DIMENSION_NO_DESCRIPTION", did,
                  "shared dimension has no description", "describe it in catalogue/dimensions/core.yaml")
        for vname, member in (d.get("views") or {}).items():
            leaf = member.split(".")[-1]
            if vname not in cb["views"]:
                r.add("reconciliation", "BLOCKER", "DIMENSION_BAD_VIEW", f"{did} -> {vname}",
                      "dimension maps to a Cube view that does not exist",
                      "fix catalogue/dimensions/core.yaml")
            elif vmembers.get(vname) and leaf not in vmembers[vname]:
                r.add("reconciliation", "BLOCKER", "DIMENSION_MEMBER_MISSING", f"{did} -> {vname}.{leaf}",
                      "dimension is not included in that Cube view",
                      "add to the view includes or fix core.yaml")

    for fname, c in om["contracts"].items():
        if not (c.get("description") or c.get("contract", {}).get("description")):
            r.add("semantics", "WARN", "CONTRACT_NO_DESCRIPTION", fname,
                  "data contract carries no description", f"describe it in {OM}/contracts/{fname}")


# ---------------------------------------------------------------------- main
SECTIONS = ("coverage", "reconciliation", "semantics")
ORDER = {"BLOCKER": 0, "WARN": 1, "WAIVED": 2}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--section", choices=SECTIONS, action="append")
    ap.add_argument("--severity", choices=("BLOCKER", "WARN", "WAIVED"))
    ap.add_argument("--hide-waived", action="store_true", help="omit deliberately waived findings")
    args = ap.parse_args()

    wh = load_warehouse()
    cb = load_cube()
    om = load_om()
    cat = load_catalogue()

    r = Report(load_waivers())
    if cb.get("live_members") is None:
        r.add("reconciliation", "WARN", "CUBE_META_UNREACHABLE", "cube",
              "could not read Cube /meta; member checks fell back to the view YAML and skipped "
              "views that include a whole cube — a broken metric mapping can hide there",
              "start Cube, or set SELERIC_CUBE_API")
    check_coverage(r, wh, cb, cat)
    check_reconciliation(r, wh, cb, om, cat)
    check_semantics(r, wh, cb, om, cat)
    check_crosswalk(r, cat)

    findings = r.findings
    if args.section:
        findings = [f for f in findings if f["section"] in args.section]
    if args.severity:
        findings = [f for f in findings if f["severity"] == args.severity]
    if args.hide_waived:
        findings = [f for f in findings if f["severity"] != "WAIVED"]
    findings.sort(key=lambda f: (f["section"], ORDER[f["severity"]], f["code"], f["subject"]))

    blockers = sum(1 for f in findings if f["severity"] == "BLOCKER")
    waived = sum(1 for f in findings if f["severity"] == "WAIVED")

    if args.json:
        print(json.dumps({
            "inventory": {
                "gold_tables": len(wh["gold"]),
                "serve_relations": len(wh["serve"]),
                "cubes": len(cb["cubes"]),
                "cube_views": len(cb["views"]),
                "om_domains": len(om["declared_domains"]),
                "om_products": len(om["products"]),
                "om_contracts": len(om["contracts"]),
                "catalogue_metrics": len(cat["metrics"]),
                "modules": len(cat["modules"]),
            },
            "blockers": blockers,
            "warnings": sum(1 for f in findings if f["severity"] == "WARN"),
            "waived": waived,
            "findings": findings,
        }, indent=2))
        return 1 if blockers else 0

    print("SELERIC LAYER RECONCILIATION")
    print(f"  gold {len(wh['gold'])} tables -> serve {len(wh['serve'])} relations -> "
          f"{len(cb['cubes'])} cubes -> {len(cb['views'])} certified views")
    print(f"  OpenMetadata: {len(om['declared_domains'])} domains, {len(om['products'])} data products, "
          f"{len(om['contracts'])} contracts")
    print(f"  agent catalogue: {len(cat['metrics'])} metrics, {len(cat['modules'])} modules\n")

    by_section: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        by_section[f["section"]].append(f)
    for sec in SECTIONS:
        rows = by_section.get(sec)
        if not rows:
            continue
        nb = sum(1 for f in rows if f["severity"] == "BLOCKER")
        nwv = sum(1 for f in rows if f["severity"] == "WAIVED")
        print(f"== {sec.upper()}  ({nb} blockers, {len(rows) - nb - nwv} warnings, {nwv} waived)")
        code = None
        for f in rows:
            if f["code"] != code:
                code = f["code"]
                print(f"\n  [{f['severity']}] {code}")
            print(f"    - {f['subject']}: {f['detail']}")
            if f["waiver"]:
                print(f"      waived: {f['waiver']}")
            elif f["fix"]:
                print(f"      fix: {f['fix']}")
        print()

    print(f"TOTAL: {blockers} blockers, "
          f"{sum(1 for f in findings if f['severity'] == 'WARN')} warnings, {waived} waived")
    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
