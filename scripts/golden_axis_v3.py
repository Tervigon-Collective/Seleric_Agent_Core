#!/usr/bin/env python3
"""Phase 7: the date-axis golden questions (mage-ai serve/evaluations/axis_golden_questions.yml) on the v3 planner path.

Each question names the request an agent must build from the wording (metric + date_axis). This checks that the request plans and answers
with that axis applied, or is refused with a reason where `refused: true` — i.e. the platform side of "processed in" / "happened in" is
certified; whether the LLM picks the axis from the wording is tested by the Seleric_Agent evals in Phase 8.
  .venv/bin/python scripts/golden_axis_v3.py [--file path]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import capability_check_v3 as C  # noqa: E402

DEFAULT = "/home/tervigon/work/mage_v3/serve/evaluations/axis_golden_questions.yml"


async def amain(a) -> int:
    v3 = C.Stack("v3", a.v3_url, ROOT / "catalogue_v3")
    qs = yaml.safe_load(open(a.file))["questions"]
    bad = 0
    for q in qs:
        e = q["expected"]
        r = await v3.ask(C.req(e["metric"], e["time_range"]["start"], e["time_range"]["end"], "20", axis=e["date_axis"]))
        if e.get("refused"):
            ok, note = "refused" in r and len(r["refused"]) > 20, (r.get("refused") or r.get("error") or "ANSWERED")[:90]
        else:
            applied = (r.get("axis") or {}).get(e["metric"]) if isinstance(r.get("axis"), dict) else r.get("axis")
            ok = "rows" in r and bool(r["rows"]) and e.get("applied_axis", e["date_axis"]) in str(applied)
            note = f"axis={applied} value={C.scalar(r, e['metric']) if 'rows' in r else r.get('refused') or r.get('error')}"
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {q['question']}  → {e['metric']} @ {e['date_axis']}: {note}")
    print(f"{len(qs) - bad}/{len(qs)} golden axis questions pass")
    await v3.cube.aclose()
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--v3-url", default="http://127.0.0.1:4003")
    return asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
