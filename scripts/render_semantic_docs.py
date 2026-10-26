#!/usr/bin/env python3
"""Render the semantic layer decision and known-limit registers into readable docs and the plan.

Sources (edit these, never the rendered output):
  doc/semantic/DECISIONS.yaml     every business decision (ruling, status, where implemented, tests)
  doc/semantic/KNOWN_LIMITS.yaml  what the data / model cannot do, impact, next step
Rendered:
  doc/semantic/DECISIONS.md, doc/semantic/KNOWN_LIMITS.md
  plan/SEMANTIC_V3_REBUILD_PLAN.md and doc/semantic/PLAN.md — the blocks between
  <!-- BEGIN GENERATED: decisions --> / <!-- END GENERATED: decisions --> and the same for limits

  python3 scripts/render_semantic_docs.py           # write
  python3 scripts/render_semantic_docs.py --check   # exit 1 when any rendered file is stale
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "doc", "semantic")
PLANS = [os.path.join(ROOT, "plan", "SEMANTIC_V3_REBUILD_PLAN.md"), os.path.join(DOC, "PLAN.md")]

AREAS = [  # (title, ids)
    ("Orders, outcomes and customers", ["D-01", "D-01d", "D-02", "D-02a", "D-05", "D-08", "D-08a", "D-08b", "D-15", "D-16", "D-16a",
                                        "D-20", "D-22", "D-22a", "D-22b", "D-22c", "D-22d", "D-22e", "D-22f", "D-22g", "D-22h", "D-22i"]),
    ("Revenue, GST and deductions", ["D-02b", "D-09", "D-09a", "D-10", "D-10a", "D-22j", "D-22k", "D-25", "D-20b", "D-01a", "D-01b", "D-01c"]),
    ("Costs", ["D-03", "D-03a", "D-05a", "D-05b", "D-08c", "D-11", "D-11a", "D-11c", "D-11d"]),
    ("Ads, website and attribution", ["D-00", "D-06", "D-06a", "D-06b", "D-13", "D-13r", "D-14", "D-17"]),
    ("Profit, periods and product ads", ["D-07", "D-12", "D-12a", "D-18", "D-18r", "D-19", "D-19o", "D-21"]),
    ("Date axis (order_date / event_date)", ["D-28", "D-29", "D-30", "D-31", "D-32", "D-33", "D-34", "D-35", "D-36", "D-37"]),
    ("Scope and process", ["D-04", "D-23", "D-24", "D-26"]),
]


def cell(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).replace("|", "/").strip()


def short(s: str, n: int = 260) -> str:
    s = cell(s)
    s = re.sub(r"^(DECIDED|\*\*DECIDED)[^:]*:\s*", "", s)
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def where(d: dict) -> str:
    locs = d.get("implemented_in") or []
    files = sorted({re.sub(r"^mage-ai:serve/(semantic_v3/views/|core/views/)?", "", l).split(" (")[0] for l in locs})
    return ", ".join(f"`{f}`" for f in files)


def decisions_tables(reg: list[dict]) -> str:
    by = {d["id"]: d for d in reg}
    placed = {i for _, ids in AREAS for i in ids}
    areas = AREAS + ([("Other", sorted(set(by) - placed))] if set(by) - placed else [])
    out = []
    for title, ids in areas:
        rows = [by[i] for i in ids if i in by]
        if not rows:
            continue
        out.append(f"#### {title}\n\n| ID | Decision | Ruling | Status | Lives in | Evidence / ₹ |\n|---|---|---|---|---|---|")
        for d in rows:
            st = d["status"] + (f" (P{d['phase']})" if d.get("phase") is not None else "")
            out.append(f"| {d['id']} | {cell(d.get('title'))} | {short(d.get('ruling', ''))} | {st} | {where(d)} | {short(d.get('evidence', ''), 140)} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def limits_table(lims: list[dict]) -> str:
    out = ["| ID | Area | Limit | Impact | Handled by | Status | Next step |", "|---|---|---|---|---|---|---|"]
    for l in lims:
        out.append(f"| {l['id']} | {l['area']} | {cell(l['limit'])} | {cell(l['impact'])} | "
                   f"{', '.join(l.get('handled_by') or []) or '—'} | {l['status']} | {cell(l.get('next_step'))} |")
    return "\n".join(out) + "\n"


def decisions_checklist(reg: list[dict]) -> str:
    by = {d["id"]: d for d in reg}
    placed = {i for _, ids in AREAS for i in ids}
    areas = AREAS + ([("Other", sorted(set(by) - placed))] if set(by) - placed else [])
    out = []
    for title, ids in areas:
        rows = [by[i] for i in ids if i in by]
        if not rows:
            continue
        out.append(f"#### {title}")
        for d in rows:
            done = d["status"] in ("implemented", "scope", "process", "superseded")
            st = d["status"] + (f", phase {d['phase']}" if d.get("phase") is not None else "")
            loc = f" · {where(d)}" if where(d) else ""
            out.append(f"- [{'x' if done else ' '}] **{d['id']}** {cell(d.get('title'))} — {short(d.get('ruling', ''), 220)} _({st})_{loc}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def limits_checklist(lims: list[dict]) -> str:
    out = []
    for l in lims:
        done = l["status"] == "accepted"
        h = ", ".join(l.get("handled_by") or [])
        out.append(f"- [{'x' if done else ' '}] **{l['id']}** ({l['area']}, {l['status']}) {cell(l['limit'])} "
                   f"Impact: {cell(l['impact'])}{f' Handled by {h}.' if h else ''} Next: {cell(l.get('next_step'))}")
    return "\n".join(out) + "\n"


def render() -> dict[str, str]:
    reg = yaml.safe_load(open(os.path.join(DOC, "DECISIONS.yaml")))["decisions"]
    lims = yaml.safe_load(open(os.path.join(DOC, "KNOWN_LIMITS.yaml")))["limits"]
    n_impl = sum(d["status"] == "implemented" for d in reg)
    dec_block = (f"{len(reg)} decisions ({n_impl} implemented). Source: `doc/semantic/DECISIONS.yaml` (where each lives, evidence "
                 f"codes, tests, how to change); business wording: spec §0B; check: `scripts/check_decisions.py`.\n\n"
                 + decisions_tables(reg))
    open_l = [l for l in lims if l["status"] == "open"]
    lim_block = (f"{len(lims)} known limits ({len(open_l)} open). Source: `doc/semantic/KNOWN_LIMITS.yaml`.\n\n" + limits_table(lims))
    plan_dec = (f"{len(reg)} decisions ({n_impl} implemented). `[x]` = implemented (or a scope / process rule), `[ ]` = decided, "
                f"built in the named phase. Full table: `doc/semantic/DECISIONS.md`; source: `doc/semantic/DECISIONS.yaml`; "
                f"business wording: spec §0B.\n\n" + decisions_checklist(reg))
    plan_lim = (f"{len(lims)} known limits ({len(open_l)} open). `[x]` = accepted and shown to users, `[ ]` = open or planned. "
                f"Full table: `doc/semantic/KNOWN_LIMITS.md`; source: `doc/semantic/KNOWN_LIMITS.yaml`.\n\n" + limits_checklist(lims))
    files = {
        os.path.join(DOC, "DECISIONS.md"): "# Semantic layer — decisions\n\n_Generated by scripts/render_semantic_docs.py from "
                                           "DECISIONS.yaml — edit the YAML._\n\n" + dec_block,
        os.path.join(DOC, "KNOWN_LIMITS.md"): "# Semantic layer — known limits\n\n_Generated by scripts/render_semantic_docs.py from "
                                              "KNOWN_LIMITS.yaml — edit the YAML._\n\n" + lim_block,
    }
    for plan in PLANS:
        txt = open(plan).read()
        for name, block in (("decisions", plan_dec), ("limits", plan_lim)):
            b, e = f"<!-- BEGIN GENERATED: {name} -->", f"<!-- END GENERATED: {name} -->"
            if b not in txt:
                raise SystemExit(f"{plan}: missing marker {b}")
            txt = re.sub(re.escape(b) + r".*?" + re.escape(e), lambda _m, bl=block, bb=b, ee=e: f"{bb}\n{bl}{ee}", txt, flags=re.S)
        files[plan] = txt
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    files = render()
    stale = [p for p, t in files.items() if not os.path.exists(p) or open(p).read() != t]
    if a.check:
        for p in stale:
            print("STALE", os.path.relpath(p, ROOT))
        print("rendered docs", "OK" if not stale else f"stale ({len(stale)}) — run scripts/render_semantic_docs.py")
        return 1 if stale else 0
    for p in stale:
        open(p, "w").write(files[p])
        print("wrote", os.path.relpath(p, ROOT))
    if not stale:
        print("rendered docs already current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
