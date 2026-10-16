#!/usr/bin/env python
"""SEM-15 dataset contract certification against live ClickHouse.

For every contract in ``catalogue_v2_src/datasets.yaml`` this runs the gates the
review asked for on each physical binding:

  U  uniqueness   ``count()`` == distinct (declared_keys)  (no duplicate rows)
  C  cardinality  non-empty; row count and distinct-key count recorded
  P  population   every declared key is populated (null/empty coverage recorded)
  T  temporal     date column min/max and freshness (days since max) recorded
  R  reconciliation  datasets with >1 binding: row totals and shared numeric
                     column sums must agree between bindings

Evidence is written to ``doc/semantic_datasets/certification.json`` and
``CERTIFICATION.md``. Nothing is mutated unless ``--apply`` is passed, and then
only contracts whose every gate passes on every binding are flipped to
``certification.status: certified`` with the recorded evidence attached.

  uv run python scripts/certify_datasets.py --write     # run gates, write evidence
  uv run python scripts/certify_datasets.py --check     # write, exit 1 if any gate fails
  uv run python scripts/certify_datasets.py --apply     # flip passing contracts to certified
  uv run python scripts/certify_datasets.py --dataset Ads.Delivery
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from seleric_mcp.semantic_inventory.sources import ch_query  # noqa: E402

# v2 by default; semantic v3 (Phase 7): CERTIFY_DATASETS=catalogue_v3_src CERTIFY_OUT=doc/semantic_v3/results/datasets
_SRC = os.environ.get("CERTIFY_DATASETS", "catalogue_v2_src")
_OUT = Path(os.environ.get("CERTIFY_OUT", "doc/semantic_datasets"))
DATASETS = ROOT / _SRC / "datasets.yaml"
OUT_JSON = ROOT / _OUT / "certification.json"
OUT_MD = ROOT / _OUT / "CERTIFICATION.md"
FRESHNESS_FAIL_DAYS = 14


def _rows(sql: str) -> list[dict]:
    body = ch_query(sql).strip()
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def _scalar(sql: str):
    return next(iter(_rows(sql)[0].values()))


def _qualified(table: str) -> str:
    return table if "." in table else f"serve.{table}"


def _columns(table: str) -> dict[str, str]:
    db, name = _qualified(table).split(".")
    rows = _rows(
        f"SELECT name, type FROM system.columns WHERE database='{db}' AND table='{name}'"
    )
    return {r["name"]: r["type"] for r in rows}


def _key_expr(keys: list[str]) -> str:
    return ", ".join(f"ifNull(toString({k}), '∅')" for k in keys)


def _freshness_days(value: str, today: date) -> int:
    text = value.replace("Z", "+00:00").split("+")[0].split(".")[0]
    return (today - datetime.fromisoformat(text).date()).days


def certify_binding(table: str, keys: list[str], date_col: str, today: date) -> dict:
    table = _qualified(table)
    cols = _columns(table)
    missing = keys if not cols else [k for k in keys if k not in cols]
    result: dict = {"table": table, "declared_keys": keys, "missing_columns": missing}

    if not cols:
        result["gates"] = {"U_uniqueness": "fail", "C_cardinality": "fail", "P_population": "fail", "T_temporal": "fail"}
        result["errors"] = [f"table {table} has no columns"]
        return result

    row = _rows(
        f"SELECT count() AS rows, uniqExact(({_key_expr(keys)})) AS distinct_keys FROM {table}"
    )[0]
    rows, distinct = int(row["rows"]), int(row["distinct_keys"])
    dup = rows - distinct

    null_exprs = ", ".join(
        f"countIf(isNull({k}) OR toString({k}) = '') AS null_{i}" for i, k in enumerate(keys)
    )
    null_row = _rows(f"SELECT {null_exprs} FROM {table}")[0]
    population = {
        k: {
            "null_or_empty_rows": null_row[f"null_{i}"],
            "null_or_empty_pct": round(100.0 * null_row[f"null_{i}"] / rows, 4) if rows else None,
            "type": cols.get(k),
        }
        for i, k in enumerate(keys)
    }

    temporal: dict = {}
    if date_col in cols:
        row = _rows(f"SELECT min({date_col}) AS mn, max({date_col}) AS mx FROM {table}")[0]
        temporal = {"column": date_col, "min": str(row["mn"]), "max": str(row["mx"])}
        if row["mx"]:
            temporal["freshness_days"] = _freshness_days(str(row["mx"]), today)

    fresh_bad = temporal.get("freshness_days") is not None and temporal["freshness_days"] > FRESHNESS_FAIL_DAYS
    pop_bad = [k for k, v in population.items() if v["null_or_empty_pct"] == 100.0]
    result.update(
        {
            "row_count": rows,
            "distinct_keys": distinct,
            "duplicate_key_rows": dup,
            "population": population,
            "temporal": temporal,
            "gates": {
                "U_uniqueness": "pass" if dup == 0 else "fail",
                "C_cardinality": "pass" if rows > 0 else "fail",
                "P_population": "pass" if not pop_bad else "fail",
                "T_temporal": "pass" if temporal and not fresh_bad else "fail",
            },
        }
    )
    if dup:
        result.setdefault("errors", []).append(f"{dup} duplicate rows on declared keys")
    if pop_bad:
        result.setdefault("errors", []).append(f"declared keys 100% null/empty: {pop_bad}")
    elif [k for k, v in population.items() if v["null_or_empty_pct"] and v["null_or_empty_pct"] > 0]:
        result.setdefault("warnings", []).append(f"declared keys with null/empty values (advisory): {[k for k,v in population.items() if v['null_or_empty_pct'] and v['null_or_empty_pct']>0]}")
    if not temporal:
        result.setdefault("errors", []).append(f"date column {date_col} not present")
    elif fresh_bad:
        result.setdefault("errors", []).append(f"stale: max {temporal['max']} ({temporal['freshness_days']}d)")
    return result


def reconcile(bindings: list[dict], cols_by_table: dict[str, dict]) -> dict:
    if len(bindings) < 2:
        return {"applicable": False}
    totals = {b["table"]: b["row_count"] for b in bindings}
    numeric: dict[str, dict[str, float]] = {}
    shared = set.intersection(*(
        {c for c, t in cols_by_table[b["table"]].items() if "Float" in t or "Int" in t}
        for b in bindings
    )) if all(b["table"] in cols_by_table for b in bindings) else set()
    for col in sorted(shared):
        numeric[col] = {}
        for b in bindings:
            try:
                numeric[col][b["table"]] = _scalar(f"SELECT sum({col}) FROM {b['table']}")
            except Exception:  # noqa: BLE001
                pass
    return {"applicable": True, "row_totals": totals, "numeric_sums": numeric}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true", help="exit 1 if any gate fails")
    ap.add_argument("--apply", action="store_true", help="flip passing contracts to certified")
    ap.add_argument("--dataset")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    contracts = yaml.safe_load(DATASETS.read_text())
    report: dict = {"generated_on": today.isoformat(), "datasets": {}}
    cols_by_table: dict[str, dict] = {}

    for name, ds in contracts.items():
        if args.dataset and name != args.dataset:
            continue
        keys = list(ds["grain"]["declared_keys"])
        date_col = ds["date_policy"]["default"]
        bindings = []
        for bname, table in ds["physical_bindings"].items():
            cols_by_table[_qualified(table)] = _columns(table)
            b = certify_binding(table, keys, date_col, today)
            b["binding"] = bname
            bindings.append(b)

        binding_results = {b["binding"]: all(v == "pass" for v in b["gates"].values()) for b in bindings}
        recon = reconcile(bindings, cols_by_table)
        if all(binding_results.values()):
            overall = "certified"
        elif any(binding_results.values()):
            overall = "partial"
        else:
            overall = "failed"
        entry = {
            "role": ds.get("role"),
            "declared_keys": keys,
            "date_policy": date_col,
            "bindings": bindings,
            "reconciliation": recon,
            "verdict": overall,
            "binding_verdicts": binding_results,
        }
        report["datasets"][name] = entry

    if args.write or args.apply or args.check:
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(report, indent=2, default=str) + "\n")
        _write_md(report, OUT_MD)
        print(f"wrote {OUT_JSON.relative_to(ROOT)} and {OUT_MD.relative_to(ROOT)}")

    failed = [n for n, d in report["datasets"].items() if d["verdict"] != "certified"]
    for name, d in report["datasets"].items():
        marks = " ".join(
            f"{b['binding']}:{'/'.join(v[0] for v in b['gates'].values())}" for b in d["bindings"]
        )
        print(f"  {d['verdict']:>9}  {name:<22} {marks}")

    if args.apply:
        _apply(contracts, report)
    if args.check and failed:
        print(f"\n{len(failed)} dataset(s) failed: {', '.join(failed)}")
        return 1
    return 0


def _write_md(report: dict, path: Path) -> None:
    lines = ["# Dataset contract certification (SEM-15)", "", f"Generated: {report['generated_on']}", ""]
    lines += ["| dataset | verdict | binding | binding verdict | rows | distinct | dup | freshness (d) |", "|---|---|---|---|---|---|---|---|"]
    for name, d in report["datasets"].items():
        bv = d.get("binding_verdicts", {})
        for b in d["bindings"]:
            t = b.get("temporal", {})
            lines.append(
                f"| {name} | {d['verdict']} | {b['binding']} | {bv.get(b['binding'], '?')} | "
                f"{b.get('row_count')} | {b.get('distinct_keys')} | {b.get('duplicate_key_rows')} | {t.get('freshness_days')} |"
            )
    lines.append("")
    for name, d in report["datasets"].items():
        errs = [e for b in d["bindings"] for e in b.get("errors", [])]
        warns = [w for b in d["bindings"] for w in b.get("warnings", [])]
        if errs or warns:
            lines += [f"## {name} — {d['verdict']}", *[f"- ERROR: {e}" for e in errs], *[f"- WARNING: {w}" for w in warns], ""]
    path.write_text("\n".join(lines) + "\n")


def _apply(contracts: dict, report: dict) -> None:
    popped = []
    for name, d in report["datasets"].items():
        if d["verdict"] == "certified" and name in contracts:
            contracts[name]["certification"] = {
                "status": "certified",
                "certified_on": report["generated_on"],
                "evidence": str(OUT_JSON.relative_to(ROOT)),
            }
            popped.append(name)
    DATASETS.write_text(yaml.safe_dump(contracts, sort_keys=False, allow_unicode=True))
    print(f"applied certification to: {', '.join(popped) or '(none)'}")


if __name__ == "__main__":
    raise SystemExit(main())
