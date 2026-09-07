#!/usr/bin/env python3
"""Generate the agent-side crosswalk from the systems that actually own each fact.

The three repos each own something, and nothing else may restate it:

  ClickHouse            table existence, columns, and the REAL gold -> serve
                        lineage (read out of the serve view DDL, never declared)
  Cube model / /meta    Cube view names, their members, and which serve table
                        each view executes against
  mage-ai/openmetadata  domains, data products, ownership, certification,
                        contracts (grain, time dimension), freshness SLAs

Everything the agent needs that is DERIVED from those — domain -> cube_views,
product -> cube_views, view -> serve table -> gold inputs -> contract -> date
axis -> freshness — is generated here into a single file. Hand-maintained
catalogue YAML keeps only what no system can derive: business prose, entity
clusters, the attribution boundary, and metric semantics.

  py scripts/sync_catalogue_from_sources.py            # regenerate
  py scripts/sync_catalogue_from_sources.py --check    # CI: exit 1 if stale
  py scripts/sync_catalogue_from_sources.py --diff     # show what would change
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
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
CUBE_DIR = Path(os.environ.get("SELERIC_CUBE_DIR", "/opt/seleric/mage-ai/infra/cube"))
OM_DIR = Path(os.environ.get("SELERIC_OM_DIR", "/opt/seleric/mage-ai/openmetadata"))
CUBE_API = os.environ.get("SELERIC_CUBE_API", "http://127.0.0.1:4001")
OUT = CORE / "catalogue" / "openmetadata" / "crosswalk.generated.yaml"

CH_DB_GOLD, CH_DB_SERVE = "gold", "serve"
CH_PREFIX = "clickhouse.default"


# ----------------------------------------------------------------- ClickHouse
def _ch_conn() -> tuple[str, str, str]:
    env: dict[str, str] = {}
    f = CUBE_DIR / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    env.update({k: v for k, v in os.environ.items() if k.startswith("CUBEJS_DB_")})
    return (
        f"http://{env.get('CUBEJS_DB_HOST', '127.0.0.1')}:{env.get('CUBEJS_DB_PORT', '8123')}/",
        env.get("CUBEJS_DB_USER", "default"),
        env.get("CUBEJS_DB_PASS", ""),
    )


def ch(sql: str) -> list[list[str]]:
    base, user, pw = _ch_conn()
    url = base + "?" + urllib.parse.urlencode({"user": user, "password": pw})
    req = urllib.request.Request(url, data=sql.encode(), method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - fixed internal host
        return [ln.split("\t") for ln in r.read().decode().splitlines() if ln]


def warehouse() -> dict:
    """Real lineage, read from the serve view SQL rather than declared anywhere."""
    gold = {r[0] for r in ch(f"SELECT name FROM system.tables WHERE database='{CH_DB_GOLD}' FORMAT TSV")}
    serve = [r[0] for r in ch(f"SELECT name FROM system.tables WHERE database='{CH_DB_SERVE}' FORMAT TSV")]
    ddl = {
        r[0]: r[1]
        for r in ch(
            "SELECT name, replaceAll(create_table_query, '\\n', ' ') FROM system.tables "
            f"WHERE database='{CH_DB_SERVE}' FORMAT TSV"
        )
    }
    direct = {n: set(re.findall(r"gold\.`?([A-Za-z0-9_]+)`?", s)) & gold for n, s in ddl.items()}
    deps = {
        n: (set(re.findall(r"serve\.`?([A-Za-z0-9_]+)`?", s)) & set(serve)) - {n}
        for n, s in ddl.items()
    }

    def closure(name: str) -> set[str]:
        out: set[str] = set()
        seen: set[str] = set()
        stack = [name]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            out |= direct.get(cur, set())
            stack.extend(deps.get(cur, ()))
        return out

    return {"gold": gold, "serve": set(serve), "gold_inputs": {n: closure(n) for n in serve}}


# ----------------------------------------------------------------------- Cube
def cube_surface() -> dict:
    """Cube view -> members and the serve tables its cubes execute against.

    /meta is authoritative for members; the YAML supplies sql_table, which /meta
    does not expose.
    """
    with urllib.request.urlopen(f"{CUBE_API}/cubejs-api/v1/meta", timeout=30) as r:  # noqa: S310
        meta = json.loads(r.read().decode())

    cube_tables: dict[str, list[str]] = {}
    for f in sorted((CUBE_DIR / "model" / "cubes").glob("*.yml")):
        for c in (yaml.safe_load(f.read_text()) or {}).get("cubes", []) or []:
            if c.get("sql_table"):
                # structured and unambiguous - always prefer it
                tables = [c["sql_table"].split(".", 1)[-1]]
            else:
                # inline-SQL cube: scan the sql block only, never comments, which
                # routinely name a sibling table and would win a naive file-wide scan
                tables = sorted(set(re.findall(r"serve\.`?([a-z0-9_]+)`?", str(c.get("sql") or ""))))
            cube_tables[c["name"]] = tables

    view_cubes: dict[str, list[str]] = {}
    vf = CUBE_DIR / "model" / "views" / "serve_views.yml"
    for v in (yaml.safe_load(vf.read_text()) or {}).get("views", []) or []:
        seen: list[str] = []
        for entry in v.get("cubes") or []:
            root = (entry.get("join_path") or "").split(".")[0]
            if root and root not in seen:
                seen.append(root)
        view_cubes[v["name"]] = seen

    views: dict[str, dict] = {}
    for c in meta.get("cubes", []):
        if c["name"] not in view_cubes:
            continue  # a cube, not a certified view
        views[c["name"]] = {
            "title": c.get("title") or c.get("name"),
            "description": (c.get("description") or "").strip(),
            "measures": sorted(m["name"].split(".")[-1] for m in c.get("measures") or []),
            "dimensions": sorted(d["name"].split(".")[-1] for d in c.get("dimensions") or []),
            "time_dimensions": sorted(
                d["name"].split(".")[-1] for d in c.get("dimensions") or [] if d.get("type") == "time"
            ),
            "_cubes": view_cubes[c["name"]],
            "serve_tables": sorted({t for cu in view_cubes[c["name"]] for t in cube_tables.get(cu, [])}),
        }
    # keep views declared in YAML but absent from /meta visible rather than silently dropped
    for name, cubes in view_cubes.items():
        views.setdefault(
            name,
            {
                "title": name,
                "description": "",
                "measures": [],
                "dimensions": [],
                "time_dimensions": [],
                "_cubes": cubes,
                "serve_tables": sorted({t for cu in cubes for t in cube_tables.get(cu, [])}),
                "_not_in_cube_meta": True,
            },
        )
    return views


# --------------------------------------------------------------- OpenMetadata
def _split_ports(ports: list | None) -> tuple[list[str], list[str]]:
    tables, cube_views = [], []
    for p in ports or []:
        if isinstance(p, str):
            tables.append(p)
        elif isinstance(p, dict) and "cube_view" in p:
            cube_views.append(p["cube_view"])
    return tables, cube_views


def openmetadata() -> dict:
    domains = {}
    for f in sorted((OM_DIR / "domains").glob("*.yml")):
        d = yaml.safe_load(f.read_text()) or {}
        d = d.get("domain", d)
        domains[d["name"]] = {
            "owner_team": (d.get("owners") or [None])[0],
            "description": " ".join((d.get("description") or "").split()),
        }

    products = {}
    for f in sorted((OM_DIR / "products").glob("*.yml")):
        d = yaml.safe_load(f.read_text()) or {}
        out_tables, out_views = _split_ports(d.get("output_ports"))
        in_tables, _ = _split_ports(d.get("input_ports"))
        tags = d.get("tags") or []
        products[d["name"]] = {
            "domain": d.get("domain"),
            "owner_team": (d.get("owners") or [None])[0],
            "certification": next(
                (t.split(".", 1)[1] for t in tags if t.startswith(("Certification.", "ReleaseStatus."))),
                None,
            ),
            "agent_ready": "DataProduct.AgentReady" in tags,
            "serve_tables": [t.split(".", 1)[-1] for t in out_tables if t.startswith("serve.")],
            "declared_gold_inputs": [t.split(".", 1)[-1] for t in in_tables if t.startswith("gold.")],
            "cube_views": out_views,
            "product_file": f"products/{f.name}",
        }

    contracts = {}
    for f in sorted((OM_DIR / "contracts").glob("*.yml")):
        d = yaml.safe_load(f.read_text()) or {}
        tbl = (d.get("table") or "").split(".", 1)[-1]
        if not tbl:
            continue
        sem = d.get("semantics") or {}
        contracts[tbl] = {
            "name": d.get("name"),
            "file": f"contracts/{f.name}",
            "time_dimension": ((d.get("grain") or {}).get("time_dimension") or "").split("#")[0].strip()
            or None,
            # What a consumer filters on. Deliberately distinct from grain: a
            # customer-grain port has no time in its key but still serves an axis.
            "serving_date_axis": sem.get("serving_date_axis"),
            "serving_datetime_axis": sem.get("serving_datetime_axis"),
            "declares_serving_axis": "serving_date_axis" in sem,
            "grain_key": (d.get("grain") or {}).get("key") or [],
            "materialization": d.get("materialization"),
            "tier": sem.get("tier"),
            "domain": sem.get("domain"),
            "data_product": (sem.get("data_product") or "").split(".")[-1] or None,
            "required_columns": (d.get("schema") or {}).get("required_columns") or [],
            "quality_tests": (d.get("quality") or {}).get("tests") or [],
            "attribution_boundary": " ".join((sem.get("attribution_boundary") or "").split()) or None,
        }

    slo_file = OM_DIR / "slos" / "freshness.yml"
    slos: dict[str, dict] = {}
    if slo_file.exists():
        doc = yaml.safe_load(slo_file.read_text()) or {}
        defaults = doc.get("defaults") or {}
        for _key, spec in (doc.get("products") or {}).items():
            tbl = (spec.get("table") or "").split(".", 1)[-1]
            if not tbl:
                continue
            slos[tbl] = {
                "max_age_hours": spec.get("max_age_hours", defaults.get("max_age_hours")),
                "source_column": spec.get("source_column", defaults.get("source_column")),
                "timezone": spec.get("timezone", defaults.get("timezone")),
                "description": " ".join((spec.get("description") or "").split()) or None,
            }
    return {"domains": domains, "products": products, "contracts": contracts, "slos": slos}


# ------------------------------------------------------------------- assemble
def build() -> dict:
    wh = warehouse()
    cubes = cube_surface()
    om = openmetadata()

    product_of_view: dict[str, str] = {}
    for pname, p in om["products"].items():
        for v in p["cube_views"]:
            product_of_view.setdefault(v, pname)

    views: dict[str, dict] = {}
    for vname, v in sorted(cubes.items()):
        prod_for_tables = product_of_view.get(vname)
        declared = om["products"].get(prod_for_tables, {}).get("serve_tables") or []
        referenced = [t for t in v["serve_tables"] if t in wh["serve"]]
        # A view built by inline SQL touches every table it reads. The OUTPUT PORT
        # is whichever of those the owning data product declares — governance
        # decides the port, not the order ClickHouse happens to return.
        serve_tables = [t for t in referenced if t in declared] or referenced
        # Port resolution, most trustworthy signal first. An inline-SQL cube touches
        # every table it reads, so picking the first alphabetically is wrong: it gave
        # daily_pnl the amazon_attribution_overview port instead of canonical_pnl.
        cube_named = [t for cu in v["_cubes"] for t in (cu.removeprefix("serve_"),) if t in serve_tables]
        primary = next(
            (t for t in serve_tables if t == vname),          # view named after its port
            next((t for t in declared if t in serve_tables),  # the product's declared port
                 next(iter(cube_named),                       # the cube's own name
                      serve_tables[0] if serve_tables else None)),
        )
        # a view over several serve tables inherits the union of their real inputs
        gold_inputs = sorted({g for t in serve_tables for g in wh["gold_inputs"].get(t, set())})
        contract = om["contracts"].get(primary or "")
        slo = om["slos"].get(primary or "")
        prod = product_of_view.get(vname)
        # Resolution order, most specific owner first:
        #   1. the contract's explicit serving axis (settles genuine ambiguity)
        #   2. grain.time_dimension, when the grain axis IS the serving axis
        #   3. the view's sole time dimension, when there is no ambiguity to settle
        # Anything left unresolved is reported, never guessed.
        datetime_dim = contract["serving_datetime_axis"] if contract else None
        if contract and contract["declares_serving_axis"]:
            date_dim = contract["serving_date_axis"]
        elif contract and contract["time_dimension"] in v["time_dimensions"]:
            date_dim = contract["time_dimension"]
        elif len(v["time_dimensions"]) == 1:
            date_dim = v["time_dimensions"][0]
        else:
            date_dim = None

        views[vname] = {
            "data_product": prod,
            "domain": om["products"].get(prod, {}).get("domain") if prod else None,
            "owner_team": om["products"].get(prod, {}).get("owner_team") if prod else None,
            "title": v["title"],
            "serve_table": f"{CH_PREFIX}.{CH_DB_SERVE}.{primary}" if primary else None,
            "serve_tables": [f"{CH_PREFIX}.{CH_DB_SERVE}.{t}" for t in serve_tables],
            "gold_inputs": [f"{CH_PREFIX}.{CH_DB_GOLD}.{g}" for g in gold_inputs],
            "contract": contract["name"] if contract else None,
            "date_dimension": date_dim,
            "datetime_dimension": datetime_dim,
            "date_axis_resolved": bool(date_dim) or bool(contract and contract["declares_serving_axis"]),
            "time_dimensions": v["time_dimensions"],
            "grain_key": contract["grain_key"] if contract else [],
            "freshness": slo or None,
            "measures": v["measures"],
            "dimensions": v["dimensions"],
        }
        if v.get("_not_in_cube_meta"):
            views[vname]["_not_in_cube_meta"] = True

    domains: dict[str, dict] = {}
    for dname, d in sorted(om["domains"].items()):
        prods = sorted(p for p, spec in om["products"].items() if spec["domain"] == dname)
        dviews: list[str] = []
        for p in prods:
            for v in om["products"][p]["cube_views"]:
                if v not in dviews:
                    dviews.append(v)
        domains[dname] = {
            "owner_team": d["owner_team"],
            "description": d["description"],
            "data_products": prods,
            "cube_views": sorted(dviews),
        }

    data_products = []
    for pname, p in sorted(om["products"].items()):
        serve = [t for t in p["serve_tables"] if t in wh["serve"]]
        actual_gold = sorted({g for t in serve for g in wh["gold_inputs"].get(t, set())})
        data_products.append(
            {
                "name": pname,
                "domain": p["domain"],
                "owner_team": p["owner_team"],
                "certification": p["certification"],
                "agent_ready": p["agent_ready"],
                "cube_views": sorted(p["cube_views"]),
                "serve_tables": [f"{CH_PREFIX}.{CH_DB_SERVE}.{t}" for t in sorted(serve)],
                "primary_serve_table": (
                    f"{CH_PREFIX}.{CH_DB_SERVE}.{p['serve_tables'][0]}" if p["serve_tables"] else None
                ),
                "gold_inputs": [f"{CH_PREFIX}.{CH_DB_GOLD}.{g}" for g in actual_gold],
                "contracts": sorted(
                    {om["contracts"][t]["name"] for t in serve if t in om["contracts"]}
                ),
                "product_file": p["product_file"],
            }
        )

    # Contract summaries, so the agent side stops restating mage-ai/openmetadata/contracts/.
    contracts = {}
    for tbl, c in sorted(om["contracts"].items(), key=lambda kv: kv[1]["name"] or kv[0]):
        if not c["name"] or tbl not in wh["serve"]:
            continue
        contracts[c["name"]] = {
            "serve_table": f"{CH_PREFIX}.{CH_DB_SERVE}.{tbl}",
            "data_product": c["data_product"],
            "domain": c["domain"],
            "grain": c["grain_key"],
            "time_dimension": c["serving_date_axis"] if c["declares_serving_axis"] else c["time_dimension"],
            "currency": "INR",
            "required_columns": c["required_columns"],
            "quality_tests": c["quality_tests"],
            "attribution_boundary": c["attribution_boundary"],
            "contract_file": c["file"],
        }

    body = {"domains": domains, "data_products": data_products, "views": views, "contracts": contracts}
    fingerprint = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    return {
        "_generated_by": "scripts/sync_catalogue_from_sources.py",
        "_do_not_hand_edit": True,
        "_sources": [
            f"ClickHouse {CH_DB_GOLD}/{CH_DB_SERVE} (table existence + real gold->serve lineage)",
            f"{CUBE_API}/cubejs-api/v1/meta + {CUBE_DIR}/model (view members + serve tables)",
            f"{OM_DIR}/domains|products|contracts|slos (governance, grain, freshness)",
        ],
        "_fingerprint": fingerprint,
        **body,
    }


def render(doc: dict) -> str:
    header = (
        "# GENERATED FILE - do not hand-edit.\n"
        "# Regenerate:  py scripts/sync_catalogue_from_sources.py\n"
        "# Verify:      py scripts/sync_catalogue_from_sources.py --check\n"
        "#\n"
        "# Every field here is DERIVED. Edit the owning system instead:\n"
        "#   a Cube view, its members or its serve table -> mage-ai/infra/cube/model/\n"
        "#   a domain, product, contract, grain or SLA    -> mage-ai/openmetadata/\n"
        "#   gold -> serve lineage                        -> the serve view SQL in ClickHouse\n"
        "# Business prose, entity clusters, the attribution boundary and metric\n"
        "# semantics are NOT derived and stay hand-authored in catalogue/.\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if the file on disk is stale")
    ap.add_argument("--diff", action="store_true", help="print the diff instead of writing")
    args = ap.parse_args()

    doc = build()
    new = render(doc)
    old = OUT.read_text() if OUT.exists() else ""

    if args.check or args.diff:
        if old == new:
            print(f"crosswalk up to date ({doc['_fingerprint']}, {len(doc['views'])} views)")
            return 0
        print(f"crosswalk STALE — {OUT.relative_to(CORE)} does not match its sources")
        if args.diff or args.check:
            d = list(
                difflib.unified_diff(
                    old.splitlines(), new.splitlines(), "on-disk", "generated", lineterm="", n=1
                )
            )
            print("\n".join(d[:120]))
            if len(d) > 120:
                print(f"... {len(d) - 120} more diff lines")
        print("\nRegenerate: py scripts/sync_catalogue_from_sources.py")
        return 1

    # Deliberately no generated-at timestamp: the file must be byte-identical for
    # identical sources, or --check can never distinguish real drift from a re-run.
    # _fingerprint identifies the content; git records when it changed.
    OUT.write_text(new)
    print(f"wrote {OUT.relative_to(CORE)}")
    print(
        f"  {len(doc['domains'])} domains, {len(doc['data_products'])} data products, "
        f"{len(doc['views'])} cube views, {len(doc['contracts'])} contracts, "
        f"fingerprint {doc['_fingerprint']}"
    )
    unowned = [v for v, s in doc["views"].items() if not s["data_product"]]
    nolineage = [v for v, s in doc["views"].items() if not s["gold_inputs"]]
    noaxis = [v for v, s in doc["views"].items() if not s["date_axis_resolved"]]
    nocontract = [v for v, s in doc["views"].items() if not s["contract"]]
    if noaxis:
        print(f"  WARNING {len(noaxis)} views have an unresolved date axis: {', '.join(noaxis)}")
        print("          declare semantics.serving_date_axis in the port's contract")
    if nocontract:
        print(f"  WARNING {len(nocontract)} views have no data contract: {', '.join(nocontract)}")
    if unowned:
        print(f"  WARNING {len(unowned)} views have no owning data product: {', '.join(unowned)}")
    if nolineage:
        print(f"  WARNING {len(nolineage)} views resolve to no gold input: {', '.join(nolineage)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
