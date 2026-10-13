#!/usr/bin/env python3
"""Semantic v3 Phase 0 — repo ↔ live DDL drift for every ClickHouse `serve` and `semantic` object.

Repo side: every CREATE statement in mage-ai `serve/<domain>/views/*.sql` and `serve/semantic/views/*.sql`
(git HEAD of the checkout given by --mage). Live side: `system.tables.create_table_query`. Both sides are
normalised by ClickHouse itself (`formatQuerySingleLine`, run with readonly=1), then `CREATE OR REPLACE` /
`IF NOT EXISTS` and table SETTINGS defaults are folded, so only real differences remain.

  python3 scripts/serve_ddl_drift_v3.py [--mage /opt/seleric/mage-ai] [--json out.json] [--md out.md]

Read-only. Exit 0 always (report tool); the counts are printed.
"""
from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

CREATE_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(MATERIALIZED\s+VIEW|VIEW|TABLE|DATABASE|DICTIONARY)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"`?(\w+)`?(?:\.`?(\w+)`?)?", re.I)


def load_env(mage: str) -> None:
    for line in open(os.path.join(mage, ".env")):
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")


def ch(sql: str, params: dict | None = None) -> str:
    q = {"readonly": "1", "max_execution_time": "120", **{f"param_{k}": v for k, v in (params or {}).items()}}
    url = f"http://{os.environ['CLICKHOUSE_HOST']}:{os.environ.get('CLICKHOUSE_PORT', '8123')}/?" + \
        urllib.parse.urlencode(q)
    req = urllib.request.Request(url, data=sql.encode(), method="POST")
    req.add_header("X-ClickHouse-User", os.environ["CLICKHOUSE_USER"])
    req.add_header("X-ClickHouse-Key", os.environ["CLICKHOUSE_PASSWORD"])
    try:
        return urllib.request.urlopen(req, timeout=300).read().decode()
    except urllib.error.HTTPError as e:
        return "ERROR " + e.read().decode()[:400]


def strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    out = []
    for line in sql.splitlines():
        # drop -- comments that are not inside a quoted string (good enough for this repo's SQL)
        if "--" in line and line.count("'", 0, line.index("--")) % 2 == 0:
            line = line[: line.index("--")]
        out.append(line)
    return "\n".join(out)


def statements(sql: str) -> list[str]:
    parts, cur, quote = [], [], False
    for ch_ in strip_comments(sql):
        if ch_ == "'":
            quote = not quote
        if ch_ == ";" and not quote:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch_)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def fmt(sql: str) -> str:
    esc = sql.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")  # param = TSV
    out = ch("SELECT formatQuerySingleLine({q:String}) FORMAT TSVRaw", {"q": esc}).rstrip("\n")
    if out.startswith("ERROR"):
        return out
    s = re.sub(r"^CREATE OR REPLACE ", "CREATE ", out)
    s = re.sub(r"\bIF NOT EXISTS ", "", s)
    s = re.sub(r",?\s*SETTINGS index_granularity = 8192$", "", s)
    s = re.sub(r"\s+SETTINGS index_granularity = 8192\b", "", s)
    return s


def tokens(s: str) -> list[str]:
    """Tokens; unquoted words lower-cased (ClickHouse re-renders `nullif` as `nullIf`)."""
    return [t if t[0] in "`'" else t.lower() for t in re.findall(r"`[^`]*`|'(?:[^'\\]|\\.)*'|\w+|[^\s\w]", s)]


def strip_view_columns(toks: list[str]) -> list[str]:
    """Live VIEW / MATERIALIZED VIEW DDL carries an explicit `(col Type, …)` list the repo files omit; drop it."""
    for anchor in ("to", "view"):  # refreshable MV: `… TO db.target (cols)`; view: `VIEW db.name (cols)`
        idx = [k for k, t in enumerate(toks[:60]) if t == anchor and k + 4 < len(toks) and toks[k + 4] == "("]
        if idx:
            i = idx[0] + 4
            break
    else:
        return toks
    if toks[i] == "(":
        depth = 0
        for j in range(i, len(toks)):
            depth += toks[j] == "("
            depth -= toks[j] == ")"
            if depth == 0:
                return toks[:i] + toks[j + 1:]
    return toks


def contains(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(hay[k:k + n] == needle for k in range(len(hay) - n + 1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mage", default="/opt/seleric/mage-ai")
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args()
    load_env(a.mage)
    head = subprocess.run(["git", "-C", a.mage, "rev-parse", "--short", "HEAD"], capture_output=True,
                          text=True).stdout.strip()

    repo: dict[str, dict] = {}
    files = sorted(glob.glob(os.path.join(a.mage, "serve", "*", "views", "*.sql")))
    for f in files:
        for st in statements(open(f).read()):
            m = CREATE_RE.match(st)
            if not m or m.group(1).upper() == "DATABASE":
                continue
            db, name = (m.group(2), m.group(3)) if m.group(3) else ("serve", m.group(2))
            key = f"{db}.{name}"
            if key in repo:
                repo[key].setdefault("also_in", []).append(os.path.relpath(f, a.mage))
                continue
            repo[key] = {"file": os.path.relpath(f, a.mage), "sql": st}

    live_rows = ch("SELECT database, name, engine, create_table_query FROM system.tables "
                   "WHERE database IN ('serve','semantic') AND NOT is_temporary ORDER BY 1,2 FORMAT JSONEachRow")
    live = {}
    for line in live_rows.splitlines():
        r = json.loads(line)
        live[f"{r['database']}.{r['name']}"] = r

    results = []
    for key in sorted(set(repo) | set(live)):
        r, l = repo.get(key), live.get(key)
        row = {"object": key, "engine": l["engine"] if l else None, "file": r["file"] if r else None}
        if r and r.get("also_in"):
            row["also_in"] = r["also_in"]
        if key.startswith("semantic.") and l and l["engine"] == "MergeTree" and key.startswith("semantic.mv_"):
            pass
        if not l:
            row["status"] = "repo_only"
        elif not r:
            row["status"] = "live_only"
        else:
            fr, fl = fmt(r["sql"]), fmt(l["create_table_query"])
            if fr.startswith("ERROR") or fl.startswith("ERROR"):
                row["status"] = "format_error"
                row["detail"] = (fr if fr.startswith("ERROR") else fl)[:300]
            elif fr == fl:
                row["status"] = "same"
            else:
                tr, tl = tokens(fr), tokens(fl)
                if l["engine"] in ("View", "MaterializedView"):
                    tl = strip_view_columns(tl)
                ops = [o for o in difflib.SequenceMatcher(a=tr, b=tl, autojunk=False).get_opcodes() if o[0] != "equal"]
                # ClickHouse stores a view with top-level CTEs copied into the subqueries that use them: an insert
                # whose tokens are a verbatim copy of repo text is that propagation, not drift.
                ops = [o for o in ops if not (o[0] == "insert" and o[4] - o[3] >= 8 and contains(tr, tl[o[3]:o[4]]))]
                if not ops:
                    row["status"] = "same"
                    results.append(row)
                    continue
                row["status"] = "differs"
                row["changes"] = len(ops)
                row["detail"] = [f"{o[0]}: repo «{' '.join(tr[o[1]:o[2]])[:160]}» live «{' '.join(tl[o[3]:o[4]])[:160]}»"
                                 for o in ops[:8]]
        results.append(row)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"mage-ai HEAD {head}; repo objects {len(repo)}, live objects {len(live)}; {counts}")
    for r in results:
        if r["status"] != "same":
            print(f"  {r['status']:12} {r['object']:45} {r['file'] or ''} {r.get('changes', '')}")
    if a.json:
        json.dump({"mage_head": head, "counts": counts, "objects": results}, open(a.json, "w"), indent=1)
    if a.md:
        with open(a.md, "w") as fh:
            fh.write(f"# serve / semantic DDL drift (mage-ai {head})\n\n{counts}\n\n")
            for r in results:
                if r["status"] == "same":
                    continue
                fh.write(f"## {r['object']} — {r['status']}\nfile: `{r['file']}`  engine: {r['engine']}\n")
                for d in r.get("detail") or []:
                    fh.write(f"- {d}\n")
                fh.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
