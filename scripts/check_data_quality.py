#!/usr/bin/env python3
"""Data-level checks on the serve layer — the bugs structure alone cannot catch.

scripts/reconcile_layers.py proves the layers AGREE with each other. This proves
the data behind them is usable:

  DEAD_DIMENSION      a dimension the agent can group by that is empty, or holds a
                      single constant value. Grouping by it returns one bucket and
                      the agent presents that as a real breakdown.
  COVERAGE_REGRESSION a column that used to be populated and recently stopped —
                      a silent loader break. Nothing else notices these, because
                      totals stay correct while a dimension quietly goes blank.
  UNMAPPED_TENANT     a brand_id present in serve data but absent from
                      catalogue/brands.yaml, so the agent can neither name it nor
                      answer questions about it, while its numbers still land in
                      any all-brand aggregate.
  GRAIN_VIOLATION     a serve relation with more rows than distinct contract-grain
                      keys — duplicates reaching the semantic layer.

Slower than the structural gate (it scans data), so run it on its own cadence.

  py scripts/check_data_quality.py                # report; exit 1 on ERROR
  py scripts/check_data_quality.py --json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

CORE = Path(__file__).resolve().parent.parent
CUBE_DIR = Path(os.environ.get("SELERIC_CUBE_DIR", "/opt/seleric/mage-ai/infra/cube"))
CUBE_API = os.environ.get("SELERIC_CUBE_API", "http://127.0.0.1:4001")
WAIVERS = CORE / "catalogue" / "data_quality_waivers.yaml"

# Columns that are constant BY DESIGN — provenance and model stamps. A constant
# here is correct, not a defect, so they are never reported as dead dimensions.
PROVENANCE = {
    "model_version", "source_basis", "source_currency", "is_final", "currency_code",
    "accounting_basis", "data_as_of", "attribution_model", "credit_pct",
}


def ch(sql: str) -> str:
    env: dict[str, str] = {}
    f = CUBE_DIR / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    env.update({k: v for k, v in os.environ.items() if k.startswith("CUBEJS_DB_")})
    url = (
        f"http://{env.get('CUBEJS_DB_HOST','127.0.0.1')}:{env.get('CUBEJS_DB_PORT','8123')}/?"
        + urllib.parse.urlencode({"user": env.get("CUBEJS_DB_USER", "default"),
                                  "password": env.get("CUBEJS_DB_PASS", "")})
    )
    req = urllib.request.Request(url, data=sql.encode(), method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 - fixed internal host
        return r.read().decode()


def load_waivers() -> dict[str, str]:
    if not WAIVERS.exists():
        return {}
    return {k: str(v) for k, v in ((yaml.safe_load(WAIVERS.read_text()) or {}).get("waivers") or {}).items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-regression", action="store_true", help="skip the slower coverage scan")
    args = ap.parse_args()

    waivers = load_waivers()
    findings: list[dict] = []

    def add(sev: str, code: str, subject: str, detail: str, fix: str = "") -> None:
        reason = waivers.get(f"{code}:{subject}")
        findings.append({"severity": "WAIVED" if reason else sev, "code": code, "subject": subject,
                         "detail": detail, "fix": fix, "waiver": reason})

    cw = yaml.safe_load((CORE / "catalogue/openmetadata/crosswalk.generated.yaml").read_text())
    meta = json.loads(urllib.request.urlopen(f"{CUBE_API}/cubejs-api/v1/meta", timeout=60).read())  # noqa: S310

    cols: dict[str, set[str]] = collections.defaultdict(set)
    for row in ch("SELECT table, name FROM system.columns WHERE database='serve' FORMAT TSV").splitlines():
        if "\t" in row:
            t, n = row.split("\t")
            cols[t].add(n)

    # ---- DEAD_DIMENSION -----------------------------------------------------
    checked = 0
    for c in meta.get("cubes", []):
        view = c["name"]
        spec = (cw.get("views") or {}).get(view)
        if not spec or not spec.get("serve_table"):
            continue
        tbl = spec["serve_table"].split(".")[-1]
        dims = [d["name"].split(".")[-1] for d in c.get("dimensions") or []]
        dims = [d for d in dims if d in cols.get(tbl, ()) and d not in PROVENANCE]
        if not dims:
            continue
        sel = ", ".join(f"uniqExact({d}) AS `{d}`" for d in dims)
        try:
            out = ch(f"SELECT count() AS __rows, {sel} FROM serve.{tbl} FORMAT TSV").strip().split("\t")
        except Exception as e:  # noqa: BLE001 - one bad column must not kill the run
            add("WARN", "CHECK_FAILED", f"{view}", f"could not scan: {e}", "")
            continue
        rows = int(out[0])
        checked += len(dims)
        if rows == 0:
            continue
        for d, u in zip(dims, out[1:]):
            if int(u) <= 1:
                add("ERROR", "DEAD_DIMENSION", f"{view}.{d}",
                    f"{'empty' if int(u) == 0 else 'single constant value'} across all {rows:,} rows — "
                    f"grouping by it yields one bucket the agent will present as a real breakdown",
                    "populate it upstream, or remove it from the Cube view and the catalogue")

    # ---- UNMAPPED_TENANT ----------------------------------------------------
    brands = yaml.safe_load((CORE / "catalogue/brands.yaml").read_text()) or {}
    known = {str(b.get("id") or b.get("brand_id")) for b in (brands.get("brands") or [])}
    tenant_tables = [t for t in cols if "brand_id" in cols[t]]
    seen: dict[str, list[str]] = collections.defaultdict(list)
    if tenant_tables:
        union = "\nUNION ALL\n".join(
            f"SELECT '{t}' AS t, toString(brand_id) AS b FROM serve.{t} GROUP BY b" for t in tenant_tables
        )
        for row in ch(f"{union} FORMAT TSV").splitlines():
            if "\t" in row:
                t, b = row.split("\t")
                seen[b].append(t)
    for b, tables in sorted(seen.items()):
        if b not in known:
            add("ERROR", "UNMAPPED_TENANT", f"brand_id={b}",
                f"present in {len(tables)} serve relations ({', '.join(sorted(tables)[:4])}…) but absent "
                f"from catalogue/brands.yaml — unnameable by the agent, yet its rows land in any "
                f"all-brand aggregate",
                "add the brand to catalogue/brands.yaml, or exclude it from serve")

    # ---- GRAIN_VIOLATION ----------------------------------------------------
    for view, spec in (cw.get("views") or {}).items():
        key = spec.get("grain_key") or []
        tbl = (spec.get("serve_table") or "").split(".")[-1]
        if not key or not tbl or not set(key) <= cols.get(tbl, set()):
            continue
        k = ", ".join(key)
        out = ch(f"SELECT count(), uniqExact({k}) FROM serve.{tbl} FORMAT TSV").strip().split("\t")
        rows, keys = int(out[0]), int(out[1])
        if rows > keys:
            add("ERROR", "GRAIN_VIOLATION", f"serve.{tbl}",
                f"{rows:,} rows for {keys:,} distinct contract-grain keys ({k}) — "
                f"{rows - keys:,} duplicate rows reach the semantic layer and inflate every sum",
                "add FINAL / dedupe the source, or correct the contract grain")

    # ---- COVERAGE_REGRESSION ------------------------------------------------
    if not args.skip_regression:
        for view, spec in (cw.get("views") or {}).items():
            tbl = (spec.get("serve_table") or "").split(".")[-1]
            dd = spec.get("date_dimension")
            if not tbl or not dd or dd not in cols.get(tbl, ()):
                continue
            dims = [d for d in cols[tbl] if d not in PROVENANCE and d != dd][:40]
            if not dims:
                continue
            recent, prior = f"{dd}>=today()-30", f"{dd}<today()-30 AND {dd}>=today()-120"
            sel = ", ".join(
                f"round(100*countIf(toString({d}) NOT IN ('','NULL') AND {recent})"
                f"/nullIf(countIf({recent}),0),1) AS `r_{d}`, "
                f"round(100*countIf(toString({d}) NOT IN ('','NULL') AND {prior})"
                f"/nullIf(countIf({prior}),0),1) AS `p_{d}`"
                for d in dims
            )
            try:
                out = ch(f"SELECT {sel} FROM serve.{tbl} WHERE {dd}>=today()-120 FORMAT TSV").strip().split("\t")
            except Exception:  # noqa: BLE001
                continue
            for i, d in enumerate(dims):
                r, p = out[2 * i], out[2 * i + 1]
                if r in ("\\N", "") or p in ("\\N", ""):
                    continue
                rf, pf = float(r), float(p)
                if pf > 50 and rf < pf - 20:
                    add("ERROR", "COVERAGE_REGRESSION", f"{view}.{d}",
                        f"populated {pf:.1f}% in the prior 90 days, {rf:.1f}% in the last 30 — "
                        f"a silent upstream break; totals stay right while the dimension goes blank",
                        "fix the loader; this will not surface in any totals-based check")

    # ---- STUCK_INGEST_CURSOR ------------------------------------------------
    # A coverage regression only becomes visible weeks after the fact. The cursor
    # that caused it is observable immediately: an ingest watermark that stops
    # advancing while its siblings move on. Meta's /ads edge can return rows older
    # than the updated_time filter it was given, the local filter then empties the
    # frame, and the unchanged watermark is re-stored — deadlocking the cursor.
    dsn = os.environ.get("PG_DSN") or ""
    pg = {k: os.environ.get(k) for k in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")}
    if not any(pg.values()) and (MAGE_ENV := Path("/opt/seleric/mage-ai/.env")).exists():
        for line in MAGE_ENV.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith(("PGHOST=", "PGPORT=", "PGDATABASE=", "PGUSER=", "PGPASSWORD=")):
                k, v = line.split("=", 1)
                pg.setdefault(k, None)
                pg[k] = pg[k] or v.strip()
    if pg.get("PGHOST"):
        import subprocess
        sql = (
            "SELECT brand_id, platform, entity, cursor_value, "
            "round(EXTRACT(EPOCH FROM (now() - cursor_value::timestamptz))/3600) "
            "FROM core.brand_env_sync_state WHERE cursor_field='last_watermark'"
        )
        env = {**os.environ, "PGPASSWORD": pg.get("PGPASSWORD") or ""}
        try:
            out = subprocess.run(  # noqa: S603 - fixed local psql invocation
                ["psql", "-h", pg["PGHOST"], "-p", pg.get("PGPORT") or "5432",
                 "-U", pg.get("PGUSER") or "", "-d", pg.get("PGDATABASE") or "",
                 "-tAF", "\t", "-c", sql],
                capture_output=True, text=True, timeout=60, env=env,
            ).stdout
        except Exception as e:  # noqa: BLE001
            out = ""
            add("WARN", "CHECK_FAILED", "ingest_cursors", f"could not read sync state: {e}")
        rows = [r.split("\t") for r in out.splitlines() if r.count("\t") >= 4]
        # Compare each cursor against the freshest sibling on the same brand+platform:
        # an absolute threshold flags legitimately slow streams, a relative one does not.
        freshest: dict[tuple[str, str], float] = {}
        for b, plat, ent, val, age in rows:
            try:
                freshest[(b, plat)] = min(freshest.get((b, plat), 1e9), float(age))
            except ValueError:
                continue
        for b, plat, ent, val, age in rows:
            try:
                hours = float(age)
            except ValueError:
                continue
            peer = freshest.get((b, plat), hours)
            if hours > 48 and hours - peer > 48:
                add("ERROR", "STUCK_INGEST_CURSOR", f"{plat}.{ent} (brand {b})",
                    f"watermark is {hours:.0f}h old ({val[:19]}) while the freshest cursor on this "
                    f"account is {peer:.0f}h — the cursor has stopped advancing, so this dimension "
                    f"silently stops updating while totals stay correct",
                    "reset the watermark, or let the loader's stale-cursor circuit-breaker snapshot it")

    errors = sum(1 for f in findings if f["severity"] == "ERROR")
    if args.json:
        print(json.dumps({"errors": errors,
                          "warnings": sum(1 for f in findings if f["severity"] == "WARN"),
                          "waived": sum(1 for f in findings if f["severity"] == "WAIVED"),
                          "dimensions_checked": checked, "findings": findings}, indent=2))
        return 1 if errors else 0

    print(f"SERVE DATA QUALITY — {checked} agent-exposed dimensions scanned\n")
    by_code: dict[str, list[dict]] = collections.defaultdict(list)
    for f in findings:
        by_code[f["code"]].append(f)
    for code in sorted(by_code):
        rows = by_code[code]
        print(f"== {code}  ({sum(1 for f in rows if f['severity']=='ERROR')} errors, "
              f"{sum(1 for f in rows if f['severity']=='WAIVED')} waived)")
        for f in sorted(rows, key=lambda x: x["subject"]):
            mark = "  " if f["severity"] == "ERROR" else " ~"
            print(f"  {mark} {f['subject']}: {f['detail']}")
            if f["waiver"]:
                print(f"      waived: {f['waiver']}")
        print()
    print(f"TOTAL: {errors} errors, {sum(1 for f in findings if f['severity']=='WAIVED')} waived")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
