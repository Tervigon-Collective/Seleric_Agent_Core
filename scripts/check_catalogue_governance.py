#!/usr/bin/env python3
"""One CI gate for catalogue governance. Exit non-zero on any drift so nothing
ships silently and the operator is told the single thing to fix.

Checks (each independent; network checks warn-skip if the endpoint is down):
  1. integrity        catalogue loads (loader._check_integrity)
  2. dim coverage     dimensions/generated_coverage.yaml matches Cube /meta
  3. crosswalk        openmetadata/crosswalk.generated.yaml matches its sources
  4. broken metrics   every metric's Cube members exist in live /meta
  5. descriptions     no NEW metric-description overlap above threshold
  6. deploy==repo     live MCP catalogue_version matches the repo (F-0)

  py scripts/check_catalogue_governance.py
  py scripts/check_catalogue_governance.py --accept-descriptions   # reseed waivers
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

import yaml

CORE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE / "src"))
from seleric_mcp.catalogue_service.loader import load_catalogue  # noqa: E402

CAT_DIR = CORE / "catalogue"
CUBE_API = os.environ.get("SELERIC_CUBE_API", "http://127.0.0.1:4001")
MCP_META = os.environ.get("SELERIC_MCP_VERSION_URL")  # optional endpoint returning catalogue_version
DESC_THRESHOLD = 0.80
DESC_WAIVERS = CAT_DIR / "description_overlap_waivers.yaml"

OK, WARN, FAIL = "ok", "warn", "fail"


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split())


def _desc_pairs(cat) -> list[tuple[str, str, float]]:
    ms = list(cat.metrics.values())
    pairs = []
    for i in range(len(ms)):
        a = _norm(ms[i].description)[:400]
        if not a:
            continue
        for j in range(i + 1, len(ms)):
            b = _norm(ms[j].description)[:400]
            if not b:
                continue
            r = SequenceMatcher(None, a, b).ratio()
            if r >= DESC_THRESHOLD:
                pairs.append((min(ms[i].id, ms[j].id), max(ms[i].id, ms[j].id), round(r, 3)))
    return sorted(pairs)


def _cube_members() -> set[str] | None:
    try:
        with urllib.request.urlopen(f"{CUBE_API}/cubejs-api/v1/meta", timeout=20) as r:
            meta = json.loads(r.read().decode())
    except Exception:
        return None
    out: set[str] = set()
    for c in meta.get("cubes", []):
        for kind in ("measures", "dimensions"):
            for m in c.get(kind, []):
                out.add(m["name"])
    return out


def _run_check(script: str) -> tuple[str, str]:
    p = subprocess.run(
        [sys.executable, str(CORE / "scripts" / script), "--check"],
        capture_output=True, text=True,
    )
    out = p.stdout + p.stderr
    tail = out.strip().splitlines()
    msg = tail[-1] if tail else ""
    if p.returncode == 0:
        return OK, msg
    # A source being unreachable (ClickHouse/Cube down) is not drift — warn-skip so
    # the gate still runs offline. Only an actual STALE verdict is a hard failure.
    if re.search(r"URLError|WinError|refused|unreachable|timed out|Connection", out, re.I) \
            and "STALE" not in out.upper():
        return WARN, "source unreachable — skipped"
    return FAIL, msg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accept-descriptions", action="store_true",
                    help="write current description overlaps to the waiver file and exit")
    args = ap.parse_args()

    results: list[tuple[str, str, str]] = []

    # 1. integrity
    try:
        cat = load_catalogue(CAT_DIR)
        results.append((OK, "integrity", f"loads clean ({len(cat.metrics)} metrics, v{cat.version})"))
    except Exception as e:
        results.append((FAIL, "integrity", str(e).splitlines()[0]))
        _report(results)
        return 1

    # 5-seed. reseed description waivers if asked
    pairs = _desc_pairs(cat)
    if args.accept_descriptions:
        DESC_WAIVERS.write_text(
            "# Accepted metric-description overlaps (>= %.2f similarity). The governance\n"
            "# gate fails on any NEW pair not listed here — keep new metrics distinct.\n"
            "# Reseed: py scripts/check_catalogue_governance.py --accept-descriptions\n"
            % DESC_THRESHOLD
            + yaml.safe_dump({"accepted": [f"{a}|{b}" for a, b, _ in pairs]}, sort_keys=False),
            encoding="utf-8",
        )
        print(f"wrote {DESC_WAIVERS.relative_to(CORE)} ({len(pairs)} accepted pairs)")
        return 0

    # 2 & 3. generated-file freshness
    results.append((*_run_check("sync_dimension_coverage.py"),))  # type: ignore[misc]
    results[-1] = (results[-1][0], "dim_coverage", results[-1][1])
    results.append((*_run_check("sync_catalogue_from_sources.py"),))  # type: ignore[misc]
    results[-1] = (results[-1][0], "crosswalk", results[-1][1])

    # 4. broken metrics vs live /meta
    members = _cube_members()
    if members is None:
        results.append((WARN, "broken_metrics", "Cube /meta unreachable — skipped"))
    else:
        broken = []
        for m in cat.metrics.values():
            needed = [m.cube_mapping.measure]
            if m.cube_mapping.measure_pct:
                needed.append(m.cube_mapping.measure_pct)
            if m.ratio_components:
                needed += [m.ratio_components.numerator, m.ratio_components.denominator]
            for did in m.supported_dimensions:
                d = cat.dimensions.get(did)
                if d and m.cube_mapping.view in d.views:
                    needed.append(d.views[m.cube_mapping.view])
            if [n for n in needed if n not in members]:
                broken.append(m.id)
        results.append((OK if not broken else FAIL, "broken_metrics",
                        "none" if not broken else f"{len(broken)}: {', '.join(broken[:8])}"))

    # 5. description overlap ratchet
    accepted = set()
    if DESC_WAIVERS.exists():
        accepted = set((yaml.safe_load(DESC_WAIVERS.read_text()) or {}).get("accepted") or [])
    new = [(a, b, r) for a, b, r in pairs if f"{a}|{b}" not in accepted]
    if not new:
        results.append((OK, "descriptions", f"{len(pairs)} overlaps, all accepted"))
    else:
        results.append((FAIL, "descriptions",
                        f"{len(new)} NEW overlap(s): " +
                        ", ".join(f"{a}~{b}({r})" for a, b, r in new[:5]) +
                        " — differentiate, or accept with --accept-descriptions"))

    # 6. deploy==repo
    if not MCP_META:
        results.append((WARN, "deploy==repo", "SELERIC_MCP_VERSION_URL unset — skipped"))
    else:
        try:
            with urllib.request.urlopen(MCP_META, timeout=15) as r:
                live = json.loads(r.read().decode()).get("catalogue_version")
            results.append((OK if live == cat.version else FAIL, "deploy==repo",
                            f"live={live} repo={cat.version}"))
        except Exception as e:
            results.append((WARN, "deploy==repo", f"unreachable: {e}"))

    return _report(results)


def _report(results) -> int:
    icons = {OK: "PASS", WARN: "WARN", FAIL: "FAIL"}
    print("\nCatalogue governance gate")
    print("-" * 60)
    for status, name, msg in results:
        print(f"  [{icons[status]}] {name:16} {msg}")
    failed = [r for r in results if r[0] == FAIL]
    print("-" * 60)
    print(f"{len(failed)} failed, "
          f"{sum(1 for r in results if r[0]==WARN)} warned, "
          f"{sum(1 for r in results if r[0]==OK)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
