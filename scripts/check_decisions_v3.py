#!/usr/bin/env python3
"""Keep the semantic v3 decision register, the spec and the code in step.

Checks (exit 1 on any failure):
  1 every spec §0B row has a register entry, and every register entry is a spec row (or a named sub-decision);
  2 status / phase are valid; implemented entries list where they live and how to change them;
  3 every file an implemented entry cites exists and mentions the decision id;
  4 every decision id cited in v3 code is in the register;
  5 every outcome evidence code the v3 SQL can emit is mapped to a decision, and every mapped code exists in the SQL;
  6 every known limit (KNOWN_LIMITS.yaml) points at registered decisions;
  7 the rendered docs (DECISIONS.md, KNOWN_LIMITS.md, the plan sections) are current.

  python3 scripts/check_decisions_v3.py --mage /home/tervigon/work/mage_v3
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "doc", "Seleric_Enterprise_Data_Model_v2_Two_Planes.md")
REG = os.path.join(ROOT, "doc", "semantic_v3", "DECISIONS.yaml")
STATUSES = {"implemented", "planned", "scope", "process", "superseded"}
SUB = {"D-02a", "D-09a", "D-20b"}  # sub-decisions recorded inside their parent spec row
CODE_GLOBS = ("serve/semantic_v3/views/*.sql", "serve/core/views/*.sql", "serve/semantic_v3/*.py", "serve/evaluations/v3_*.py")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mage", default="/home/tervigon/work/mage_v3")
    a = ap.parse_args()
    errs: list[str] = []
    spec_ids = set(re.findall(r"^\| (D-\d+[a-z]*) \|", open(SPEC).read(), re.M))
    reg = yaml.safe_load(open(REG))["decisions"]
    ids = [d["id"] for d in reg]
    if len(ids) != len(set(ids)):
        errs.append("duplicate register ids")
    for i in sorted(spec_ids - set(ids)):
        errs.append(f"spec row {i} has no register entry")
    for i in sorted(set(ids) - spec_ids - SUB):
        errs.append(f"register entry {i} is not a spec §0B row")

    files = [f for g in CODE_GLOBS for f in glob.glob(os.path.join(a.mage, g))]
    code = {f: open(f).read() for f in files}
    cited_codes: set[str] = set()
    for d in reg:
        if d.get("status") not in STATUSES:
            errs.append(f"{d['id']}: bad status {d.get('status')}")
        if d.get("status") in ("implemented", "planned") and d.get("phase") is None:
            errs.append(f"{d['id']}: {d['status']} without phase")
        if d.get("status") == "implemented":
            locs = d.get("implemented_in") or []
            if not locs:
                errs.append(f"{d['id']}: implemented but no implemented_in")
            for loc in locs:
                if loc.startswith("(no code"):
                    continue
                path = os.path.join(a.mage, loc.split(":", 1)[1].split(" (")[0])
                if not os.path.exists(path):
                    errs.append(f"{d['id']}: file missing {path}")
                elif not re.search(rf"\b{re.escape(d['id'])}\b", open(path).read()):
                    errs.append(f"{d['id']}: {os.path.relpath(path, a.mage)} does not mention {d['id']}")
            if locs and not locs[0].startswith("(no code") and not d.get("change_how"):
                errs.append(f"{d['id']}: no change_how")
        cited_codes |= set(d.get("evidence_codes") or [])

    known = set(ids)
    for f, txt in code.items():
        for i in set(re.findall(r"\bD-\d+[a-z]*\b", txt)):
            if i not in known:
                errs.append(f"{os.path.relpath(f, a.mage)} cites {i}, not in the register")

    # outcome evidence codes emitted by the classification SQL: ('<outcome>', '<code>') tuples
    emitted: set[str] = set()
    for name in ("01_core_order_class.sql", "02_core_order_line_class.sql"):
        p = os.path.join(a.mage, "serve/semantic_v3/views", name)
        sql = open(p).read()
        # result tuples of the outcome multiIf: ('<outcome>', '<code>') or (c.outcome, '<code>'), each followed by `,` or `)`
        emitted |= {m.group(1) for m in re.finditer(r"\((?:'\w+'|c\.outcome),\s*'(\w+)'\)\s*[,)]", sql)}
    emitted |= {"exchange_failed_replacement", "refund_without_return_all_lines"}  # emitted via if() / multiIf, not tuples
    emitted -= {"active", "cancelled", "rto", "returned", "exchanged", "unknown",
                "ADJUSTMENT", "partial", "return_exchange"}  # IN-list literals, not outcome codes
    for c in sorted(emitted - cited_codes):
        errs.append(f"evidence code {c} is emitted by the SQL but mapped to no decision")
    for c in sorted(cited_codes - emitted):
        errs.append(f"evidence code {c} is mapped in the register but not emitted by the SQL")

    # known limits: handled_by must name registered decisions; ids unique; statuses valid
    lims = yaml.safe_load(open(os.path.join(ROOT, "doc", "semantic_v3", "KNOWN_LIMITS.yaml")))["limits"]
    lids = [l["id"] for l in lims]
    if len(lids) != len(set(lids)):
        errs.append("duplicate known-limit ids")
    for l in lims:
        if l.get("status") not in ("accepted", "open", "planned"):
            errs.append(f"{l['id']}: bad status {l.get('status')}")
        for d in l.get("handled_by") or []:
            if d not in known:
                errs.append(f"{l['id']}: handled_by {d} is not in the decision register")
    # rendered docs (DECISIONS.md, KNOWN_LIMITS.md, plan sections) must be current
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "render_semantic_v3_docs.py"), "--check"],
                       capture_output=True, text=True)
    if r.returncode:
        errs.append("rendered docs stale — run scripts/render_semantic_v3_docs.py")

    for e in errs:
        print("FAIL", e)
    impl = sum(d["status"] == "implemented" for d in reg)
    print(f"{len(reg)} decisions ({impl} implemented); {len(emitted)} evidence codes; {len(files)} code files; "
          f"{'OK' if not errs else f'{len(errs)} problems'}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
