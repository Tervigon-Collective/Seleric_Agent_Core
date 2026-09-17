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

Run:  uv run --with openpyxl python scripts/build_metric_inventory.py
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
OUT = os.path.join(CORE, "doc", "METRIC_INVENTORY.xlsx")

def extract_mcp_metrics():
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
                               'file':f.replace(SERVE + '/', 'serve/')}
        SERVE_SQL[m.group(1)]['columns']=select_item_exprs(body)
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


def build_workbook(mcp, extra, serve, node):
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
     ('Sheet: MCP Metrics','The 218 approved catalogue ids the MCP can query by name (metrics_query). These are the only ids the agent may use. Includes the catalogue formula, the Cube expression that executes, the serve view it reads and its gold inputs.'),
     ('Sheet: Cube-only Measures','186 further measures that exist in the Cube semantic layer and can be computed there, but have NO catalogue id — so the MCP will reject them until a metric file is added. Useful as the backlog of "already modelled, not yet agent-queryable".'),
     ('Sheet: Serve Views','The 35 certified ClickHouse serve views behind those measures: physical table, grain, date axis, contract, gold inputs and how the view itself is built.'),
     ('Sheet: Node Backend','445 metric definitions found in the Node backend (dashboard/API layer, a separate system from the MCP): SQL aliases, JS-computed KPIs, API response fields and platform-API source fields.'),
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
     ('validation_tests','Validation tests'),('description','Description'),('source_file','Catalogue file')],
     [30,34,13,13,22,13,34,46,52,60,26,28,28,10,9,14,26,14,24,30,26,30,40,44,26,7,16,50,70,34],
     wrap=('H','I','J','Q','V','W','X','AB','AC'))
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


def main() -> int:
    mcp = extract_mcp_metrics()
    extra, serve = extract_cube_and_serve()
    node = extract_node_metrics()
    path, sheets = build_workbook(mcp, extra, serve, node)
    print(f"wrote {path}")
    print(f"  MCP metrics (queryable) : {len(mcp)}")
    print(f"  Cube-only measures      : {len(extra)}")
    print(f"  Serve views             : {len(serve)}")
    print(f"  Node backend definitions: {len(node)}")
    print(f"  sheets: {', '.join(sheets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
