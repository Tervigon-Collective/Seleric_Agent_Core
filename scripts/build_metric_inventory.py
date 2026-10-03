#!/usr/bin/env python3
"""Build doc/METRIC_INVENTORY.xlsx — every metric that can be fetched or calculated.

Three surfaces are inventoried, each with its formula and how it is derived:

  MCP / catalogue   the approved metric ids the agent can query by name, with the
                    Cube expression that executes, the serve column behind it and
                    the gold inputs underneath
  Cube semantic     measures that exist in the Cube model but have no catalogue id,
                    so they are computable but not yet agent-queryable
  Node backend      the dashboard/API layer (a separate implementation over the same
                    gold tables): SQL aliases, JS-computed KPIs, API response fields
                    and platform-API source fields

Run:  uv run --with openpyxl python scripts/build_metric_inventory.py [--check [--warn-only]] [--baseline PATH]
"""
from __future__ import annotations

import collections
import datetime
import glob
import json
import os
import re

import yaml

CORE = os.environ.get("SELERIC_CORE", "/opt/seleric/Seleric_Agent_Core")
CUBE = os.environ.get("SELERIC_CUBE_DIR", "/opt/seleric/mage-ai/infra/cube") + "/model"
SERVE = os.environ.get("SELERIC_SERVE_DIR", "/opt/seleric/mage-ai/serve")
NODE_ROOT = os.environ.get("SELERIC_NODE_BACKEND", "/srv/seleric/javascript_projects/Node-Backend/src")
OUT = os.environ.get("SELERIC_INVENTORY_OUT", os.path.join(CORE, "doc", "METRIC_INVENTORY.xlsx"))

AGENT = os.environ.get("SELERIC_AGENT", "/opt/seleric/Seleric_Agent")
CUBE_URL = os.environ.get("CUBE_API_URL", "http://127.0.0.1:4001")
# Live value probe: brand + last full month. Metrics returning the same non-zero
# number are grouped so duplicate ids (one number, several lineages) are visible.
PROBE_BRAND = os.environ.get("SELERIC_INVENTORY_BRAND", "20")
PROBE_LIVE = os.environ.get("SELERIC_INVENTORY_LIVE", "1") == "1"

# Business phrasing checked against every resolver, on top of every alias the
# glossary, concept layer and agent registry declare.
PROBE_PHRASES = ["revenue","sales","net sales","gross sales","total sales","profit","net profit",
    "gross profit","contribution margin","margin","net margin","roas","meta roas","google roas","mer",
    "cac","ltv","aov","orders","returns","return rate","rto","cogs","ad spend","meta spend","spend",
    "conversion rate","cpa","cod orders","amazon sales","new customers","repeat rate",
    "profit by channel","net profit meta","revenue by city","net revenue","gmv"]


def resolution_lexicon():
    """metric id -> the vocabulary that routes to it, from every layer that resolves words to ids."""
    glossary=collections.defaultdict(list); concepts=collections.defaultdict(list)
    registry=collections.defaultdict(list)
    for t in yaml.safe_load(open(f'{CORE}/catalogue/glossary/terms.yaml'))['terms']:
        if t.get('canonical_id'): glossary[t['canonical_id']].append(t['term'])
    for f in sorted(glob.glob(f'{CORE}/catalogue/concepts/*.yaml')):
        c=yaml.safe_load(open(f))
        for r in c.get('resolves') or []:
            axes=','.join(f'{k}={v}' for k,v in (r.get('when') or {}).items())
            concepts[r.get('metric')].append(f"{c['id']}({axes})")
    reg_path=f'{AGENT}/config/metric_registry.yaml'
    reg=yaml.safe_load(open(reg_path))['metrics'] if os.path.exists(reg_path) else []
    for r in reg:
        registry[r.get('catalogue_metric')].append(r['id'])
    return {'glossary':glossary,'concepts':concepts,'registry':registry,'registry_rows':reg,
            'measure_groups':{}}


def resolution_conflicts(lex):
    """Run each phrase through every resolver; a row conflicts when they name different ids."""
    import sys
    sys.path.insert(0, f'{CORE}/src')
    from seleric_mcp.catalogue_service.loader import load_catalogue
    from seleric_mcp.catalogue_service.service import CatalogueService
    from seleric_mcp.config import load_settings
    s=load_settings()
    svc=CatalogueService(load_catalogue(s.catalogue_dir), auto_threshold=s.resolve_auto_threshold,
        ambiguous_threshold=s.resolve_ambiguous_threshold, runner_up_margin=s.resolve_runner_up_margin,
        serve_db=s.serve_db)
    reg_alias={}
    for r in lex['registry_rows']:
        for a in r.get('aliases') or []: reg_alias.setdefault(a.lower(), r.get('catalogue_metric'))
    phrases=set(p.lower() for p in PROBE_PHRASES) | set(reg_alias)
    for f in glob.glob(f'{CORE}/catalogue/concepts/*.yaml'):
        phrases |= {a.lower() for a in yaml.safe_load(open(f)).get('aliases') or []}
    def mid(r):
        d=r.model_dump(); k=d.get('kind','')
        if d.get('metric_id'): return d['metric_id'], k
        cands=d.get('candidates') or []
        return ('?'+'|'.join(c['metric_id'] for c in cands[:3]) if cands else ''), k
    rows=[]
    for p in sorted(phrases):
        term,tk=mid(svc.resolve_term(p))
        try: conc,ck=mid(svc.resolve_concept(p))
        except Exception as e: conc,ck='',f'error: {e}'[:60]
        hits=svc.search(p).matches
        srch=hits[0].id if hits else ''
        agent=reg_alias.get(p,'')
        named={x for x in (term,conc,srch,agent) if x and not x.startswith('?')}
        rows.append({'phrase':p,'resolve_term':term,'term_kind':tk,'resolve_concept':conc,'concept_kind':ck,
                     'search_top1':srch,'agent_registry':agent,
                     'verdict':'CONFLICT' if len(named)>1 else ('UNRESOLVED' if not named else 'agree')})
    rows.sort(key=lambda r:({'CONFLICT':0,'UNRESOLVED':1,'agree':2}[r['verdict']], r['phrase']))
    return rows


def _cube_token():
    import base64, hashlib, hmac, time
    secret=os.environ.get('CUBEJS_API_SECRET')
    if not secret:
        envf=os.path.join(os.path.dirname(CUBE), '.env')
        for line in open(envf) if os.path.exists(envf) else []:
            if line.startswith('CUBEJS_API_SECRET='): secret=line.split('=',1)[1].strip()
    if not secret: return None
    b64=lambda b: base64.urlsafe_b64encode(b).rstrip(b'=')
    head=b64(b'{"alg":"HS256","typ":"JWT"}')+b'.'+b64(json.dumps({'iat':int(time.time())}).encode())
    return (head+b'.'+b64(hmac.new(secret.encode(),head,hashlib.sha256).digest())).decode()


def last_full_months(n):
    """[(start, end)] for the n most recent complete calendar months, newest first."""
    out=[]; end=datetime.date.today().replace(day=1)-datetime.timedelta(days=1)
    for _ in range(n):
        start=end.replace(day=1); out.append((start,end)); end=start-datetime.timedelta(days=1)
    return out


def probe_values(mcp, brand, start, end):
    """metric id -> live value (rounded 1e-6) for one brand over [start, end]; None = error/no data."""
    import time, urllib.parse, urllib.request, concurrent.futures as cf
    tok=_cube_token()
    if not tok: return {}
    def run(r):
        q={'measures':[r['cube_measure']],'timezone':'Asia/Kolkata',
           'timeDimensions':[{'dimension':f"{r['cube_view']}.{r['date_axis']}",'dateRange':[start.isoformat(),end.isoformat()]}],
           'filters':[{'member':f"{r['cube_view']}.brand_id",'operator':'equals','values':[str(brand)]}]}
        url=f'{CUBE_URL}/cubejs-api/v1/load?query='+urllib.parse.quote(json.dumps(q))
        for _ in range(30):
            try: res=json.load(urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':tok}),timeout=120))
            except Exception: return None
            if res.get('error')=='Continue wait': time.sleep(1); continue
            data=res.get('data') or []
            v=data[0].get(r['cube_measure']) if data else None
            try: return round(float(v),6)
            except (TypeError, ValueError): return None
        return None
    todo=[r for r in mcp if r['cube_measure'] and r['date_axis']]
    with cf.ThreadPoolExecutor(6) as ex:
        return dict(zip((r['metric_id'] for r in todo), ex.map(run, todo)))


def probe_value_groups(mcp):
    """Query every metric live for one brand over the last full month; group identical values."""
    (start,end),=last_full_months(1)
    vals=probe_values(mcp, PROBE_BRAND, start, end)
    unit={r['metric_id']:r['unit'] for r in mcp}
    by_val=collections.defaultdict(list)
    for mid,v in vals.items():
        if v not in (None, 0.0): by_val[(unit[mid], v)].append(mid)
    out={}
    for (_,v),ids in by_val.items():
        if len(set(ids))>1:
            for i in ids: out[i]=(f"{v:,.2f}" if abs(v)>=100 else f"{v:.4f}")+" = "+', '.join(sorted(set(ids)))
    return out


def write_baseline(mcp, path, brands, months):
    """Phase-0 snapshot: every certified metric x brand x month, with the Cube member it came from."""
    cert=[r for r in mcp if r['status']=='certified']
    rows=[]
    for b in brands:
        for start,end in last_full_months(months):
            vals=probe_values(cert, b, start, end)
            for r in cert:
                rows.append({'metric_id':r['metric_id'],'brand_id':str(b),'start':start.isoformat(),'end':end.isoformat(),
                             'value':vals.get(r['metric_id']),'cube_measure':r['cube_measure'],'date_axis':r['date_axis'],
                             'unit':r['unit']})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({'generated_at':datetime.datetime.now().isoformat(timespec='seconds'),'cube_url':CUBE_URL,
               'timezone':'Asia/Kolkata','rows':rows}, open(path,'w'), indent=1)
    return len(rows)


def serve_depths():
    """serve view -> max depth of its serve -> serve read chain (0 = reads gold only)."""
    reads={}
    for f in glob.glob(SERVE+'/*/views/*.sql'):
        txt=open(f).read()
        m=re.search(r'CREATE\s+OR\s+REPLACE\s+VIEW\s+serve\.`?(\w+)`?', txt, re.I)
        if not m: continue
        body=re.sub(r'/\*.*?\*/', ' ', '\n'.join(l.split('--')[0] for l in txt.splitlines()), flags=re.S)
        reads[m.group(1)]=set(re.findall(r'serve\.`?(\w+)`?', body))-{m.group(1)}
    memo={}
    def depth(v, stack=()):
        if v in memo: return memo[v]
        if v in stack or v not in reads: return 0
        d=max((1+depth(x, stack+(v,)) for x in reads[v] if x in reads), default=0)
        memo[v]=d; return d
    return {v:depth(v) for v in reads}


GATES = [  # (name, integrity-check substring or callable over the run, max allowed)
    ('resolution conflicts',              lambda run: sum(r['verdict']=='CONFLICT' for r in run['conflicts'])),
    ('same-value groups (certified ids)', lambda run: sum(1 for r in run['mcp'] if r['status']=='certified' and r['same_value_group'])),
    ('phantom supported_dimensions',      lambda run: sum(1 for i in run['integrity'] if i['check'].startswith('supported_dimensions not in view'))),
    ('serve views without repo SQL',      lambda run: sum(1 for i in run['integrity'] if i['check']=='live serve view has no SQL source file')),
    ('serve SQL not deployed',            lambda run: sum(1 for i in run['integrity'] if i['check']=='SQL file not deployed / not behind a Cube view')),
    ('cube sql_table not live',           lambda run: sum(1 for i in run['integrity'] if i['check'].startswith('cube sql_table'))),
    ('glossary targets missing',          lambda run: sum(1 for i in run['integrity'] if i['check']=='target metric does not exist')),
    ('agent registry drift',              lambda run: sum(1 for i in run['integrity'] if i['layer']=='agent registry')),
    ('serve view depth > 1',              lambda run: sum(1 for d in serve_depths().values() if d>1)),
]


def run_gates(run):
    """Print each gate; return the number of failing gates."""
    failed=0
    print('\ninventory gates:')
    for name, fn in GATES:
        n=fn(run); ok=n==0; failed+=not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name:36s} {n}")
    return failed


def integrity_issues(lex, mcp):
    """Cross-layer checks: glossary / registry / cube / serve, one row per defect."""
    out=[]; add=lambda layer,check,item,detail='': out.append({'layer':layer,'check':check,'item':item,'detail':detail})
    mets={r['metric_id'] for r in mcp}
    seen=collections.defaultdict(set)
    for t in yaml.safe_load(open(f'{CORE}/catalogue/glossary/terms.yaml'))['terms']:
        cid=t.get('canonical_id'); seen[t['term'].strip().lower()].add(cid)
        if cid and cid not in mets: add('glossary','target metric does not exist',t['term'],cid)
        if not cid and not t.get('canonical_dimension_id'): add('glossary','term resolves to no metric (definition only)',t['term'],' '.join(str(t.get('definition','')).split())[:200])
    for term,ids in seen.items():
        if len(ids)>1: add('glossary','same term mapped to several ids',term,', '.join(sorted(map(str,ids))))
    gl={k:next(iter(v)) for k,v in seen.items() if len(v)==1}
    for r in lex['registry_rows']:
        cm=r.get('catalogue_metric')
        if cm not in mets: add('agent registry','catalogue_metric not in catalogue',r['id'],cm)
        for a in r.get('aliases') or []:
            g=gl.get(a.lower())
            if g and g!=cm: add('agent registry','alias disagrees with Core glossary',a,f'agent={cm} core={g}')
    cw=yaml.safe_load(open(f'{CORE}/catalogue/openmetadata/crosswalk.generated.yaml'))['views']
    live_serve={(v.get('serve_table') or '').split('.')[-1] for v in cw.values()}
    files={os.path.basename(f)[:-4] for f in glob.glob(SERVE+'/*/views/*.sql')}
    for s in sorted(live_serve - files - {''}): add('serve','live serve view has no SQL source file',f'serve.{s}','DDL not reproducible from repo')
    for s in sorted(files - live_serve - {'00_create_database'}): add('serve','SQL file not deployed / not behind a Cube view',f'serve.{s}')
    for f in sorted(glob.glob(f'{CUBE}/cubes/*.yml')):
        for c in yaml.safe_load(open(f))['cubes']:
            st=(c.get('sql_table') or '').split('.')[-1]
            if st and st not in live_serve: add('cube','cube sql_table not exposed by any view / not live',c['name'],c.get('sql_table'))
    for r in mcp:
        if r['issues']: add('catalogue',r['issues'].split(':')[0],r['metric_id'],r['issues'])
        if re.search(r'(same (number|total|value) as|identical to)', r['description'] or '', re.I):
            add('catalogue','description declares it equals another metric',r['metric_id'],r['description'][:220])
    return out


def extract_mcp_metrics(lex):
    import yaml, glob, json, re, os
    cw=yaml.safe_load(open(f'{CORE}/catalogue/openmetadata/crosswalk.generated.yaml'))
    views_cw=cw['views']; oms=yaml.safe_load(open(f'{CORE}/catalogue/openmetadata/metrics.yaml'))['metrics']
    vyaml={v['name']:v for v in yaml.safe_load(open(f'{CORE}/catalogue/views.yaml'))['views']}
    # cube members -> definition
    cubes={}
    for f in glob.glob(f'{CUBE}/cubes/*.yml'):
        for c in yaml.safe_load(open(f))['cubes']:
            cubes[c['name']]={'file':os.path.basename(f),'sql_table':c.get('sql_table'),'has_inline_sql':bool(c.get('sql')),
                'measures':{m['name']:m for m in c.get('measures') or []},'dimensions':{d['name']:d for d in c.get('dimensions') or []}}
    view_member={}   # (view, member) -> (cube, def, kind)
    for v in yaml.safe_load(open(f'{CUBE}/views/serve_views.yml'))['views']:
        for cc in v.get('cubes') or []:
            cn=cc['join_path'].split('.')[-1]; cu=cubes.get(cn)
            if not cu: continue
            inc=cc.get('includes','*')
            names=list(cu['measures'])+list(cu['dimensions']) if inc=='*' else [i if isinstance(i,str) else i['name'] for i in inc]
            for n in names:
                if n in cu['measures']: view_member[(v['name'],n)]=(cn,cu['measures'][n],'measure')
                elif n in cu['dimensions']: view_member[(v['name'],n)]=(cn,cu['dimensions'][n],'dimension')
    def select_item_exprs(body):
        """alias -> the select-item expression that defines it, walking back from each `AS alias`
        with paren balancing so multi-line, multi-arg expressions survive intact."""
        out={}
        for m in re.finditer(r'\bAS\s+([a-z_][a-z0-9_]{2,60})\b', body, re.I):
            alias=m.group(1); i=m.start(); depth=0; j=i-1
            while j >= 0:
                ch=body[j]
                if ch == ')': depth += 1
                elif ch == '(':
                    if depth == 0: break
                    depth -= 1
                elif depth == 0 and ch == ',': break
                elif depth == 0 and body[max(0,j-6):j+1].upper().endswith('SELECT'): break
                j -= 1
            expr=' '.join(body[j+1:i].split())
            expr=re.sub(r'^(SELECT|select)\s+','',expr).strip()
            if 3 < len(expr) < 400: out.setdefault(alias, expr)
        return out

    def derivation(mdef, cat):
        """How the number is produced, from the Cube measure definition + catalogue."""
        if mdef is None: return 'unknown', ''
        sql=str(mdef.get('sql','')).strip(); typ=mdef.get('type','')
        filt=mdef.get('filters')
        agg=(cat.get('aggregation') or '').lower()
        if typ=='number' or 'ratio' in agg or re.search(r'\bsum\(|nullIf', sql) and '/' in sql:
            return 'derived — ratio of aggregates', sql
        if re.search(r'^[a-z_][a-z0-9_]*$', sql):
            base=f'{typ}({sql})'
            return ('base aggregate (filtered)' if filt else 'base aggregate'), base
        if re.search(r'CASE|if\(|coalesce|\+|\-|\*|/', sql):
            return ('derived — computed expression (filtered)' if filt else 'derived — computed expression'), sql
        if typ in ('sum','count','count_distinct','min','max','avg'):
            return ('base aggregate (filtered)' if filt else 'base aggregate'), f'{typ}({sql})' if sql else typ
        return f'{typ}', sql
    SERVE_SQL={}
    for f in glob.glob(SERVE + '/*/views/*.sql'):
        txt=open(f).read()
        m=re.search(r'CREATE\s+OR\s+REPLACE\s+VIEW\s+serve\.`?(\w+)`?', txt, re.I)
        if not m: continue
        body='\n'.join(l for l in txt.splitlines() if not l.lstrip().startswith('--'))
        golds=sorted(set(re.findall(r'gold\.`?(\w+)`?', body))); serves=sorted(set(re.findall(r'serve\.`?(\w+)`?', body))-{m.group(1)})
        shape=[]
        if re.search(r'\bUNION\b', body, re.I): shape.append('UNION of arms')
        if re.search(r'\bWITH\b', body, re.I): shape.append('CTE composition')
        if re.search(r'\bGROUP BY\b', body, re.I): shape.append('aggregated rollup')
        if re.search(r'\bFINAL\b', body): shape.append('FINAL dedupe')
        if not shape: shape.append('pass-through projection')
        SERVE_SQL[m.group(1)]={'shape':'; '.join(shape),'reads':', '.join(['gold.'+g for g in golds]+['serve.'+x for x in serves]),
                               'file':f.replace(SERVE + '/', 'serve/'),'gold':golds,'serve':serves}
        SERVE_SQL[m.group(1)]['columns']=select_item_exprs(body)
    def lineage(st, seen=None):
        """(serve chain, gold leaves) for a serve view, following serve -> serve reads."""
        seen=set() if seen is None else seen
        if st in seen or st not in SERVE_SQL: return [], set()
        seen.add(st); chain=[st]; golds=set(SERVE_SQL[st]['gold'])
        for s in SERVE_SQL[st]['serve']:
            c,g=lineage(s, seen); chain+=c; golds|=g
        return chain, golds
    rows=[]
    for f in sorted(glob.glob(f'{CORE}/catalogue/metrics/*.yaml')):
        d=yaml.safe_load(open(f)); mid=d['id']; cm=d.get('cube_mapping') or {}
        view=cm.get('view'); measure=(cm.get('measure') or '')
        member=measure.split('.',1)[1] if '.' in measure else ''
        cube_name, mdef, kind = view_member.get((view,member),(None,None,None))
        kindtxt, cube_sql = derivation(mdef, d)
        st=(views_cw.get(view,{}) or {}).get('serve_table','').split('.')[-1]
        scols=(SERVE_SQL.get(st,{}) or {}).get('columns',{})
        base_col=re.sub(r'^\{CUBE\}\.','',str((mdef or {}).get('sql','')).strip()) if mdef else ''
        serve_expr=scols.get(base_col,'')
        if serve_expr and kindtxt.startswith('base aggregate') and re.search(r'[-+*/]|CASE|if\(|sumIf|coalesce', serve_expr, re.I):
            kindtxt = kindtxt.replace('base aggregate','base aggregate over a derived serve column')
        rc=d.get('ratio_components') or {}
        dep=(d.get('formula') or {}).get('depends_on') or []
        vcw=views_cw.get(view,{}) ; om=oms.get(mid,{})
        chain, golds = lineage(st)
        issues=[]
        if mdef is None: issues.append('cube measure not found in view')
        if st and st not in SERVE_SQL: issues.append(f'serve view has no repo SQL file: serve.{st}')
        bad_dims=[x for x in d.get('supported_dimensions') or []
                  if (view,x) not in view_member and x not in ('date_range',)]
        if bad_dims: issues.append('supported_dimensions not in view: '+', '.join(bad_dims))
        if re.search(r'BROKEN UPSTREAM', str((mdef or {}).get('description',''))): issues.append('cube description flags broken upstream')
        rows.append({
          'metric_id':mid,'display_name':d.get('display_name'),
          'domain':vcw.get('domain') or om.get('domain') or '','category':d.get('category'),
          'data_product':vcw.get('data_product') or '','status':d.get('status'),
          'unit':d.get('unit'),'currency':d.get('currency_default'),'aggregation':d.get('aggregation'),
          'grain':d.get('grain'),
          'formula_human_readable':(d.get('formula') or {}).get('human_readable'),
          'derivation':kindtxt,
          'cube_expression':cube_sql,
          'serve_column_expression':serve_expr,
          'depends_on_metrics':', '.join(dep),
          'ratio_numerator':rc.get('numerator',''),'ratio_denominator':rc.get('denominator',''),
          'cube_view':view,'cube_measure':measure,'cube_cube':cube_name or '',
          'serve_table':(vcw.get('serve_table') or '').replace('clickhouse.default.',''),
          'gold_inputs':', '.join(g.replace('clickhouse.default.','') for g in (vcw.get('gold_inputs') or [])),
          'serve_view_shape':(SERVE_SQL.get((vcw.get('serve_table') or '').split('.')[-1],{}) or {}).get('shape',''),
          'serve_view_reads':(SERVE_SQL.get((vcw.get('serve_table') or '').split('.')[-1],{}) or {}).get('reads',''),
          'serve_view_sql_file':(SERVE_SQL.get((vcw.get('serve_table') or '').split('.')[-1],{}) or {}).get('file',''),
          'serve_chain':' → '.join('serve.'+s for s in chain),
          'gold_lineage':', '.join('gold.'+g for g in sorted(golds)),
          'glossary_terms':', '.join(lex['glossary'].get(mid,[])),
          'concept_routes':', '.join(lex['concepts'].get(mid,[])),
          'agent_registry_ids':', '.join(lex['registry'].get(mid,[])),
          'same_value_group':'',
          'issues':'; '.join(issues),
          'date_axis':vcw.get('date_dimension') or (vyaml.get(view,{}) or {}).get('date_dimension') or '',
          'contract':vcw.get('contract') or '',
          'om_metric_name':om.get('om_name',''),
          'dimensions_supported':len(d.get('supported_dimensions') or []),
          'dashboard_alignment':(d.get('dashboard_alignment') or {}).get('status',''),
          'dashboard_evidence':(d.get('dashboard_alignment') or {}).get('evidence',''),
          'validation_tests':' | '.join(d.get('validation_tests') or []),
          'description':' '.join((d.get('description') or '').split()),
          'source_file':f.replace(CORE+'/',''),
        })
    return rows


def extract_cube_and_serve():
    import yaml, glob, json, re, os, urllib.request
    cw=yaml.safe_load(open(f'{CORE}/catalogue/openmetadata/crosswalk.generated.yaml')); views_cw=cw['views']
    catalogued=set()
    for f in glob.glob(f'{CORE}/catalogue/metrics/*.yaml'):
        d=yaml.safe_load(open(f)); cm=d.get('cube_mapping') or {}
        for k in ('measure','measure_pct'):
            if cm.get(k): catalogued.add(cm[k])
        rc=d.get('ratio_components') or {}
        for k in ('numerator','denominator'):
            if rc.get(k): catalogued.add(rc[k])
    cubes={}
    for f in glob.glob(f'{CUBE}/cubes/*.yml'):
        for c in yaml.safe_load(open(f))['cubes']:
            cubes[c['name']]={'measures':{m['name']:m for m in c.get('measures') or []},
                              'dimensions':{d['name']:d for d in c.get('dimensions') or []}}
    extra=[]; serve_rows=[]
    for v in yaml.safe_load(open(f'{CUBE}/views/serve_views.yml'))['views']:
        vn=v['name']; vcw=views_cw.get(vn,{}); ms=[]; ds=[]
        for cc in v.get('cubes') or []:
            cn=cc['join_path'].split('.')[-1]; cu=cubes.get(cn)
            if not cu: continue
            inc=cc.get('includes','*')
            names=list(cu['measures'])+list(cu['dimensions']) if inc=='*' else [i if isinstance(i,str) else i['name'] for i in inc]
            for n in names:
                q=f'{vn}.{n}'
                if n in cu['measures']:
                    ms.append(n)
                    if q not in catalogued:
                        md=cu['measures'][n]
                        extra.append({'cube_view':vn,'member':n,'qualified':q,'type':md.get('type'),
                            'expression':str(md.get('sql','')).strip(),'title':md.get('title',''),
                            'domain':vcw.get('domain',''),'data_product':vcw.get('data_product',''),
                            'description':' '.join(str(md.get('description','')).split())[:400]})
                elif n in cu['dimensions']: ds.append(n)
        serve_rows.append({'cube_view':vn,'title':v.get('title',''),'domain':vcw.get('domain',''),
            'data_product':vcw.get('data_product',''),'serve_table':(vcw.get('serve_table') or '').replace('clickhouse.default.',''),
            'measures':len(ms),'dimensions':len(ds),'date_axis':vcw.get('date_dimension',''),
            'contract':vcw.get('contract',''),'grain_key':', '.join(vcw.get('grain_key') or []),
            'gold_inputs':', '.join(g.replace('clickhouse.default.','') for g in (vcw.get('gold_inputs') or [])),
            'measure_list':', '.join(sorted(ms)),'description':' '.join(str(v.get('description','')).split())[:500]})
    return extra, serve_rows


def extract_node_metrics():
    import re, json, os, collections
    ROOT=NODE_ROOT
    DOMAIN=[  # (path fragment, domain, surface)
     ('integrations/historicalAnalytics','Commerce / Finance','Historical Analytics dashboard'),
     ('integrations/shopify','Commerce (Shopify platform API)','Shopify analytics'),
     ('integrations/meta/','PaidMedia (Meta)','Meta ads analytics'),
     ('integrations/google/','PaidMedia (Google)','Google ads analytics'),
     ('integrations/metaAttribution','Attribution (Meta)','Meta Attribution'),
     ('integrations/googleAttribution','Attribution (Google)','Google Attribution'),
     ('integrations/organicAttribution','Attribution (Organic)','Organic Attribution'),
     ('integrations/attributionPnLHelpers','Attribution P&L','Attribution channel P&L'),
     ('integrations/attribution','Attribution','Attribution helpers'),
     ('integrations/adChannelPnlGold','Attribution P&L','Ad channel P&L'),
     ('integrations/ga4','WebAnalytics (GA4)','GA4'),
     ('integrations/channelFunnel','WebAnalytics / Funnel','Channel funnel'),
     ('integrations/metaFunnel','WebAnalytics / Funnel','Meta funnel'),
     ('integrations/customerJourney','Customer','Customer journey'),
     ('integrations/productAnalytics','Product','Product analytics'),
     ('integrations/productSpend','Product','Product spend / COGS'),
     ('integrations/performanceSummary','Cross-domain summary','Performance summary'),
     ('integrations/liveDashboard','Cross-domain live','Live dashboard (platform APIs)'),
     ('integrations/gstr1','Tax / GST','GSTR-1'),
     ('integrations/eshopbox','Operations (3PL)','Eshopbox'),
     ('integrations/analyst','Analyst','Analyst tooling'),
     ('integrations/internalDb','Internal','Internal DB'),
     ('integrations/shared','Shared','Shared helpers'),
     ('services/pnl','Finance (P&L)','P&L service'),
     ('services/cogsAnalysis','Finance (COGS)','COGS analysis'),
     ('services/orderCogs','Finance (COGS)','Order COGS'),
     ('services/inventory','Operations (Inventory)','Inventory'),
     ('services/receiving','Operations (Inventory)','Receiving / inventory sync'),
     ('services/shipping','Operations (Shipping)','Shipping analysis'),
     ('services/categoryIntelligence','Product','Category intelligence'),
     ('services/dashboardService','Cross-domain summary','Dashboard summary API'),
     ('integrations/amazon','Marketplace (retired connector)','not served to the MCP'),
     ('controllers/gstr1Controller','Tax / GST','GSTR-1 API'),
    ]
    RETIRED=re.compile(r'amazon', re.I)
    SKIP=re.compile(r'^(models/|routes/|middleware/|validators/|schemas/|iam/|events/|jobs/|types/|config/|utils/tokenRefresh)|services/(auth|notification|personalization|platformAccess|platformUser|platformInvite|company|user|password|permission|navigation|job|sku|purchaseRequest|procurement|productLaunch|dataExport|industry|category(?!Intelligence))')
    METRICISH=re.compile(r'sales|profit|cogs|spend|order|roas|rate|margin|aov|cac|ltv|mer|tax|revenue|discount|refund|cancel|return|unit|count|value|ratio|pct|percent|fee|cost|qty|quantity|stock|inventory|session|click|impression|conversion|view|customer|payout|settle|gst|shipping|delivery|rto|basket|frequency|retention|churn|ticket', re.I)
    AGG=re.compile(r'\b(sum|sumIf|count|countIf|uniqExact|uniqExactIf|uniq|avg|avgIf|min|max|any|anyLast|argMax|median|quantile)\s*\(', re.I)
    def domain_for(path):
        for frag,dom,surf in DOMAIN:
            if frag in path: return dom,surf
        return 'Other','—'
    rows=[]
    for dirpath,_,files in os.walk(ROOT):
        for fn in files:
            if not fn.endswith('.js'): continue
            p=os.path.join(dirpath,fn); rel=p.replace(ROOT+'/','')
            if SKIP.search(rel): continue
            try: txt=open(p, encoding='utf-8', errors='replace').read()
            except Exception: continue
            # JSDoc/line comments explain metrics but do not define them — drop them
            # before scanning, or prose is mistaken for SQL.
            txt=re.sub(r'/\*.*?\*/', ' ', txt, flags=re.S)
            txt=re.sub(r'(?m)^\s*//.*$', ' ', txt)
            # Platform-API metric fields (fetched, not computed): Meta Insights + GA4
            if 'integrations/meta/' in rel:
                for m in re.finditer(r"fields:\s*'([^']*impressions[^']*)'", txt):
                    for fld in [x.strip() for x in m.group(1).split(',')]:
                        if METRICISH.search(fld) or fld in ('reach','ctr','cpc','cpm','actions','action_values'):
                            rows.append({'metric':fld,'domain':'PaidMedia (Meta)','surface':'Meta Insights API',
                                'module':rel,'formula_sql':'fetched field from Meta Insights API (no local formula)',
                                'derivation':'source field (platform API)','served_to_mcp':''})
            if 'integrations/ga4/' in rel:
                blocks=re.findall(r'metrics:\s*\[(.*?)\]', txt, re.S)
                for blk in blocks:
                  for m in re.finditer(r"name:\s*'([A-Za-z][A-Za-z0-9_]{2,40})'", blk):
                    fld=m.group(1)
                    rows.append({'metric':fld,'domain':'WebAnalytics (GA4)','surface':'GA4 Data API',
                        'module':rel,'formula_sql':'fetched metric from the GA4 Data API (no local formula)',
                        'derivation':'source field (platform API)','served_to_mcp':''})

            if not re.search(r'\b(SELECT|sumIf|toFloat64)\b', txt): continue
            dom,surf=domain_for(rel)
            # SQL aliases: <expr> AS alias   (expr = tail of the preceding expression on that line/lines)
            for m in re.finditer(r'([^\n,]{0,400}?)\s+AS\s+([a-z_][a-z0-9_]{2,60})\b', txt, re.I):
                expr=' '.join(m.group(1).split()); alias=m.group(2)
                if alias.lower() in ('t','a','b','c','d','x','y','sub','tmp','src','o','oi','r','p','e','po','od','od2'): continue
                if not AGG.search(expr) and not re.search(r'[-+*/]|CASE|if\(|coalesce', expr, re.I): continue
                if len(expr) < 4: continue
                ATTR=re.compile(r'(^|_)(name|title|sku|id|ids|status|type|code|label|desc|description|email|phone|address|city|state|country|pincode|url|source|medium|campaign|currency|brand|vendor|category|image|note|tag|tags)($|_)|_at$|_date$|_ts$|^last_|^first_', re.I)
                if ATTR.search(alias) and re.match(r'^(max|min|any|anyLast|argMax|argMin)\s*\(', expr, re.I): continue
                kind = 'derived — ratio/expression' if (('/' in expr and AGG.search(expr)) or re.search(r'CASE|if\(', expr, re.I) or re.search(r'[-+*]', expr)) else 'base aggregate'
                if AGG.search(expr) and not re.search(r'[-+*/]|CASE|if\(', expr, re.I): kind='base aggregate'
                rows.append({'metric':alias,'domain':dom,'surface':surf,'module':rel,
                             'formula_sql':expr[:380],'derivation':kind,
                             'served_to_mcp':'no — retired connector' if RETIRED.search(rel) or RETIRED.search(expr) or RETIRED.search(alias) else ''})
            # JS-computed KPIs: const netProfit = grossProfit - totalAdSpend
            for m in re.finditer(r'(?:const|let)\s+([A-Za-z_]\w{2,40})\s*=\s*([^;\n]{4,300});', txt):
                name, expr = m.group(1), ' '.join(m.group(2).split())
                if not METRICISH.search(name): continue
                if not (re.search(r'[A-Za-z_]\w*\s*[-+*/]\s*[A-Za-z_(]', expr) or re.search(r'safeDiv\(|round2\(', expr)): continue
                if re.search(r'\b(await|require|new |function|=>|\?\.|\[\]|\{\})', expr): continue
                rows.append({'metric':name,'domain':dom,'surface':surf,'module':rel,
                             'formula_sql':expr[:380],'derivation':'derived — computed in JS (dashboard layer)',
                             'served_to_mcp':'no — retired connector' if RETIRED.search(rel) or RETIRED.search(expr) or RETIRED.search(name) else ''})
            # API payload fields: net_sales: round2(...) / parseFloat(...)
            for m in re.finditer(r'\n\s*([a-z_][a-z0-9_]{2,40})\s*:\s*(round2\([^\n]{0,160}|parseFloat\([^\n]{0,160}|safeDiv\([^\n]{0,160})', txt):
                name, expr = m.group(1), ' '.join(m.group(2).split())
                if not METRICISH.search(name): continue
                rows.append({'metric':name,'domain':dom,'surface':surf,'module':rel,
                             'formula_sql':expr.rstrip(',')[:380],'derivation':'API response field (dashboard layer)',
                             'served_to_mcp':'no — retired connector' if RETIRED.search(rel) or RETIRED.search(expr) or RETIRED.search(name) else ''})


    # dedupe: keep the longest expression per (module, metric)
    best={}
    for r in rows:
        k=(r['module'],r['metric'])
        if k not in best or len(r['formula_sql'])>len(best[k]['formula_sql']): best[k]=r
    CANON=[('Marketplace','Marketplace (retired connector)'),('Attribution','Attribution'),('PaidMedia','PaidMedia'),
           ('WebAnalytics','WebAnalytics'),('Commerce','Commerce'),('Finance','Finance'),('Customer','Customer'),
           ('Product','Product'),('Operations','Operations'),('Tax','Tax / GST'),('Cross','Cross-domain')]
    for r in best.values():
        d=r['domain']; r['canonical_domain']=next((c for k,c in CANON if d.startswith(k)), 'Commerce / Finance' if d.startswith('Commerce /') else 'Other')
    # Only connected platforms are inventoried: a retired connector is not fetchable
    # and naming it here would imply it still is.
    rows=[r for r in best.values() if not r['served_to_mcp'] and not r['canonical_domain'].startswith('Marketplace')]
    rows=sorted(rows, key=lambda r:(r['canonical_domain'],r['domain'],r['module'],r['metric']))
    return rows


def build_workbook(mcp, extra, serve, node, conflicts, integrity, probe_note):
    from openpyxl import Workbook  # noqa: E402
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    HDR=PatternFill('solid', fgColor='1F3864'); HF=Font(color='FFFFFF', bold=True, size=10)
    TITLE=Font(bold=True, size=14); SUB=Font(italic=True, color='555555')
    thin=Side(style='thin', color='D9D9D9'); BORD=Border(bottom=thin)
    wb=Workbook(); wb.remove(wb.active)
    def sheet(name, rows, cols, widths, wrap=(), freeze='A2'):
        ws=wb.create_sheet(name)
        ws.append([c[1] for c in cols])
        for i,c in enumerate(cols,1):
            cell=ws.cell(row=1,column=i); cell.fill=HDR; cell.font=HF
            cell.alignment=Alignment(vertical='center', wrap_text=True)
            ws.column_dimensions[get_column_letter(i)].width=widths[i-1]
        ws.row_dimensions[1].height=30
        for r in rows:
            ws.append([r.get(c[0],'') for c in cols])
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            for cell in row:
                cell.alignment=Alignment(vertical='top', wrap_text=(cell.column_letter in wrap))
                cell.border=BORD
                cell.font=Font(size=10)
        ws.freeze_panes=freeze
        ws.auto_filter.ref=f'A1:{get_column_letter(len(cols))}{ws.max_row}'
        return ws
    # ---------- README ----------
    ws=wb.create_sheet('README')
    ws.column_dimensions['A'].width=26; ws.column_dimensions['B'].width=120
    ws['A1']='Metric inventory — Seleric'; ws['A1'].font=TITLE
    rowsr=[
     ('Generated', datetime.date.today().isoformat()),
     ('Scope','Every metric that can be fetched or calculated, across the agent (MCP/Cube/serve) surface and the Node backend, with its formula, how it is derived, and its domain.'),
     ('',''),
     ('Sheet: MCP Metrics',f'The {len(mcp)} approved catalogue ids the MCP can query by name (metrics_query). These are the only ids the agent may use. Includes the catalogue formula, the Cube expression that executes, the serve view chain it reads, the gold tables at the bottom of that chain, every word that routes to it (glossary, concept layer, agent registry) and any defects.'),
     ('Sheet: Cube-only Measures',f'{len(extra)} further measures that exist in the Cube semantic layer and can be computed there, but have NO catalogue id — so the MCP will reject them until a metric file is added. Useful as the backlog of "already modelled, not yet agent-queryable".'),
     ('Sheet: Serve Views',f'The {len(serve)} certified ClickHouse serve views behind those measures: physical table, grain, date axis, contract, gold inputs and how the view itself is built.'),
     ('Sheet: Node Backend',f'{len(node)} metric definitions found in the Node backend (dashboard/API layer, a separate system from the MCP): SQL aliases, JS-computed KPIs, API response fields and platform-API source fields.'),
     ('Sheet: Resolution Conflicts',f'{sum(r["verdict"]=="CONFLICT" for r in conflicts)} of {len(conflicts)} business phrases resolve to different metric ids depending on which resolver handles them (resolve_term, resolve_concept, search, Seleric_Agent metric_registry). Each conflict is a question the agent can answer with a different number or a different lineage.'),
     ('Sheet: Integrity Issues',f'{len(integrity)} cross-layer defects: glossary targets, agent-registry drift, serve views with no source, cube tables not live, catalogue dimensions the view lacks, metrics that declare they equal another.'),
     ('Same value group',probe_note),
     ('Sheet: Domain Summary','Counts per domain for each surface.'),
     ('',''),
     ('Derivation legend',''),
     ('  source field (platform API)','Fetched as-is from a vendor API (Meta Insights, GA4). No local formula.'),
     ('  base aggregate','A single aggregate over one column, e.g. sum(net_revenue_excl_tax). Additive.'),
     ('  base aggregate (filtered)','Same, restricted by a Cube measure filter (e.g. only COD orders).'),
     ('  derived — computed expression','Arithmetic/CASE over columns inside the aggregate, e.g. net_sales − net_cogs − ad_spend.'),
     ('  derived — ratio of aggregates','A ratio computed as sum(numerator)/sum(denominator) — never an average of daily ratios.'),
     ('  derived — computed in JS (dashboard layer)','Node backend computes it in JavaScript after querying (dashboard cards).'),
     ('  API response field (dashboard layer)','The field name the Node API returns; formula column shows how it is filled.'),
     ('',''),
     ('Caveats',''),
     ('  Two systems','The MCP/Cube surface and the Node backend are independent implementations over the same gold tables. Where both define a metric, the numbers can differ; the catalogue records that per metric in dashboard_alignment.'),
     ('  Connected platforms','Shopify commerce plus Meta and Google ads. Connectors that are no longer served are excluded from every sheet, so nothing here implies a channel that cannot be queried.'),
     ('  Node extraction','Pattern-based over the backend source, so it is a close inventory rather than a guaranteed-complete one; each row cites its module so it can be verified.'),
     ('Sheet: Coverage Gaps','Node backend metrics with no same-named MCP catalogue id — the agent cannot answer these today.'),
     ('Regenerate','uv run --with openpyxl python scripts/build_metric_inventory.py'),
    ]
    for i,(a,b) in enumerate(rowsr, start=3):
        ws.cell(row=i,column=1,value=a).font=Font(bold=not a.startswith('  ') and bool(a), size=10)
        c=ws.cell(row=i,column=2,value=b); c.alignment=Alignment(wrap_text=True, vertical='top'); c.font=Font(size=10)
    # ---------- MCP ----------
    sheet('MCP Metrics',mcp,[('metric_id','Metric id (use this in metrics_query)'),('display_name','Display name'),
     ('domain','Domain'),('category','Category'),('data_product','Data product'),('status','Status'),
     ('derivation','Derivation'),('formula_human_readable','Formula (catalogue)'),('cube_expression','Cube expression (executed)'),
     ('serve_column_expression','Serve column expression (upstream of the Cube measure)'),
     ('depends_on_metrics','Depends on metrics'),('ratio_numerator','Ratio numerator'),('ratio_denominator','Ratio denominator'),
     ('unit','Unit'),('currency','Currency'),('aggregation','Aggregation'),('grain','Grain'),('date_axis','Date axis'),
     ('cube_view','Cube view'),('cube_measure','Cube measure'),('serve_table','Serve table'),
     ('serve_view_shape','How the serve view is built'),('serve_view_reads','Serve view reads'),('gold_inputs','Gold inputs'),
     ('contract','Contract'),('dimensions_supported','# dims'),('dashboard_alignment','Dashboard alignment'),
     ('validation_tests','Validation tests'),('description','Description'),('source_file','Catalogue file'),
     ('serve_chain','Serve view chain (top → down)'),('gold_lineage','Gold lineage (all leaves)'),
     ('glossary_terms','Glossary terms → this id'),('concept_routes','Concept routes → this id'),
     ('agent_registry_ids','Seleric_Agent registry ids'),('same_value_group','Same value as (live probe)'),('issues','Issues')],
     [30,34,13,13,22,13,34,46,52,60,26,28,28,10,9,14,26,14,24,30,26,30,40,44,26,7,16,50,70,34,44,50,40,40,26,50,50],
     wrap=('H','I','J','Q','V','W','X','AB','AC','AE','AF','AG','AH','AJ','AK'))
    # ---------- Resolution conflicts ----------
    sheet('Resolution Conflicts',conflicts,[('phrase','Business phrase'),('verdict','Verdict'),
     ('resolve_term','catalogue_resolve_term'),('term_kind','term result kind'),
     ('resolve_concept','catalogue_resolve_concept'),('concept_kind','concept result kind'),
     ('search_top1','catalogue_search_metrics (top hit)'),('agent_registry','Seleric_Agent metric_registry alias')],
     [28,13,34,18,34,22,34,34])
    # ---------- Integrity issues ----------
    sheet('Integrity Issues',integrity,[('layer','Layer'),('check','Check'),('item','Item'),('detail','Detail')],
     [16,46,40,90], wrap=('D',))
    # ---------- Cube-only ----------
    sheet('Cube-only Measures',extra,[('qualified','Cube member (view.measure)'),('cube_view','Cube view'),('member','Measure'),
     ('title','Title'),('domain','Domain'),('data_product','Data product'),('type','Cube type'),('expression','Expression'),
     ('description','Description')],[40,24,28,34,13,22,12,54,70], wrap=('H','I'))
    # ---------- Serve views ----------
    sheet('Serve Views',serve,[('cube_view','Cube view'),('title','Title'),('domain','Domain'),('data_product','Data product'),
     ('serve_table','Serve table'),('grain_key','Grain key'),('date_axis','Date axis'),('contract','Contract'),
     ('measures','# measures'),('dimensions','# dims'),('gold_inputs','Gold inputs'),('measure_list','Measures'),
     ('description','Description')],[26,34,13,22,30,26,14,28,11,9,50,70,70], wrap=('K','L','M'))
    # ---------- Node ----------
    sheet('Node Backend',node,[('metric','Metric / field'),('canonical_domain','Domain'),
     ('domain','Domain detail / platform'),('surface','Surface'),
     ('derivation','Derivation'),('formula_sql','Formula / expression'),('module','Module (source of truth)'),
     ('served_to_mcp','MCP availability')],[34,22,30,30,38,70,48,24], wrap=('F',))
    # ---------- Coverage gaps: in the backend, not queryable by the agent ----------
    def norm(n):
        s=re.sub(r'(?<!^)(?=[A-Z])','_',n).lower()
        return re.sub(r'[^a-z0-9]+','_',s).strip('_')
    mcp_names={norm(r['metric_id']) for r in mcp} | {norm(r['display_name'] or '') for r in mcp}
    seen=set(); gaps=[]
    for r in sorted(node, key=lambda r:(r.get('canonical_domain',''), r['metric'])):
        n=norm(r['metric'])
        if n in mcp_names or n in seen: continue
        seen.add(n)
        gaps.append({'metric':r['metric'],'normalised':n,'canonical_domain':r.get('canonical_domain',''),
                     'domain':r['domain'],'surface':r['surface'],'derivation':r['derivation'],
                     'formula_sql':r['formula_sql'],'module':r['module'],'served_to_mcp':r['served_to_mcp']})
    sheet('Coverage Gaps',gaps,[('metric','Backend metric / field'),('canonical_domain','Domain'),
     ('domain','Domain detail / platform'),('surface','Surface'),('derivation','Derivation'),
     ('formula_sql','Formula / expression'),('module','Module'),('served_to_mcp','Note')],
     [34,22,30,30,38,70,48,24], wrap=('F',))

    # ---------- Domain summary ----------
    dm=collections.Counter(r['domain'] or '(none)' for r in mcp)
    dc=collections.Counter(r['domain'] or '(none)' for r in extra)
    dn=collections.Counter(r.get('canonical_domain') or r['domain'] for r in node)
    allk=sorted(set(dm)|set(dc)|set(dn))
    rows=[{'domain':k,'mcp':dm.get(k,0),'cube':dc.get(k,0),'node':dn.get(k,0)} for k in allk]
    rows.append({'domain':'TOTAL','mcp':len(mcp),'cube':len(extra),'node':len(node)})
    ws=sheet('Domain Summary',rows,[('domain','Domain'),('mcp','MCP metrics (queryable)'),
     ('cube','Cube-only measures'),('node','Node backend definitions')],[40,22,22,26])
    for c in ws[ws.max_row]: c.font=Font(bold=True, size=10)
    wb.save(OUT)
    return OUT, wb.sheetnames


def main(argv=None) -> int:
    import argparse
    ap=argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--check', action='store_true', help='evaluate the inventory gates; exit 1 if any fails')
    ap.add_argument('--warn-only', action='store_true', help='with --check: report failures but exit 0 (CI report mode)')
    ap.add_argument('--baseline', metavar='PATH', help='write a live value snapshot (certified metrics x brands x months) and exit')
    ap.add_argument('--brands', default='20,28', help='brands for --baseline (default 20,28)')
    ap.add_argument('--months', type=int, default=3, help='full months for --baseline (default 3)')
    args=ap.parse_args(argv)
    lex = resolution_lexicon()
    mcp = extract_mcp_metrics(lex)
    if args.baseline:
        n = write_baseline(mcp, args.baseline, args.brands.split(','), args.months)
        print(f"wrote {args.baseline} ({n} rows)")
        return 0
    extra, serve = extract_cube_and_serve()
    node = extract_node_metrics()
    probe_note = 'Live probe disabled (SELERIC_INVENTORY_LIVE=0).'
    if PROBE_LIVE:
        groups = probe_value_groups(mcp)
        for r in mcp: r['same_value_group'] = groups.get(r['metric_id'], '')
        probe_note = (f'Each metric queried live for brand {PROBE_BRAND} over the last full month; ids returning the '
                      'same non-zero number (same unit) are grouped. A group is usually one business number with '
                      'several ids and several lineages — candidates to collapse to one canonical id.')
    conflicts = resolution_conflicts(lex)
    integrity = integrity_issues(lex, mcp)
    path, sheets = build_workbook(mcp, extra, serve, node, conflicts, integrity, probe_note)
    print(f"wrote {path}")
    print(f"  MCP metrics (queryable) : {len(mcp)}")
    print(f"  Cube-only measures      : {len(extra)}")
    print(f"  Serve views             : {len(serve)}")
    print(f"  Node backend definitions: {len(node)}")
    print(f"  Resolution conflicts    : {sum(r['verdict']=='CONFLICT' for r in conflicts)} / {len(conflicts)} phrases")
    print(f"  Integrity issues        : {len(integrity)}")
    print(f"  Same-value groups       : {sum(1 for r in mcp if r['same_value_group'])} metrics")
    print(f"  sheets: {', '.join(sheets)}")
    if args.check:
        failed = run_gates({'mcp':mcp,'conflicts':conflicts,'integrity':integrity})
        return 0 if (failed == 0 or args.warn_only) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
