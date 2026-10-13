import sys, json, urllib.request, urllib.parse, time, concurrent.futures as cf
sys.path.insert(0, "/home/tervigon/work/agent_core_v3/scripts")
import v2_parity as p
tok = p.token(p.secret())
meta = json.load(open(sys.argv[1]))
jobs = []
for c in meta["cubes"]:
    if c.get("type") != "view": continue
    meas = c["measures"][0]["name"] if c["measures"] else None
    td = next((d["name"] for d in c["dimensions"] if d["type"] == "time"), None)
    for d in c["dimensions"] + [{"name": h["name"], "type": "hier"} for h in c.get("hierarchies") or []]:
        if d["type"] == "hier": continue
        jobs.append((c["name"], meas, td, d["name"], d["type"]))
def run(j):
    view, meas, td, dim, typ = j
    q = {"dimensions": [dim], "limit": 1, "filters": [{"member": f"{view}.brand_id", "operator": "equals", "values": ["20"]}]}
    if meas: q["measures"] = [meas]
    if td: q["timeDimensions"] = [{"dimension": td, "dateRange": ["2026-09-01", "2026-09-30"]}]
    if typ == "time": q = {**q, "dimensions": [], "timeDimensions": [{"dimension": dim, "granularity": "day", "dateRange": ["2026-09-01", "2026-09-30"]}]}
    req = urllib.request.Request("http://127.0.0.1:4002/cubejs-api/v1/load?query=" + urllib.parse.quote(json.dumps(q)), headers={"Authorization": tok})
    for _ in range(120):
        try: d = json.load(urllib.request.urlopen(req, timeout=300))
        except urllib.error.HTTPError as e: d = json.loads(e.read() or b"{}")
        except Exception as e: return (j, f"EXC {e}")
        if d.get("error") == "Continue wait": time.sleep(1); continue
        return (j, d.get("error"))
    return (j, "timeout")
with cf.ThreadPoolExecutor(4) as ex: res = list(ex.map(run, jobs))
bad = [(j, e) for j, e in res if e]
print(len(jobs), "dimension queries;", len(bad), "errors")
for j, e in bad: print(j[3], "|", str(e)[:230].replace("\n", " "))
