#!/usr/bin/env python3
"""Build and serve a local C2VSimFG input land-use explorer (Python standard library).

Build: python c2vsim_landuse_explorer_expanded.py build --input DIR --output DIR --start-year 2000
Serve: python c2vsim_landuse_explorer_expanded.py serve --output DIR
The input directory must contain Nodes, Elements, and the four *_Area files.
An optional ZBudget zone-definition DAT adds subbasin aggregation.
NonPondedCrop_Area may instead be in a ZIP archive. Input header version text is
ignored; the data and input SHA-256 hashes determine the snapshot.
"""
import argparse
import gzip
import hashlib
import json
import math
import re
import shutil
import sqlite3
import struct
import sys
import zipfile
from contextlib import contextmanager
from datetime import date, timedelta
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SOURCES = {
    'nonponded': ('NonPondedCrop_Area', ['GR','CO','SB','CN','DB','SA','FL','AL','PA','TP','TF','CU','OG','PO','TR','AP','OR','CS','VI','ID']),
    'ponded': ('PondedCrop_Area', ['RI_FL','RI_NFL','RI_NDC','RF_SL','RF_PR']),
    'native': ('NativeVeg_Area', ['NV','RV']),
    'urban': ('Urban_Area', ['U']),
}
LABELS = {'GR':'Grain','CO':'Cotton','SB':'Sugar beets','CN':'Corn','DB':'Dry beans','SA':'Safflower','FL':'Other field','AL':'Alfalfa','PA':'Pasture','TP':'Processing tomatoes','TF':'Fresh tomatoes','CU':'Cucurbits','OG':'Onions and garlic','PO':'Potatoes','TR':'Other truck','AP':'Almonds and pistachios','OR':'Other deciduous','CS':'Citrus and subtropical','VI':'Vineyards','ID':'Idle','RI_FL':'Rice: flooded decomposition','RI_NFL':'Rice: nonflooded decomposition','RI_NDC':'Rice: no decomposition','RF_SL':'Seasonal refuge','RF_PR':'Permanent refuge','NV':'Native vegetation','RV':'Riparian vegetation','U':'Urban'}
DATE_RE = re.compile(r'^(\d{2})/(\d{2})/(\d{4})_(\d{2}):(\d{2})$')

def datestamp(s):
    m = DATE_RE.match(s)
    if not m: raise ValueError(f'Invalid IWFM time: {s}')
    mm, dd, yy, hh, minute = map(int, m.groups())
    d = date(yy, mm, dd)
    if hh == 24 and minute == 0: d += timedelta(days=1)
    elif hh != 0 or minute != 0: raise ValueError(f'Unsupported daily time: {s}')
    return d.isoformat()

def period_end_for_day(dates, day):
    """Find the block covering a calendar day; stored dates are end boundaries.

    A September 30 24:00 stamp is stored as October 1 midnight. September
    30 belongs to that block; October 1 belongs to the following block.
    The legacy SQLite column name `effective` is retained for compatibility.
    """
    return next((d for d in dates if d > day), None)

def reconcile_dates(manifest):
    """Apply interval-ending semantics to both new and existing snapshots."""
    ends = [max(ds) for ds in manifest['dates'].values()]
    common_end = min(ends)
    last_day = date.fromisoformat(common_end) - timedelta(days=1)
    manifest['last_year'] = last_day.year - (last_day < date(last_day.year, 7, 1))
    if manifest['last_year'] < manifest['start_year']:
        raise ValueError('Inputs do not cover July 1 of the selected start year')
    manifest['date_convention'] = 'interval_end'
    manifest['coverage_end'] = common_end
    manifest['date_note'] = ('Display year Y is July 1 of Y. Each ITLN timestamp ends its block: '
        'values apply after the preceding timestamp through this timestamp. '
        '24:00 closes the stated day. No values are extended past the final block.')
    return manifest

def input_periods(manifest, active):
    periods = {}
    for source, end in active.items():
        dates = manifest['dates'][source]; i = dates.index(end)
        periods[source] = {'effective':end, 'start':dates[i-1] if i else None,
            'raw':(date.fromisoformat(end)-timedelta(days=1)).isoformat()+' 24:00'}
    return periods

def choose(directory, stem):
    matches = [p for p in directory.iterdir() if re.search(r'(^|_)'+re.escape(stem)+r'($|[.(])',p.stem,re.I) and p.suffix.lower() in ('.dat','.zip')]
    if len(matches) != 1: raise ValueError(f'Expected one {stem} DAT or ZIP in {directory}; found {[p.name for p in matches]}')
    return matches[0]

@contextmanager
def lines(path):
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as z:
            members = [n for n in z.namelist() if n.lower().endswith('.dat')]
            if len(members) != 1: raise ValueError(f'Expected one DAT in {path}')
            with z.open(members[0]) as f:
                yield (b.decode('utf-8', 'replace') for b in f)
    else:
        with path.open(encoding='utf-8', errors='replace') as f: yield f

def noncomments(stream):
    for line in stream:
        s = line.strip()
        if s and not re.match(r'^[Cc](?:\s|[*-]|$)',s) and not s.startswith(('#', '*')):
            yield s

def geometry(nodes_path, elements_path):
    with lines(nodes_path) as stream:
        rows = iter(noncomments(stream))
        n = int(next(rows).split('/')[0]); next(rows)  # ND and FACT; coordinates remain in native units.
        nodes = {}
        for _ in range(n):
            v = next(rows).split(); nodes[int(v[0])] = (float(v[1]), float(v[2]))
    with lines(elements_path) as stream:
        rows = iter(noncomments(stream))
        ne = int(next(rows).split('/')[0]); next(rows)  # NE, NREGN
        # Subregion names carry /RNAME; element records have 6 integer fields.
        elements = []
        for row in rows:
            v = row.split()
            if len(v) != 6 or not all(x.lstrip('-').isdigit() for x in v): continue
            eid, *rest = map(int, v)
            ids, region = rest[:4], rest[4]
            xy = [nodes[i] for i in ids if i]
            if len(xy) < 3: raise ValueError(f'Element {eid} has fewer than 3 nodes')
            area = abs(sum(xy[i][0]*xy[(i+1)%len(xy)][1]-xy[(i+1)%len(xy)][0]*xy[i][1] for i in range(len(xy))))/2
            elements.append((eid, region, area, xy))
    if len(elements) != ne or len({e[0] for e in elements}) != ne: raise ValueError('Element count or IDs inconsistent')
    return elements

def subbasins(path, valid_ids):
    """Read a horizontal-plane IWFM ZBudget zone definition and validate membership."""
    with lines(path) as stream:
        rows = iter(noncomments(stream))
        extent = int(next(rows).split('/')[0].strip())
        if extent != 1: raise ValueError(f'{path.name}: expected horizontal ZEXTENT=1')
        names = {}; assignment = {}; reading_elements = False
        for line in rows:
            fields = line.split('/')[0].split()
            if len(fields) < 2: continue
            if not reading_elements and fields[0].lstrip('-').isdigit() and not fields[1].lstrip('-').isdigit():
                zone = int(fields[0]); names[zone] = ' '.join(fields[1:]); continue
            if len(fields) == 2 and all(x.lstrip('-').isdigit() for x in fields):
                reading_elements = True
                eid, zone = map(int, fields)
                if eid not in valid_ids: raise ValueError(f'{path.name}: unknown element {eid}')
                if eid in assignment: raise ValueError(f'{path.name}: duplicate element {eid}')
                if zone not in names: raise ValueError(f'{path.name}: zone {zone} has no name')
                assignment[eid] = zone
        if not names or not assignment: raise ValueError(f'{path.name}: no subbasin names or assignments')
    names[-99] = 'Unassigned (ZoneBudget -99)'
    return names, {eid: assignment.get(eid, -99) for eid in valid_ids}

def hashfile(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(4*1024*1024), b''): h.update(b)
    return h.hexdigest()

def utm_to_web_mercator(easting, northing, zone=10):
    """NAD83 GRS80 UTM north to EPSG:3857; suitable for imagery overlay."""
    a = 6378137.0; f = 1/298.257222101; e2 = f*(2-f)
    ep2 = e2/(1-e2); k0 = 0.9996
    m = northing/k0
    mu = m/(a*(1-e2/4-3*e2**2/64-5*e2**3/256))
    e1 = (1-math.sqrt(1-e2))/(1+math.sqrt(1-e2))
    phi = (mu + (3*e1/2-27*e1**3/32)*math.sin(2*mu)
           + (21*e1**2/16-55*e1**4/32)*math.sin(4*mu)
           + 151*e1**3/96*math.sin(6*mu) + 1097*e1**4/512*math.sin(8*mu))
    sinphi=math.sin(phi); cosphi=math.cos(phi); tanphi=math.tan(phi)
    n=a/math.sqrt(1-e2*sinphi**2)
    r=a*(1-e2)/(1-e2*sinphi**2)**1.5
    t=tanphi**2; c=ep2*cosphi**2; d=(easting-500000)/(n*k0)
    lat=(phi-(n*tanphi/r)*(d*d/2-(5+3*t+10*c-4*c*c-9*ep2)*d**4/24
          +(61+90*t+298*c+45*t*t-252*ep2-3*c*c)*d**6/720))
    lon=math.radians(zone*6-183)+(d-(1+2*t+c)*d**3/6
         +(5-2*c+28*t-3*c*c+8*ep2+24*t*t)*d**5/120)/cosphi
    if not -math.radians(85.05112878)<lat<math.radians(85.05112878):
        raise ValueError('Node outside Web Mercator extent; check input CRS')
    return [a*lon, a*math.log(math.tan(math.pi/4+lat/2))]

def parse_area(conn, source, path, min_date, valid_ids):
    codes = SOURCES[source][1]
    inserted = 0; dates = []; last_date = None; count = 0; pending = []
    with lines(path) as stream:
        for lineno, line in enumerate(stream, 1):
            v = line.split()
            if not v or re.match(r'^[Cc](?:\s|[*-]|$)',line.strip()) or v[0].startswith(('#','*')): continue
            if DATE_RE.match(v[0]):
                last_date = datestamp(v.pop(0)); dates.append(last_date); count = 0
            if last_date is None: continue
            if len(v) != len(codes)+1: raise ValueError(f'{path.name}:{lineno}: expected element plus {len(codes)} areas, got {len(v)} fields')
            eid = int(v[0]); count += 1
            if eid not in valid_ids: raise ValueError(f'{path.name}:{lineno}: unknown element {eid}')
            if last_date >= min_date:
                vals = [float(x) for x in v[1:]]
                if not all(math.isfinite(x) for x in vals): raise ValueError(f'{path.name}:{lineno}: nonfinite area')
                pending.append((source,last_date,eid,struct.pack('<'+'f'*len(vals),*vals)))
                if len(pending) >= 10000:
                    conn.executemany('INSERT INTO areas VALUES (?,?,?,?)',pending); inserted += len(pending); pending.clear()
        if pending: conn.executemany('INSERT INTO areas VALUES (?,?,?,?)',pending); inserted += len(pending)
    if not dates or len(dates) != len(set(dates)): raise ValueError(f'{path.name}: missing or duplicate dates')
    if dates != sorted(dates): raise ValueError(f'{path.name}: dates must increase')
    # Retain the prior annual endpoint to describe the initial interval.
    print(f'{source}: {len(dates)} update dates, {inserted:,} element records retained', flush=True)
    return dates

def aggregate_groups(conn, group, source_codes=None):
    """Sum each source/date/category across member elements once per build."""
    table = 'region_areas' if group == 'region' else 'subbasin_areas'
    conn.execute(f'CREATE TABLE {table}(source TEXT, effective TEXT, {group} INTEGER, vals BLOB, PRIMARY KEY(source,effective,{group})) WITHOUT ROWID')
    for source, codes in (source_codes or {s:c for s,(_,c) in SOURCES.items()}).items():
        n = len(codes); fmt = '<' + 'f'*n
        for (effective,) in conn.execute('SELECT DISTINCT effective FROM areas WHERE source=? ORDER BY effective',(source,)):
            totals = {}
            for group_id,blob in conn.execute(f'SELECT e.{group},a.vals FROM areas a JOIN elements e ON e.id=a.element WHERE a.source=? AND a.effective=?',(source,effective)):
                summed = totals.setdefault(group_id,[0.0]*n)
                for i,v in enumerate(struct.unpack(fmt,blob)): summed[i] += v
            conn.executemany(f'INSERT INTO {table} VALUES (?,?,?,?)',[(source,effective,r,struct.pack('<'+'d'*n,*vals)) for r,vals in totals.items()])
        conn.commit()
    print(f'Aggregated {group} time series',flush=True)

def build(args):
    indir, out = Path(args.input), Path(args.output)
    if getattr(args, 'model_config', None) and getattr(args, 'reader', 'legacy') != 'iwfm-io':
        raise ValueError('--model-config requires --reader iwfm-io')
    adapter_build = None
    if getattr(args, 'reader', 'legacy') == 'iwfm-io':
        from iwfm_build import AdapterBuild
        adapter_build = AdapterBuild(args)
    out.mkdir(parents=True,exist_ok=True)
    if adapter_build:
        paths = adapter_build.paths
    else:
        paths = {s: choose(indir,stem) for s,(stem,_) in SOURCES.items()}
        paths['nodes'] = choose(indir,'Nodes'); paths['elements'] = choose(indir,'Elements')
    print('Reading geometry...',flush=True)
    elems = geometry(paths['nodes'],paths['elements'])
    zones = [p for p in indir.iterdir() if p.suffix.lower() in ('.dat','.zip') and ('zbudget' in p.stem.lower() or 'zonedef' in p.stem.lower())]
    if len(zones) > 1: raise ValueError(f'Expected at most one ZoneBudget zone definition; found {[p.name for p in zones]}')
    zone_names, zone_by_element = subbasins(zones[0],{e[0] for e in elems}) if zones else ({},{})
    if zones: paths['subbasins'] = zones[0]
    db = out/'landuse.sqlite'; db.unlink(missing_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; CREATE TABLE areas(source TEXT, effective TEXT, element INTEGER, vals BLOB, PRIMARY KEY(source,effective,element)) WITHOUT ROWID; CREATE TABLE elements(id INTEGER PRIMARY KEY, region INTEGER, subbasin INTEGER, acres REAL, xy TEXT);')
    conn.executemany('INSERT INTO elements VALUES (?,?,?,?,?)',[(i,r,zone_by_element.get(i),a/4046.8564224,json.dumps([utm_to_web_mercator(x,y,args.utm_zone) for x,y in xy],separators=(',',':'))) for i,r,a,xy in elems])
    valid = {e[0] for e in elems}
    all_dates = {}
    configured = adapter_build is not None and getattr(adapter_build, 'config', None) is not None
    for s in (adapter_build.adapter.sources if configured else SOURCES):
        # Keep the preceding annual block, including March/September 1999.
        all_dates[s] = (adapter_build.parse_area(conn,s,f'{args.start_year-1}-01-01',valid,SOURCES[s][1])
            if adapter_build else parse_area(conn,s,paths[s],f'{args.start_year-1}-01-01',valid))
        retained = [d for d in all_dates[s] if d >= f'{args.start_year-1}-01-01']
        for d in retained:
            count = conn.execute('SELECT COUNT(*) FROM areas WHERE source=? AND effective=?',(s,d)).fetchone()[0]
            if count != len(elems): raise ValueError(f'{s} {d}: {count} rows, expected {len(elems)}')
        conn.commit()
    codes = adapter_build.codes if configured else {s:c for s,(_,c) in SOURCES.items()}
    aggregate_groups(conn,'region',codes)
    if zones: aggregate_groups(conn,'subbasin',codes)
    max_year = 0  # Set from the final interval endpoints below.
    manifest={'start_year':args.start_year,'last_year':max_year,'count':len(elems),'utm_zone':args.utm_zone,'map_crs':'EPSG:3857','sources':{s:{'file':p.name,'sha256':hashfile(p)} for s,p in paths.items()},'subbasins':zone_names,'dates':{s:[d for d in ds if d>=f'{args.start_year-1}-01-01'] for s,ds in all_dates.items()},'codes':{s:c for s,(_,c) in SOURCES.items()},'labels':LABELS,'date_note':''}
    reconcile_dates(manifest); max_year = manifest['last_year']
    if adapter_build: manifest['input_reader'] = adapter_build.metadata
    if configured:
        presentation = adapter_build.presentation()
        manifest['codes'] = codes
        manifest['labels'] = presentation['labels']
        manifest['model_configuration'] = presentation
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (out/'index.html').write_text(HTML,encoding='utf-8')
    conn.execute('ANALYZE'); conn.commit(); conn.close()
    print(f'Built {out}: {len(elems):,} elements; display years {args.start_year}–{max_year}',flush=True)

HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>C2VSimFG land use · Land use exports Oct 15</title>
<style>body{margin:0;font:14px system-ui;color:#182836;background:#f3f6f8}header{padding:12px 18px;background:#14394a;color:white}h1{font-size:18px;margin:0}main{display:grid;grid-template-columns:minmax(260px,300px) minmax(400px,1fr) minmax(390px,460px);height:calc(100vh - 58px)}aside{padding:14px;overflow:auto;background:white;border-right:1px solid #ccd5db}#detail{border-left:1px solid #ccd5db;border-right:0}#mapwrap{position:relative;overflow:hidden;background:#edf2f0}#tiles{position:absolute;inset:0;overflow:hidden}#tiles img{position:absolute;max-width:none;pointer-events:none}#map{position:absolute;inset:0;z-index:1}#attribution{position:absolute;bottom:4px;right:5px;z-index:2;background:#fffc;padding:2px 5px;font-size:11px}#map{width:100%;height:100%;display:block;cursor:crosshair}label{display:block;margin:7px 0}select,input,button{font:inherit}select,input[type=number],input[type=text]{padding:5px;max-width:100%}button{padding:6px 10px;background:#e6eef2;border:1px solid #a9bac5;border-radius:4px;cursor:pointer;margin:3px}button:hover{background:#d2e4ed}fieldset{border:1px solid #d9e2e6;margin:12px 0;padding:8px}legend{font-weight:650}fieldset label{white-space:nowrap}#chartwrap{position:relative}#chart{width:100%;height:310px}#charttip{box-sizing:border-box;min-height:105px;max-height:165px;overflow:auto;background:#fff;padding:7px 9px;border:1px solid #b7c7cf;border-radius:4px;font-size:12px;line-height:1.4}#primary{position:sticky;top:-14px;z-index:4;background:white;padding:7px 0 9px;border-bottom:1px solid #d9e2e6}#primary label{margin:5px 0}#categories details{border:1px solid #d9e2e6;margin:7px 0;padding:6px 8px}#categories summary{font-weight:650;cursor:pointer}#categories label{white-space:normal}#categorySearch{box-sizing:border-box;width:100%;margin-top:5px}#maplegend{position:absolute;bottom:20px;left:12px;z-index:2;width:235px;box-sizing:border-box;padding:9px 11px;background:#fffffff0;border:1px solid #bccbd1;border-radius:5px;box-shadow:0 2px 8px #0003;font-size:12px}#maplegend .ramp{height:12px;margin:7px 0 3px;background:linear-gradient(90deg,rgb(236,238,231),rgb(56,173,137))}#maplegend .range{display:flex;justify-content:space-between}#maplegend small{display:block;color:#49616c;margin-top:5px}#inputdates{margin:12px 0;color:#49616c;font-size:12px}#inputdates summary{cursor:pointer}#status{color:#d2e5ec;font-size:12px;line-height:1.45}#inputdates{font-size:13px;line-height:1.5}#datemethod{font-size:12px}.panel-options{border:1px solid #d9e2e6;border-radius:4px;margin:12px 0;padding:8px}.panel-options>summary{font-weight:650;cursor:pointer}.panel-options>fieldset{margin-top:10px}#toolhint{position:absolute;top:12px;left:62px;right:12px;z-index:4;width:fit-content;max-width:calc(100% - 74px);box-sizing:border-box;padding:8px 12px;background:#fffffff2;border:1px solid #bccbd1;border-radius:4px;box-shadow:0 1px 5px #999;font-size:13px;pointer-events:none}#tooltip{z-index:3;position:absolute;pointer-events:none;background:white;padding:5px 8px;box-shadow:0 1px 8px #5558;display:none}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:3px 5px;border-bottom:1px solid #e1e7eb;text-align:right}td:first-child,th:first-child{text-align:left}@media(max-width:1000px){main{grid-template-columns:230px 1fr;grid-template-rows:60vh auto;height:auto}#detail{grid-column:1/3;border-top:1px solid #ccd5db}}.geography-note{font-size:13px;line-height:1.5;color:#425965;margin:7px 0 12px}#subbasinjump{width:100%}#detail h2{font-size:19px;margin-bottom:5px}#exportcontrols{border-top:1px solid #d9e2e6;margin:12px 0;padding-top:7px}#exportcontrols select{max-width:100%}.export-note,#exportstatus{font-size:12px;line-height:1.4;color:#49616c;margin:5px 0}button:disabled{cursor:default;opacity:.5}</style>
<header><h1>C2VSimFG land use inputs</h1><span id="status"></span></header><main><aside><div id="primary"><label>Explore by <select id="mode"><option value="element">Element</option><option value="region">Subregion</option><option value="subbasin">Subbasin</option></select></label><label>Display year (as of July 1) <select id="year"></select></label><label>Map category or group <select id="metric"></select></label></div><div id="subbasinreference" hidden><label>Subbasin <select id="subbasinjump"><option value="">Choose a subbasin</option></select></label></div><p id="basincontext" class="geography-note" hidden></p><label><input id="percent" type="checkbox"> Map as percent of selected area</label><details id="mapappearance" class="panel-options" open><summary>Map appearance</summary><label><input id="aerial" type="checkbox" checked> USGS aerial imagery</label><fieldset><legend>Boundary overlays</legend><label><input id="boundaries" type="checkbox" checked> Show subregion boundaries</label><label><input id="subbasinboundaries" type="checkbox"> Show subbasin boundaries</label></fieldset><label>Land use fill opacity <input id="opacity" type="range" min="0" max="100" value="65"><span id="opacityValue">65%</span></label></details><label><input id="syncchart" type="checkbox" checked> Match chart selection to map category</label><details id="chartoptions" class="panel-options" open><summary>Chart options</summary><fieldset><legend>Combine chart series</legend><label><input id="nonpondedgroup" type="checkbox" checked> Nonponded crops</label><label><input id="rice" type="checkbox" checked> Three rice types</label><label><input id="refuge" type="checkbox" checked> Two refuge types</label><label><input id="native" type="checkbox"> Native + riparian vegetation</label></fieldset><label>Find chart category <input id="categorySearch" type="search" placeholder="Search crops and land uses"></label><div id="categories"></div><button id="selectall">Select all</button><button id="clear">Clear</button></details></aside><div id="mapwrap"><div id="tiles"></div><canvas id="map"></canvas><div id="mapcontrols" role="group" aria-label="Map controls"><button id="zoomin" type="button" aria-label="Zoom in" title="Zoom in">+</button><button id="zoomout" type="button" aria-label="Zoom out" title="Zoom out">−</button><button id="resetzoom" type="button" aria-label="Reset map view" title="Reset view">⌂</button><button id="boxzoom" type="button" aria-label="Zoom to drawn box" title="Draw a box to zoom" aria-pressed="false"><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="9" cy="9" r="5"/><path d="m13 13 7 7"/><path d="M6 7h6M6 11h6" stroke-dasharray="2 2"/></svg></button><button id="boxselect" type="button" aria-label="Select elements by box" title="Draw a box to select elements; Shift adds to selection" aria-pressed="false"><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="3" y="3" width="17" height="17" rx="1" stroke-dasharray="3 2"/><path d="m8 12 3 3 6-7"/></svg></button></div><div id="toolhint" role="status" hidden></div><div id="zoomrectangle"></div><div id="tooltip"></div><div id="maplegend" aria-live="polite"></div><div id="attribution">Imagery: USGS The National Map / USDA · current context, not selected model year</div></div><aside id="detail"><h2 id="element">Select an element</h2><p id="selectioncontext" class="geography-note" hidden></p><div id="selectiontools" hidden><span id="selectedids"></span><button id="clearselection" type="button">Clear selection</button></div><label><span id="golabel">Go to element</span> <input id="goto" type="text" list="gotooptions"><datalist id="gotooptions"></datalist><button id="go">Go</button></label><details id="inputdates"><summary>Input date details</summary><p id="periods"></p><p id="datemethod"></p></details><div id="chartwrap"><canvas id="chart"></canvas><div id="charttip">Hover over the chart to inspect values.</div></div><div id="legend"></div><div id="exportcontrols"><label>Download data <select id="exportscope"><option value="chart">Charted categories</option><option value="all">All land-use categories</option></select></label><button id="exportcsv" type="button" disabled>Download CSV</button><button id="exportpng" type="button" disabled>Save chart (PNG)</button><p class="export-note">CSV dates use interval start (inclusive) and end (exclusive).</p><p id="exportstatus" role="status"></p></div><h3 id="valuedate">Areas on July 1</h3><table id="values"></table><details id="viewerhelp" class="panel-options"><summary>Help</summary><p>Drag to pan. Scroll or use + and − to zoom. Use the magnifying glass to draw a zoom window.</p><p>In Element mode, click to select one element. Shift-click to add or remove an element. Use the dashed-box button to select every element the box touches. Hold Shift while starting the box to add to your selection. Escape cancels a box tool. Up to 500 elements can be selected.</p><p>The chart and table summarize the selected area. Map years represent July 1. Aerial imagery provides current geographic context and needs internet access.</p></details></aside></main>
<style>#mapcontrols{position:absolute;top:12px;left:12px;z-index:4;background:#fff;padding:4px;box-shadow:0 1px 5px #999;border-radius:4px}#mapcontrols button{display:block;width:34px;height:32px;margin:0;padding:0;border:0;border-radius:0;background:#fff;color:#14394a;box-shadow:none;font-size:18px;font-weight:400;line-height:1}#mapcontrols button:hover,#mapcontrols button[aria-pressed=true]{background:#d3e9ee}#mapcontrols button:disabled{opacity:.35;cursor:default}#mapcontrols svg{vertical-align:middle}#map.boxzoom{cursor:crosshair}#zoomrectangle{display:none;position:absolute;z-index:4;pointer-events:none;border:2px dashed #12394a;background:#69b4c938;box-sizing:border-box}#selectiontools{font-size:12px;color:#49616c;line-height:1.5;margin:0 0 10px}#selectiontools button{font-size:12px;margin-left:7px}#mapcontrols button:focus-visible{outline:3px solid #247aa5;outline-offset:-3px}</style>
<script>
const $=id=>document.getElementById(id); let manifest,geometry=[],extent, selected=null, selectedData=null,mapValues={}, view={x:0,y:0,scale:1}, hovered=null, mapBusy=false, tileCache=new Map(), regionEdges=[], subbasinEdges=[], selectedEdges=[], selectedIds=new Set(), boxZoom=false, boxSelect=false, zoomRect=null, selectionRequest=0, chartState=null;
function mode(){return $('mode').value}
function featureId(g){return mode()==='region'?g.region:mode()==='subbasin'?g.subbasin:g.id}
function selectionLabel(){return mode()==='region'?'subregion':mode()==='subbasin'?'subbasin':selectedIds.size>1?'selected elements':'element'}
const groups={'Total irrigated agriculture':[],'All nonponded crops':[],'All rice':['RI_FL','RI_NFL','RI_NDC'],'All refuge':['RF_SL','RF_PR'],'Native + riparian vegetation':['NV','RV']};
const categoryColors=['#137f8b','#d35a49','#9472ad','#b48b31','#537db5','#4e9a68','#ae6688','#6d7690','#d07e48','#718b47','#435ba8','#8c564b','#a54370','#467b96','#806930','#396f45','#ad6a1d','#7b4d9c','#a54b3d','#526f70','#337e95','#239d87','#427d61','#7955a1','#a75b99','#698c40','#9c7939','#626a79'];// Stable colors use category identity, rather than its position among visible series.
// Names from DWR Bulletin 118 basin feature service, retrieved October 14, 2026.
const basinNameByCode = {"2-003": "Suisun-Fairfield Valley", "2-004": "Pittsburg Plain", "2-005": "Clayton Valley", "2-006": "Ygnacio Valley", "2-031": "Arroyo del Hambre Valley", "5-006.01": "Bowman", "5-006.03": "Anderson", "5-006.04": "Enterprise", "5-006.05": "Millville", "5-006.06": "South Battle Creek", "5-021.50": "Red Bluff", "5-021.51": "Corning", "5-021.52": "Colusa", "5-021.53": "Bend", "5-021.54": "Antelope", "5-021.56": "Los Molinos", "5-021.57": "Vina", "5-021.60": "North Yuba", "5-021.61": "South Yuba", "5-021.62": "Sutter", "5-021.64": "North American", "5-021.65": "South American", "5-021.66": "Solano", "5-021.67": "Yolo", "5-021.69": "Wyandotte Creek", "5-021.70": "Butte", "5-022.01": "Eastern San Joaquin", "5-022.02": "Modesto", "5-022.03": "Turlock", "5-022.04": "Merced", "5-022.05": "Chowchilla", "5-022.06": "Madera", "5-022.07": "Delta-Mendota", "5-022.08": "Kings", "5-022.09": "Westside", "5-022.10": "Pleasant Valley", "5-022.11": "Kaweah", "5-022.12": "Tulare Lake", "5-022.13": "Tule", "5-022.14": "Kern County", "5-022.15": "Tracy", "5-022.16": "Cosumnes", "5-022.17": "Kettleman Plain", "5-022.18": "White Wolf", "5-022.19": "East Contra Costa"};
function applySubbasinNames(data){
  const ids=data.subbasin_ids||{...data.subbasins};
  data.subbasin_ids=ids;
  data.subbasins=Object.fromEntries(Object.entries(data.subbasins||{}).map(([id,label])=>{
    const code=ids[id],name=basinNameByCode[code];return [id,name?`${name} (${code})`:label];
  }));return data;
}
const groupColors={'All rice':'#137f8b','All refuge':'#9472ad','All nonponded crops':'#b48b31','Native + riparian vegetation':'#4e9a68','Total irrigated agriculture':'#267747'};
function seriesColor(g){
  if(groupColors[g.name])return groupColors[g.name];
  if(g.name.startsWith('Selected rice'))return groupColors['All rice'];
  if(g.name.startsWith('Selected refuge'))return groupColors['All refuge'];
  if(g.name.startsWith('Selected nonponded'))return groupColors['All nonponded crops'];
  const code=g.codes[0],index=Object.values(manifest.codes).flat().indexOf(code);
  return categoryColors[Math.max(0,index)%categoryColors.length];
}
function selectedGeography(){
  if(!selectedData)return null;
  const d=selectedData,m=d.selectionMode,ids=new Set(d.selectionIds||[]);
  const members=geometry.filter(g=>m==='element'?ids.has(g.id):m==='region'?g.region===d.selectionKey:g.subbasin===d.selectionKey);
  const regions=[...new Set(members.map(g=>g.region))].sort((a,b)=>a-b);
  const basins=[...new Set(members.map(g=>g.subbasin))].sort((a,b)=>a-b);
  const names=basins.map(id=>manifest.subbasins?.[id]||'Unassigned');
  const elementIds=members.map(g=>g.id).sort((a,b)=>a-b);
  const title=m==='region'?'Subregion '+d.selectionKey:m==='subbasin'?'Subbasin '+(manifest.subbasins[d.selectionKey]||d.selectionKey):elementIds.length===1?'Element '+elementIds[0]:elementIds.length+' selected elements';
  return {type:m==='element'&&elementIds.length>1?'elements':m,title,regions,names,elementIds,acres:d.acres};
}
function refreshSelectionContext(){
  const info=selectedGeography();$('selectioncontext').hidden=!info;
  const basinMode=mode()==='subbasin';$('basincontext').hidden=!basinMode;
  if(!info){$('selectioncontext').textContent='';$('basincontext').textContent='Select a subbasin to see its subregions.';$('subbasinjump').value='';return}
  $('element').textContent=info.title;
  $('selectioncontext').textContent='Subregion'+(info.regions.length===1?'':'s')+': '+info.regions.join(', ')+' · Subbasin'+(info.names.length===1?'':'s')+': '+info.names.join('; ')+' · '+info.acres.toLocaleString(undefined,{maximumFractionDigits:1})+' acres';
  $('basincontext').textContent=basinMode?info.title+' · Overlapping subregions: '+info.regions.join(', '):'';
  $('subbasinjump').value=basinMode?String(selectedData.selectionKey):'';if(basinMode)$('goto').value=manifest.subbasins[selectedData.selectionKey];
}
function updateGoToOptions(){
  const currentMode=mode(),field=$('goto'),list=$('gotooptions');
  const values=currentMode==='subbasin'
    ? Object.values(manifest.subbasins||{}).sort((a,b)=>a.localeCompare(b))
    : [...new Set(geometry.map(g=>currentMode==='region'?g.region:g.id))].sort((a,b)=>a-b);
  const options=document.createDocumentFragment();
  for(const value of values)options.append(new Option(String(value),String(value)));
  list.replaceChildren(options);
  const label=currentMode==='element'?'element':currentMode==='region'?'subregion':'subbasin';
  $('golabel').textContent='Go to '+label;
  field.placeholder=currentMode==='subbasin'?'Type or choose a subbasin':'Type or choose '+(currentMode==='element'?'an element ID':'a subregion number');
  field.setAttribute('aria-label','Go to '+label);
  field.setAttribute('inputmode',currentMode==='subbasin'?'text':'numeric');
}
function initSubbasinReference(){
  const crosswalk=new Map();for(const g of geometry){if(!manifest.subbasins?.[g.subbasin])continue;if(!crosswalk.has(g.subbasin))crosswalk.set(g.subbasin,new Set());crosswalk.get(g.subbasin).add(g.region)}
  for(const [id,regions] of [...crosswalk].sort((a,b)=>manifest.subbasins[a[0]].localeCompare(manifest.subbasins[b[0]]))){
    const list=[...regions].sort((a,b)=>a-b);$('subbasinjump').add(new Option(manifest.subbasins[id]+' · Subregions '+list.join(', '),id));
  }
  $('subbasinreference').hidden=mode()!=='subbasin';
  $('subbasinjump').onchange=()=>{if($('subbasinjump').value!==''){ $('goto').value=manifest.subbasins[$('subbasinjump').value];$('go').click() }};
}
function syncExportButtons(busy=false){
  const selected=!!selectedData;
  $('exportcsv').disabled=busy||!selected||($('exportscope').value==='chart'&&!chartState?.groupsNow.length);
  $('exportpng').disabled=busy||!selected||!chartState?.groupsNow.length;
}
function csvCell(value){let text=value===null||value===undefined?'':String(value);return '"'+text.replace(/"/g,'""')+'"'}
function csvNumber(value,digits){return value===null||value===undefined||!Number.isFinite(value)?'':value.toFixed(digits)}
function makeSeriesCsv(data,series,info){
  const rows=chartRows(data,series),out=[['selection_type','selection_name','element_ids','subregion_ids','subbasin_names','selected_area_acres','category','category_codes','interval_start_inclusive','interval_end_exclusive','acres','percent_of_selected_area']];
  for(let i=0;i<rows.length-1;i++)for(const g of series){
    const value=rows[i][g.name];out.push([info.type,info.title,['element','elements'].includes(info.type)?info.elementIds.join(';'):'',info.regions.join(';'),info.names.join(';'),csvNumber(info.acres,2),g.name,g.codes.join(';'),rows[i].date,rows[i+1].date,csvNumber(value,2),csvNumber(value===null||!info.acres?null:100*value/info.acres,1)]);
  }
  return '\ufeff'+out.map(row=>row.map(csvCell).join(',')).join('\r\n')+'\r\n';
}
function downloadBlob(blob,filename){
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
}
function exportFilename(info,extension){return 'C2VSimFG_landuse_'+info.title.replace(/[^a-zA-Z0-9_-]+/g,'_')+'.'+extension}
function exportCsv(){
  if(!selectedData)return;const info=selectedGeography(),all=$('exportscope').value==='all';
  const series=all?Object.values(manifest.codes).flat().map(code=>({name:manifest.labels[code],codes:[code]})):chartState.groupsNow;
  if(!series.length)return;
  downloadBlob(new Blob([makeSeriesCsv(selectedData,series,info)],{type:'text/csv;charset=utf-8'}),exportFilename(info,all?'all_categories.csv':'charted_categories.csv'));
  $('exportstatus').textContent='Downloaded '+(all?'all land-use categories':'charted categories')+' as CSV.';
}
function wrapCanvasText(ctx,text,width){
  const lines=[];let line='';for(const word of text.split(/\s+/)){const next=line?line+' '+word:word;if(line&&ctx.measureText(next).width>width){lines.push(line);line=word}else line=next}if(line)lines.push(line);return lines;
}
async function exportChart(){
  if(!chartState?.groupsNow.length||!selectedData)return;
  const state=chartState,info=selectedGeography();syncExportButtons(true);$('exportstatus').textContent='Preparing chart image…';
  try{
    const width=1440,margin=42,plot=document.createElement('canvas');
    paintChart(null,{canvas:plot,width:width-2*margin,height:540,dpr:1,state});
    const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');ctx.font='18px system-ui';
    const metadata=['Subregions: '+info.regions.join(', '),'Subbasins: '+info.names.join('; '),
      'Selected area: '+info.acres.toLocaleString(undefined,{maximumFractionDigits:1})+' acres · Chart units: acres',
      'Period: '+displayDate(state.rows[0].date)+' through '+displayDate(state.rows.at(-1).date)+' (exclusive)',
      'Map date: '+displayDate(state.selectedDate)];
    if(info.type==='elements')metadata.push('Elements: '+info.elementIds.slice(0,24).join(', ')+(info.elementIds.length>24?' … (complete list in CSV)':''));
    const metaLines=metadata.flatMap(text=>wrapCanvasText(ctx,text,width-2*margin));
    const colWidth=(width-2*margin)/2;
    const legend=state.groupsNow.map(g=>({g,lines:wrapCanvasText(ctx,g.name+' ['+g.codes.join(', ')+']',colWidth-42)}));
    let legendHeight=0;for(let i=0;i<legend.length;i+=2)legendHeight+=Math.max(legend[i].lines.length,legend[i+1]?.lines.length||0)*25+10;
    const chartY=100+metaLines.length*27;canvas.width=width;canvas.height=chartY+plot.height+legendHeight+95;
    ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.fillStyle='#14394a';ctx.font='bold 28px system-ui';ctx.fillText('C2VSimFG land use · '+info.title,margin,44);
    ctx.fillStyle='#425965';ctx.font='18px system-ui';metaLines.forEach((line,i)=>ctx.fillText(line,margin,80+i*27));
    ctx.drawImage(plot,margin,chartY);let y=chartY+plot.height+30;
    for(let i=0;i<legend.length;i+=2){for(let j=0;j<2&&i+j<legend.length;j++){const item=legend[i+j],x=margin+j*colWidth;ctx.strokeStyle=seriesColor(item.g);ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(x,y-5);ctx.lineTo(x+24,y-5);ctx.stroke();ctx.fillStyle='#182836';item.lines.forEach((line,k)=>ctx.fillText(line,x+34,y+k*25))}y+=Math.max(legend[i].lines.length,legend[i+1]?.lines.length||0)*25+10}
    ctx.fillStyle='#49616c';ctx.font='15px system-ui';ctx.fillText('IWFM input intervals end at their timestamps; the chart does not extrapolate beyond input coverage.',margin,canvas.height-23);
    const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('Could not create PNG')),'image/png'));
    downloadBlob(blob,exportFilename(info,'png'));$('exportstatus').textContent='Downloaded chart with selection details and legend.';
  }catch(error){$('exportstatus').textContent='Chart export failed: '+error.message}finally{syncExportButtons()}
}

function active(){return [...document.querySelectorAll('.cat:checked')].map(x=>x.value)}
function refreshCategoryCounts(){document.querySelectorAll('.category-section').forEach(section=>{let n=section.querySelectorAll('.cat:checked').length;section.querySelector('summary').textContent=section.dataset.title+(n?` · ${n} selected`:'')})}
function syncChart(){if(!$('syncchart').checked)return;let m=$('metric').value,codes=groups[m]||[m];document.querySelectorAll('.cat').forEach(x=>x.checked=codes.includes(x.value));refreshCategoryCounts();renderDetail()}
function grouped(codes){let irrigated=groups['Total irrigated agriculture'];if($('metric').value==='Total irrigated agriculture'&&codes.length===irrigated.length&&codes.every(c=>irrigated.includes(c)))return [{name:'Total irrigated agriculture',codes:irrigated}];let items=[],seen=new Set();for(const c of codes){if(seen.has(c))continue;let group=null;if($('nonpondedgroup').checked&&groups['All nonponded crops'].includes(c)&&codes.filter(x=>groups['All nonponded crops'].includes(x)).length>1)group='All nonponded crops';if($('rice').checked&&groups['All rice'].includes(c))group='All rice';if($('refuge').checked&&groups['All refuge'].includes(c))group='All refuge';if($('native').checked&&groups['Native + riparian vegetation'].includes(c))group='Native + riparian vegetation';if(group){let members=groups[group].filter(x=>codes.includes(x));members.forEach(x=>seen.add(x));let name=group==='All nonponded crops'&&members.length<groups[group].length?`Selected nonponded crops (${members.length})`:group==='All rice'&&members.length<groups[group].length?`Selected rice (${members.length})`:group==='All refuge'&&members.length<groups[group].length?`Selected refuge (${members.length})`:group;items.push({name,codes:members})}else{seen.add(c);items.push({name:manifest.labels[c],codes:[c]})}}return items}
async function api(path){let r=await fetch(path);if(!r.ok)throw Error(await r.text());return r.json()}
function area(poly){let a=0;for(let i=0;i<poly.length;i++){let p=poly[i],q=poly[(i+1)%poly.length];a+=p[0]*q[1]-q[0]*p[1]}return Math.abs(a)/2}
function makeBoundaryEdges(field){let edges=new Map();for(const g of geometry){let xy=g.xy;for(let i=0;i<xy.length;i++){let a=xy[i],b=xy[(i+1)%xy.length],ak=a[0].toFixed(3)+','+a[1].toFixed(3),bk=b[0].toFixed(3)+','+b[1].toFixed(3),key=ak<bk?ak+'|'+bk:bk+'|'+ak;let edge=edges.get(key);if(edge){edge.count++;if(edge.region!==g[field])edge.crosses=true}else edges.set(key,{a,b,region:g[field],count:1,crosses:false})}}return [...edges.values()].filter(e=>e.count===1||e.crosses).map(e=>[e.a,e.b])}
function makeSelectionEdges(id){let edges=new Map();for(const g of geometry){if(featureId(g)!==id)continue;let xy=g.xy;for(let i=0;i<xy.length;i++){let a=xy[i],b=xy[(i+1)%xy.length],ak=a[0].toFixed(3)+','+a[1].toFixed(3),bk=b[0].toFixed(3)+','+b[1].toFixed(3),key=ak<bk?ak+'|'+bk:bk+'|'+ak;let edge=edges.get(key);if(edge)edge.count++;else edges.set(key,{a,b,count:1})}}return [...edges.values()].filter(e=>e.count===1).map(e=>[e.a,e.b])}
function initGeometry(){regionEdges=makeBoundaryEdges('region');if(Object.keys(manifest.subbasins||{}).length)subbasinEdges=makeBoundaryEdges('subbasin');extent=[Infinity,Infinity,-Infinity,-Infinity];for(const g of geometry)for(const p of g.xy){extent[0]=Math.min(extent[0],p[0]);extent[1]=Math.min(extent[1],p[1]);extent[2]=Math.max(extent[2],p[0]);extent[3]=Math.max(extent[3],p[1])}fit()}
function fit(){let c=$('map'),w=c.clientWidth,h=c.clientHeight;view.scale=Math.min((w-30)/(extent[2]-extent[0]),(h-30)/(extent[3]-extent[1]));view.x=w/2-(extent[0]+extent[2])*view.scale/2;view.y=h/2+(extent[1]+extent[3])*view.scale/2;draw()}
function zoomAt(factor,px,py){view.x=px-(px-view.x)*factor;view.y=py-(py-view.y)*factor;view.scale*=factor;$('tooltip').style.display='none';hovered=null;draw()}
function setBoxZoom(on){boxZoom=on;if(on)boxSelect=false;syncBoxTools();$('boxzoom').setAttribute('aria-pressed',String(on));$('map').classList.toggle('boxzoom',boxZoom||boxSelect);$('zoomrectangle').style.display='none';$('tooltip').style.display='none';hovered=null;draw()}
function syncBoxTools(){let hint=$('toolhint');hint.hidden=!boxZoom&&!boxSelect;hint.innerHTML=boxSelect?'<b>Select box</b> · Drag to select elements. Hold Shift to add. Escape cancels.':'<b>Zoom window</b> · Drag to zoom. Escape cancels.';$('boxzoom').setAttribute('aria-pressed',String(boxZoom));$('boxselect').setAttribute('aria-pressed',String(boxSelect));$('map').classList.toggle('boxzoom',boxZoom||boxSelect)}
function setBoxSelect(on){boxSelect=on&&mode()==='element';if(boxSelect)boxZoom=false;syncBoxTools();$('zoomrectangle').style.display='none';$('tooltip').style.display='none';hovered=null;draw()}
function polygonIntersectsBox(points,bounds){
  let [xmin,ymin,xmax,ymax]=bounds,inBox=p=>p[0]>=xmin&&p[0]<=xmax&&p[1]>=ymin&&p[1]<=ymax;
  if(points.every(p=>p[0]<xmin)||points.every(p=>p[0]>xmax)||points.every(p=>p[1]<ymin)||points.every(p=>p[1]>ymax))return false;
  if(points.some(inBox)||[[xmin,ymin],[xmin,ymax],[xmax,ymin],[xmax,ymax]].some(p=>inside(...p,points)))return true;
  for(let i=0;i<points.length;i++){
    let a=points[i],b=points[(i+1)%points.length],lo=0,hi=1;
    for(let axis=0;axis<2;axis++){
      let delta=b[axis]-a[axis],min=bounds[axis],max=bounds[axis+2];
      if(delta===0){if(a[axis]<min||a[axis]>max){lo=1;hi=0;break}}
      else{let t1=(min-a[axis])/delta,t2=(max-a[axis])/delta;lo=Math.max(lo,Math.min(t1,t2));hi=Math.min(hi,Math.max(t1,t2))}
    }
    if(lo<=hi)return true;
  }
  return false;
}
async function selectElementsInBox(a,b,append=false){
  let p=[(a[0]-view.x)/view.scale,(view.y-a[1])/view.scale],q=[(b[0]-view.x)/view.scale,(view.y-b[1])/view.scale];
  let bounds=[Math.min(p[0],q[0]),Math.min(p[1],q[1]),Math.max(p[0],q[0]),Math.max(p[1],q[1])];
  let next=append?new Set(selectedIds):new Set();
  for(const g of geometry)if(polygonIntersectsBox(g.xy,bounds))next.add(g.id);
  if(next.size>500){alert(`This box would select ${next.size.toLocaleString()} elements. Draw a smaller box (maximum 500). Your selection has been kept.`);return}
  selectedIds=next;selected=null;selectedEdges=[];draw();await loadSelection();
}

function showZoomBox(a,b){let el=$('zoomrectangle');el.style.display='block';el.style.left=Math.min(a[0],b[0])+'px';el.style.top=Math.min(a[1],b[1])+'px';el.style.width=Math.abs(b[0]-a[0])+'px';el.style.height=Math.abs(b[1]-a[1])+'px'}
function screen(p){return [view.x+p[0]*view.scale,view.y-p[1]*view.scale]}
function updateTiles(){let layer=$('tiles');if(!$('aerial').checked){layer.style.display='none';return}layer.style.display='block';const half=20037508.342789244,w=$('map').clientWidth,h=$('map').clientHeight;let z=Math.max(0,Math.min(16,Math.round(Math.log2(156543.03392804097*view.scale))));let resolution=156543.03392804097/2**z,span=256*resolution,size=span*view.scale;let left=(-view.x/view.scale+half),right=((w-view.x)/view.scale+half),top=(half-view.y/view.scale),bottom=(half-(view.y-h)/view.scale);let x0=Math.max(0,Math.floor(left/span)),x1=Math.min(2**z-1,Math.floor(right/span)),y0=Math.max(0,Math.floor(top/span)),y1=Math.min(2**z-1,Math.floor(bottom/span));let wanted=new Set();for(let y=y0;y<=y1;y++)for(let x=x0;x<=x1;x++){let key=`${z}/${y}/${x}`;wanted.add(key);let img=tileCache.get(key);if(!img){img=new Image();img.alt='';img.loading='eager';img.src=`https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/${z}/${y}/${x}`;tileCache.set(key,img);layer.append(img)}img.style.left=(view.x+(-half+x*span)*view.scale)+'px';img.style.top=(view.y-(half-y*span)*view.scale)+'px';img.style.width=size+'px';img.style.height=size+'px'}for(const [key,img] of tileCache)if(!wanted.has(key)){img.remove();tileCache.delete(key)}}
function updateMapLegend(max,hasValues){let label=$('metric').selectedOptions[0]?.textContent||'Land use',unit=$('percent').checked?'% of selected area':'acres',top=max.toLocaleString(undefined,{maximumFractionDigits:max<1?3:$('percent').checked?1:0});$('maplegend').innerHTML=`<b>${label}</b> · ${unit}<div class="ramp"></div><div class="range"><span>0</span><span>${top}${$('percent').checked?'%':''}+</span></div><small>${hasValues?'Colors reach maximum at the 97th percentile. Scale recalculates for each map and year.':'No positive values for this selection.'}</small>${$('metric').value==='Total irrigated agriculture'?'<small>Nonponded crops excluding Idle + all rice.</small>':''}`}
function draw(){let c=$('map'),dpr=devicePixelRatio||1,w=c.clientWidth,h=c.clientHeight;if(c.width!==Math.round(w*dpr)||c.height!==Math.round(h*dpr)){c.width=w*dpr;c.height=h*dpr}let ctx=c.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);if(!$('aerial').checked){ctx.fillStyle='#edf2f0';ctx.fillRect(0,0,w,h)}updateTiles();let vals=Object.values(mapValues).filter(v=>v>0),max=vals.length?vals.sort((a,b)=>a-b)[Math.floor(vals.length*.97)]:1;max=Math.max(max||1,1e-6);updateMapLegend(max,vals.length>0);for(const g of geometry){let pts=g.xy.map(screen);if(pts.every(p=>p[0]<-5)||pts.every(p=>p[0]>w+5)||pts.every(p=>p[1]<-5)||pts.every(p=>p[1]>h+5))continue;ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.closePath();let key=featureId(g),v=mapValues[key]||0,t=Math.min(1,v/max);ctx.fillStyle=v>0?`rgb(${Math.round(236-180*t)},${Math.round(238-65*t)},${Math.round(231-94*t)})`:'#e3e8e8';ctx.globalAlpha=$('aerial').checked?+$('opacity').value/100:1;ctx.fill();ctx.globalAlpha=1;if(view.scale>0.00012){ctx.strokeStyle='#758a9066';ctx.lineWidth=.45;ctx.stroke()}if(mode()==='element'&&(selectedIds.has(key)||key===hovered)){ctx.strokeStyle=selectedIds.has(key)?'#d4372f':'#164f68';ctx.lineWidth=selectedIds.has(key)?3:2;ctx.stroke()}}for(const [enabled,edges] of [[$('boundaries').checked,regionEdges],[$('subbasinboundaries').checked,subbasinEdges]])if(enabled&&edges.length){ctx.beginPath();for(const [a,b] of edges){let p=screen(a),q=screen(b);if((p[0]<-5&&q[0]<-5)||(p[0]>w+5&&q[0]>w+5)||(p[1]<-5&&q[1]<-5)||(p[1]>h+5&&q[1]>h+5))continue;ctx.moveTo(...p);ctx.lineTo(...q)}ctx.strokeStyle='#ffffffdd';ctx.lineWidth=4;ctx.stroke();ctx.strokeStyle=edges===subbasinEdges?'#edbf55':'#14394ae8';ctx.lineWidth=edges===subbasinEdges?2.5:2;ctx.stroke()}if(mode()!=='element'&&selectedEdges.length){ctx.beginPath();for(const [a,b] of selectedEdges){let p=screen(a),q=screen(b);if((p[0]<-5&&q[0]<-5)||(p[0]>w+5&&q[0]>w+5)||(p[1]<-5&&q[1]<-5)||(p[1]>h+5&&q[1]>h+5))continue;ctx.moveTo(...p);ctx.lineTo(...q)}ctx.strokeStyle='#fff';ctx.lineWidth=5;ctx.stroke();ctx.strokeStyle='#d4372f';ctx.lineWidth=2.5;ctx.stroke()}}
function inside(x,y,p){let inPoly=false;for(let i=0,j=p.length-1;i<p.length;j=i++){let a=p[i],b=p[j];if((a[1]>y)!=(b[1]>y)&&x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0])inPoly=!inPoly}return inPoly}
function hit(px,py){let x=(px-view.x)/view.scale,y=(view.y-py)/view.scale;for(let i=geometry.length-1;i>=0;i--)if(inside(x,y,geometry[i].xy))return geometry[i];return null}
async function updateMap(){let m=$('metric').value, codes=groups[m]||[m], yr=$('year').value,percent=$('percent').checked; if(!m)return;let v=await api(`/api/map?year=${yr}&codes=${codes.join(',')}&percent=${percent?1:0}&mode=${mode()}`);mapValues=v.values;draw()}
function clearSelection(){selected=null;selectedIds.clear();selectedEdges=[];selectedData=null;selectionRequest++;$('element').textContent=(mode()==='element'?'Select an ':'Select a ')+selectionLabel();$('selectiontools').hidden=true;$('selectedids').textContent='';$('goto').value='';$('periods').textContent='';$('values').innerHTML='';$('legend').innerHTML='';chartState=null;refreshSelectionContext();syncExportButtons();$('exportstatus').textContent='';$('charttip').textContent='Hover over the chart to inspect values.';let c=$('chart');c.getContext('2d').clearRect(0,0,c.width,c.height);draw()}
async function loadSelection(){let ids=[...selectedIds].sort((a,b)=>a-b),elementMode=mode()==='element',id=selected,request=++selectionRequest;syncExportButtons(true);$('exportstatus').textContent='';if(elementMode&&!ids.length||!elementMode&&id===null){clearSelection();return}let path=elementMode?(ids.length===1?'/api/element/'+ids[0]:'/api/elements?ids='+ids.join(',')):'/api/'+(mode()==='subbasin'?'subbasin':'region')+'/'+id;path+=(path.includes('?')?'&':'?')+'year='+$('year').value;let d=await api(path);if(request!==selectionRequest)return;d.selectionMode=elementMode?'element':mode();d.selectionKey=id;d.selectionIds=ids;selectedData=d;if(elementMode){$('element').textContent=ids.length===1?'Element '+ids[0]+' · '+d.region+' · '+d.acres.toFixed(1)+' acres':ids.length+' selected elements · '+d.acres.toFixed(1)+' acres';$('selectiontools').hidden=false;$('selectedids').textContent=ids.length===1?'Element '+ids[0]:ids.length+' elements: '+ids.slice(0,16).join(', ')+(ids.length>16?' …':'');$('goto').value=ids.length===1?String(ids[0]):''}else{$('element').textContent=(mode()==='subbasin'?'Subbasin '+d.name:'Subregion '+id)+' · '+d.count+' elements · '+d.acres.toFixed(1)+' acres';$('selectiontools').hidden=true;$('goto').value=mode()==='subbasin'?d.name:id}renderDetail();refreshSelectionContext();draw()}
async function select(id,append=false){if(mode()==='element'){if(append){if(selectedIds.has(id))selectedIds.delete(id);else if(selectedIds.size<500)selectedIds.add(id);else{alert('Select at most 500 elements.');return}}else{selectedIds.clear();selectedIds.add(id)}selected=null;selectedEdges=[]}else{selected=id;selectedIds.clear();selectedEdges=makeSelectionEdges(id)}draw();await loadSelection()}
function recordForDay(records,t){return records.find(r=>r.effective>t)}
function chartRows(data,groupsNow){
  let start=`${manifest.start_year}-01-01`,sources=[...new Set(groupsNow.flatMap(g=>g.codes.map(c=>Object.keys(manifest.codes).find(s=>manifest.codes[s].includes(c)))))];
  let end=sources.length?sources.map(s=>data.series[s].at(-1).effective).sort().at(-1):manifest.coverage_end;
  let dates=[start,...sources.flatMap(s=>data.series[s].map(r=>r.effective)).filter(t=>t>start&&t<=end)];
  return [...new Set(dates)].sort().map(t=>{let row={date:t};for(const g of groupsNow){let total=0;for(const c of g.codes){let source=Object.keys(manifest.codes).find(s=>manifest.codes[s].includes(c)),rec=recordForDay(data.series[source],t);if(!rec){total=null;break}total+=rec.values[manifest.codes[source].indexOf(c)]}row[g.name]=total}return row});
}
function displayDate(t){return new Date(t+'T00:00:00Z').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric',timeZone:'UTC'})}
function renderDetail(){if(!selectedData)return;let d=selectedData,yr=+$('year').value;
  let periods=Object.entries(d.periods).map(([s,p])=>`${s}: ${p.start?'from '+p.start:'before the first endpoint'} through ${p.raw}`).join('<br>');
  $('periods').innerHTML=`<b>Input intervals covering July 1, ${yr}</b><br>${periods}`;$('valuedate').textContent=`Areas on July 1, ${yr}`;
  let codes=active(),groupsNow=grouped(codes),rows=chartRows(d,groupsNow),t=`${yr}-07-01`,now={};
  for(const g of groupsNow){now[g.name]=0;for(const c of g.codes){let s=Object.keys(manifest.codes).find(k=>manifest.codes[k].includes(c)),rec=recordForDay(d.series[s],t);if(!rec){now[g.name]=null;break}now[g.name]+=rec.values[manifest.codes[s].indexOf(c)]}}
  $('values').innerHTML='<tr><th>Category</th><th>Acres</th><th>% of selected area</th></tr>'+groupsNow.map(g=>`<tr><td>${g.name}</td><td>${now[g.name]===null?'No input coverage':now[g.name].toFixed(2)}</td><td>${now[g.name]===null?'—':(100*now[g.name]/d.acres).toFixed(1)+'%'}</td></tr>`).join('');
  $('legend').innerHTML=groupsNow.map((g,i)=>`<span style="display:inline-block;margin:4px;color:${seriesColor(g)}">● ${g.name}</span>`).join('');chart(rows,groupsNow,t,d.acres);syncExportButtons()
}
function chart(rows,groupsNow,selectedDate,areaAcres){chartState={rows,groupsNow,selectedDate,areaAcres};paintChart();chartReadout(selectedDate,'Map date')}
function chartReadout(t,heading){if(!chartState)return;let {rows,groupsNow,areaAcres}=chartState,r=rows.filter(row=>row.date<=t).at(-1)||rows[0],tip=$('charttip');if(!r||!groupsNow.length){tip.textContent='Choose chart categories to inspect values.';return}tip.innerHTML='<b>'+heading+': '+displayDate(t)+'</b><br>'+groupsNow.map((g,k)=>({name:g.name,color:seriesColor(g),value:r[g.name],index:k})).sort((a,b)=>(b.value??-1)-(a.value??-1)||a.index-b.index).map(item=>`<span style="color:${item.color}">●</span> ${item.name}: `+(item.value===null?'No input coverage':`${item.value.toLocaleString(undefined,{maximumFractionDigits:2})} acres (${areaAcres>0?(100*item.value/areaAcres).toLocaleString(undefined,{minimumFractionDigits:1,maximumFractionDigits:1}):'—'}% of ${selectionLabel()} area)`)).join('<br>')}
function paintChart(hoverDate=null,output=null){let state=output?.state||chartState;if(!state)return;let {rows,groupsNow,selectedDate}=state,c=output?.canvas||$('chart'),dpr=output?.dpr||devicePixelRatio||1,w=output?.width||c.clientWidth,h=output?.height||c.clientHeight;c.width=Math.round(w*dpr);c.height=Math.round(h*dpr);let ctx=c.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle='#fff';ctx.fillRect(0,0,w,h);let left=58,right=12,top=18,bottom=30,plotW=w-left-right,plotH=h-top-bottom,peak=Math.max(1,...rows.flatMap(r=>groupsNow.map(g=>r[g.name]||0))),unit=10**Math.floor(Math.log10(peak)),step=[1,2,5,10].map(n=>n*unit/2).find(v=>v*4>=peak)||unit*5,max=step*4,fmt=v=>Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1}).format(v);ctx.font='11px system-ui';ctx.textAlign='right';for(let i=0;i<=4;i++){let yy=h-bottom-i*plotH/4;ctx.strokeStyle=i?'#e1e7eb':'#aabac3';ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(w-right,yy);ctx.stroke();ctx.fillStyle='#4c626b';ctx.fillText(fmt(i*step),left-7,yy+4)}ctx.textAlign='left';ctx.fillStyle='#4c626b';ctx.fillText('acres',4,11);if(rows.length<2||plotW<=0)return;let start=Date.parse(rows[0].date),end=Date.parse(rows.at(-1).date),x=t=>left+(Date.parse(t)-start)/(end-start)*plotW,y=v=>h-bottom-v/max*plotH;let first=+rows[0].date.slice(0,4),last=+rows.at(-1).date.slice(0,4),gap=last-first>16?5:2;for(let yr=Math.ceil(first/gap)*gap;yr<=last;yr+=gap){let xx=x(yr+'-01-01');ctx.fillStyle='#4c626b';ctx.textAlign='center';ctx.fillText(yr,xx,h-8)}ctx.strokeStyle='#8899a0';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(x(selectedDate),top);ctx.lineTo(x(selectedDate),h-bottom);ctx.stroke();ctx.setLineDash([]);groupsNow.forEach((g,k)=>{ctx.strokeStyle=seriesColor(g);ctx.lineWidth=2.3;ctx.beginPath();rows.forEach((r,i)=>{let xx=x(r.date),v=r[g.name],prev=i?rows[i-1][g.name]:null;if(prev!==null)ctx.lineTo(xx,y(prev));if(v!==null){if(prev===null)ctx.moveTo(xx,y(v));else ctx.lineTo(xx,y(v))}});ctx.stroke()});if(hoverDate){let r=rows.filter(row=>row.date<=hoverDate).at(-1)||rows[0],xx=x(hoverDate);ctx.strokeStyle='#182836';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(xx,top);ctx.lineTo(xx,h-bottom);ctx.stroke();groupsNow.forEach((g,k)=>{if(r[g.name]===null)return;ctx.beginPath();ctx.arc(xx,y(r[g.name]),3.5,0,2*Math.PI);ctx.fillStyle=seriesColor(g);ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=1;ctx.stroke()})}}
function hoverChart(ev){if(!chartState||chartState.rows.length<2)return;let c=$('chart'),w=c.clientWidth,left=58,right=12,px=Math.max(left,Math.min(w-right,ev.offsetX)),rows=chartState.rows,start=Date.parse(rows[0].date),end=Date.parse(rows.at(-1).date),dateValue=start+(px-left)/(w-left-right)*(end-start),hoverDate=new Date(Math.min(dateValue,end-1)).toISOString().slice(0,10);chartReadout(hoverDate,'Chart cursor');paintChart(hoverDate)}
async function init(){manifest=applySubbasinNames(await api('/api/manifest'));geometry=await api('/api/geometry');$('status').textContent=`Land use input data · ${manifest.start_year}–${manifest.last_year}`;for(let y=manifest.start_year;y<=manifest.last_year;y++)$('year').add(new Option(y,y));$('year').value=String(manifest.last_year);if(!Object.keys(manifest.subbasins||{}).length){$('mode').querySelector('[value=subbasin]').disabled=true;$('subbasinboundaries').disabled=true}groups['All nonponded crops']=manifest.codes.nonponded||[];groups['Total irrigated agriculture']=[...groups['All nonponded crops'].filter(c=>c!=='ID'),...groups['All rice']];let metric=$('metric');for(const g of ['Total irrigated agriculture','All nonponded crops','All rice','All refuge','Native + riparian vegetation'])metric.add(new Option(g,g));for(const [source,codes] of Object.entries(manifest.codes)){let fs=document.createElement('details');fs.className='category-section';let summary=document.createElement('summary');summary.textContent={nonponded:'Nonponded crops',ponded:'Rice and refuge',native:'Native vegetation',urban:'Urban'}[source]||source;fs.dataset.title=summary.textContent;fs.append(summary);for(const c of codes){metric.add(new Option(manifest.labels[c],c));let l=document.createElement('label'),box=document.createElement('input');box.type='checkbox';box.className='cat';box.value=c;box.checked=['RI_FL','RI_NFL','RI_NDC','RF_SL','RF_PR','NV','RV','U'].includes(c);l.append(box,' '+manifest.labels[c]);fs.append(l)}$('categories').append(fs)}metric.value='All rice';syncChart();$('datemethod').textContent=manifest.date_note;initGeometry();initSubbasinReference();updateGoToOptions();$('exportcsv').onclick=exportCsv;$('exportpng').onclick=exportChart;$('exportscope').onchange=()=>syncExportButtons();await updateMap();$('year').onchange=async()=>{await updateMap();if(selected!==null||selectedIds.size)await loadSelection()};$('mode').onchange=async()=>{setBoxSelect(false);$('boxselect').disabled=mode()!=='element';clearSelection();$('subbasinreference').hidden=mode()!=='subbasin';refreshSelectionContext();updateGoToOptions();await updateMap()};metric.onchange=()=>{syncChart();updateMap()};$('percent').onchange=updateMap;$('syncchart').onchange=syncChart;$('subbasinboundaries').onchange=draw;$('aerial').onchange=()=>{$('attribution').style.display=$('aerial').checked?'block':'none';draw()};$('opacity').oninput=()=>{$('opacityValue').textContent=$('opacity').value+'%';draw()};$('boundaries').onchange=draw;$('chart').onpointermove=hoverChart;$('chart').onpointerleave=()=>{paintChart()};document.querySelectorAll('.cat,#nonpondedgroup,#rice,#refuge,#native').forEach(x=>x.onchange=()=>{refreshCategoryCounts();renderDetail()});let openBeforeSearch=null;$('categorySearch').oninput=()=>{let q=$('categorySearch').value.trim().toLowerCase(),sections=[...document.querySelectorAll('.category-section')];if(q&&!openBeforeSearch)openBeforeSearch=sections.map(x=>x.open);sections.forEach((section,i)=>{let matches=0;section.querySelectorAll('label').forEach(l=>{let show=!q||l.textContent.toLowerCase().includes(q);l.hidden=!show;if(show)matches++});section.hidden=!!q&&!matches;section.open=q?!!matches:(openBeforeSearch?openBeforeSearch[i]:section.open)});if(!q)openBeforeSearch=null};$('selectall').onclick=()=>{document.querySelectorAll('.cat').forEach(x=>x.checked=true);refreshCategoryCounts();renderDetail()};$('clear').onclick=()=>{document.querySelectorAll('.cat').forEach(x=>x.checked=false);refreshCategoryCounts();renderDetail()};$('go').onclick=()=>{let raw=$('goto').value.trim(),match=mode()==='subbasin'?Object.entries(manifest.subbasins||{}).find(([id,name])=>name===raw||manifest.subbasin_ids?.[id]===raw):null,id=match?+match[0]:Number(raw);if(raw!==''&&Number.isInteger(id)&&geometry.some(g=>featureId(g)===id)){let matches=geometry.filter(g=>featureId(g)===id),pts=matches.flatMap(g=>g.xy),bounds=[Infinity,Infinity,-Infinity,-Infinity];for(const p of pts){bounds[0]=Math.min(bounds[0],p[0]);bounds[1]=Math.min(bounds[1],p[1]);bounds[2]=Math.max(bounds[2],p[0]);bounds[3]=Math.max(bounds[3],p[1])}let center=[(bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2];if(mode()!=='element')view.scale=Math.min(($('map').clientWidth-60)/(bounds[2]-bounds[0]),($('map').clientHeight-60)/(bounds[3]-bounds[1]));view.x=$('map').clientWidth/2-center[0]*view.scale;view.y=$('map').clientHeight/2+center[1]*view.scale;select(id)}else alert('Selection not found')};$('zoomin').onclick=()=>{let c=$('map');zoomAt(1.5,c.clientWidth/2,c.clientHeight/2)};$('zoomout').onclick=()=>{let c=$('map');zoomAt(1/1.5,c.clientWidth/2,c.clientHeight/2)};$('resetzoom').onclick=fit;$('boxzoom').onclick=()=>setBoxZoom(!boxZoom);$('boxselect').onclick=()=>setBoxSelect(!boxSelect);$('clearselection').onclick=clearSelection;let c=$('map'),drag=null;c.onpointerdown=e=>{if(e.button!==0)return;if(boxZoom||boxSelect){drag={type:boxSelect?'selectbox':'box',start:[e.offsetX,e.offsetY],append:e.shiftKey};showZoomBox(drag.start,drag.start)}else drag=[e.offsetX,e.offsetY,view.x,view.y,false];c.setPointerCapture(e.pointerId)};c.onpointermove=e=>{if(drag){if(drag.type==='box'||drag.type==='selectbox'){showZoomBox(drag.start,[e.offsetX,e.offsetY])}else{view.x=drag[2]+e.offsetX-drag[0];view.y=drag[3]+e.offsetY-drag[1];if(Math.hypot(e.offsetX-drag[0],e.offsetY-drag[1])>3)drag[4]=true;draw()}}else if(!boxZoom&&!boxSelect){let g=hit(e.offsetX,e.offsetY);hovered=g?featureId(g):null;$('tooltip').style.display=g?'block':'none';$('tooltip').textContent=g?`${mode()==='subbasin'?'Subbasin '+manifest.subbasins[g.subbasin]:mode()==='region'?'Subregion '+g.region:'Element '+g.id}: ${(mapValues[featureId(g)]||0).toFixed(1)} ${$('percent').checked?'%':'acres'}`:'';$('tooltip').style.left=e.offsetX+12+'px';$('tooltip').style.top=e.offsetY+12+'px';draw()}};c.onpointerup=e=>{if(drag?.type==='selectbox'){let start=drag.start,append=drag.append;drag=null;if(Math.abs(e.offsetX-start[0])>=5&&Math.abs(e.offsetY-start[1])>=5){setBoxSelect(false);selectElementsInBox(start,[e.offsetX,e.offsetY],append)}else $('zoomrectangle').style.display='none';return}if(drag?.type==='box'){let dx=Math.abs(e.offsetX-drag.start[0]),dy=Math.abs(e.offsetY-drag.start[1]);if(dx>=10&&dy>=10){let cx=(drag.start[0]+e.offsetX)/2,cy=(drag.start[1]+e.offsetY)/2,factor=Math.min(c.clientWidth/dx,c.clientHeight/dy);view.x=c.clientWidth/2-(cx-view.x)*factor;view.y=c.clientHeight/2-(cy-view.y)*factor;view.scale*=factor;setBoxZoom(false)}else $('zoomrectangle').style.display='none';drag=null;return}if(drag&&!drag[4]){let g=hit(e.offsetX,e.offsetY);if(g)select(featureId(g),mode()==='element'&&e.shiftKey)}drag=null};c.onpointercancel=()=>{drag=null;$('zoomrectangle').style.display='none'};window.addEventListener('keydown',e=>{if(e.key==='Escape'&&(boxZoom||boxSelect)){drag=null;setBoxZoom(false);setBoxSelect(false)}});c.onwheel=e=>{e.preventDefault();if(drag)return;let s=Math.exp(-e.deltaY*.001);zoomAt(s,e.offsetX,e.offsetY)};window.onresize=draw}
init().catch(e=>{$('status').textContent='Error: '+e.message;console.error(e)});
</script></html>'''

from model_configuration import prepare_template
HTML = prepare_template(HTML)

def serve(args):
    out=Path(args.output); manifest=reconcile_dates(json.loads((out/'manifest.json').read_text()))
    db=out/'landuse.sqlite'
    @lru_cache(maxsize=100)
    def chosen(year):
        t=f'{year}-07-01'; found={}
        for s,ds in manifest['dates'].items():
            end=period_end_for_day(ds,t)
            if not end or not manifest['start_year']<=year<=manifest['last_year']: raise ValueError(f'No {s} input coverage for {t}')
            found[s]=end
        return found
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            u=urlparse(self.path);q=parse_qs(u.query)
            try:
                if u.path=='/': return self.send_html(HTML)
                if u.path=='/api/manifest': return self.send_json(manifest)
                conn=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
                try:
                    if u.path=='/api/geometry':
                        return self.send_json([{'id':i,'region':r,'subbasin':b,'xy':json.loads(xy)} for i,r,b,xy in conn.execute('SELECT id,region,subbasin,xy FROM elements')])
                    if u.path=='/api/map':
                        year=int(q['year'][0]); active=chosen(year)
                        codes=q['codes'][0].split(',');pct=q.get('percent',['0'])[0]=='1'
                        mode=q.get('mode',['element'])[0]
                        if mode not in ('element','region','subbasin'): raise ValueError('Unknown mode')
                        if mode=='subbasin' and not manifest['subbasins']: raise ValueError('No subbasin zone file in snapshot')
                        if not codes or any(c not in [c for cs in manifest['codes'].values() for c in cs] for c in codes): raise ValueError('Unknown map category')
                        result={}
                        group_field={'element':'id','region':'region','subbasin':'subbasin'}[mode]
                        acres=dict(conn.execute(f'SELECT {group_field},SUM(acres) FROM elements GROUP BY {group_field}')) if pct else {}
                        for s,d in active.items():
                            indices=[manifest['codes'][s].index(c) for c in codes if c in manifest['codes'][s]]
                            if not indices: continue
                            n=len(manifest['codes'][s]); fmt='<' + ('f' if mode=='element' else 'd')*n
                            table,field={'element':('areas','element'),'region':('region_areas','region'),'subbasin':('subbasin_areas','subbasin')}[mode]
                            for eid,blob in conn.execute(f'SELECT {field},vals FROM {table} WHERE source=? AND effective=?',(s,d)):
                                v=struct.unpack(fmt,blob)
                                result[eid]=result.get(eid,0)+sum(v[i] for i in indices)
                        if pct:result={i:100*v/acres[i] if acres[i] else 0 for i,v in result.items()}
                        return self.send_json({'values':result,'effective':active})
                    if u.path=='/api/elements':
                        ids=sorted(set(int(x) for x in q['ids'][0].split(',')))
                        if not ids or len(ids)>500: raise ValueError('Select between 1 and 500 elements')
                        year=int(q['year'][0]);active=chosen(year)
                        placeholders=','.join('?' for _ in ids)
                        count,acres=conn.execute(f'SELECT COUNT(*),SUM(acres) FROM elements WHERE id IN ({placeholders})',ids).fetchone()
                        if count!=len(ids): raise ValueError('Unknown element in selection')
                        series={}
                        for s,codes in manifest['codes'].items():
                            n=len(codes);fmt='<'+'f'*n;totals={}
                            for d,blob in conn.execute(f'SELECT effective,vals FROM areas WHERE source=? AND element IN ({placeholders}) ORDER BY effective',(s,*ids)):
                                values=totals.setdefault(d,[0.0]*n)
                                for i,v in enumerate(struct.unpack(fmt,blob)):values[i]+=v
                            series[s]=[{'effective':d,'values':values} for d,values in sorted(totals.items())]
                        periods=input_periods(manifest,active)
                        return self.send_json({'ids':ids,'acres':acres,'count':count,'series':series,'periods':periods})
                    if u.path.startswith(('/api/element/','/api/region/','/api/subbasin/')):
                        eid=int(u.path.rsplit('/',1)[1]);year=int(q['year'][0]);active=chosen(year)
                        is_region=u.path.startswith('/api/region/')
                        is_subbasin=u.path.startswith('/api/subbasin/')
                        field='subbasin' if is_subbasin else 'region' if is_region else 'id'
                        row=conn.execute(f'SELECT MIN(region),SUM(acres),COUNT(*) FROM elements WHERE {field}=?',(eid,)).fetchone()
                        if not row or not row[2]: raise ValueError('Unknown selection')
                        series={}
                        for s in manifest['codes']:
                            n=len(manifest['codes'][s]);fmt='<' + ('f' if not (is_region or is_subbasin) else 'd')*n
                            table,key=('subbasin_areas','subbasin') if is_subbasin else ('region_areas','region') if is_region else ('areas','element')
                            series[s]=[{'effective':d,'values':struct.unpack(fmt,b)} for d,b in conn.execute(f'SELECT effective,vals FROM {table} WHERE source=? AND {key}=? ORDER BY effective',(s,eid))]
                        periods=input_periods(manifest,active)
                        return self.send_json({'id':eid,'region':f'Subregion {row[0]}','name':manifest['subbasins'].get(str(eid)) if is_subbasin else None,'acres':row[1],'count':row[2],'series':series,'periods':periods})
                    self.send_error(404)
                finally:conn.close()
            except (ValueError,KeyError,IndexError) as e:self.send_error(400,str(e))
        def send_json(self,obj):
            b=json.dumps(obj,separators=(',',':')).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
        def send_html(self,html):
            b=html.encode('utf-8');self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
        def send_file(self,path,mime):
            b=path.read_bytes();self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    url=f'http://127.0.0.1:{args.port}/'
    print('Open '+url+' (Ctrl+C to stop)',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()

STATIC_API = r'''
const staticCache=new Map();let staticGeometry=null,staticIndex=null,staticAreas=null;
async function staticFetch(path,kind='json'){
  if(!staticCache.has(path))staticCache.set(path,(async()=>{
    let response=await fetch(path);if(!response.ok)throw Error(`${path}: HTTP ${response.status}`);
    if(kind==='binary')return new Float32Array(await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer());
    if(kind==='gzip')return new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).json();
    return response.json();
  })());
  return staticCache.get(path);
}
function staticPeriods(year){let t=`${year}-07-01`,result={};for(const [source,dates] of Object.entries(manifest.dates)){
  let effective=dates.find(d=>d>t);if(!effective)throw Error(`No ${source} input coverage for ${t}`);let i=dates.indexOf(effective),raw=new Date(Date.parse(effective+'T00:00:00Z')-86400000).toISOString().slice(0,10)+' 24:00';
  result[source]={effective,raw,start:i?dates[i-1]:null};
}return result}
function staticSeries(raw){return Object.fromEntries(Object.entries(raw).map(([source,records])=>[source,records.map(([effective,values])=>({effective,values}))]))}
function staticGroupArea(mode,id){let g=staticAreas[mode].get(id);if(!g)throw Error('Unknown selection');return g}
async function api(path){
  let u=new URL(path,location.href),route=u.pathname;
  if(route==='/api/manifest')return staticFetch('./manifest.json');
  if(route==='/api/geometry'){
    staticGeometry=await staticFetch('./geometry.json');
    staticIndex=new Map(staticGeometry.map((g,i)=>[g.id,i]));
    staticAreas={element:new Map(),region:new Map(),subbasin:new Map()};
    for(const g of staticGeometry)for(const [mode,id] of [['element',g.id],['region',g.region],['subbasin',g.subbasin]]){
      let a=staticAreas[mode].get(id)||{acres:0,count:0,region:g.region};a.acres+=g.acres;a.count++;staticAreas[mode].set(id,a);
    }
    return staticGeometry;
  }
  if(route==='/api/map'){
    let year=u.searchParams.get('year'),mode=u.searchParams.get('mode'),codes=u.searchParams.get('codes').split(','),pct=u.searchParams.get('percent')==='1';
    let data=await staticFetch(`./years/${year}.bin.gz`,'binary'),offsets=new Map(),position=0;
    for(const sourceCodes of Object.values(manifest.codes))for(const code of sourceCodes)offsets.set(code,position++);
    let columns=codes.map(c=>offsets.get(c));if(columns.some(c=>c===undefined))throw Error('Unknown category');
    let values={};for(let i=0;i<staticGeometry.length;i++){
      let g=staticGeometry[i],id=mode==='element'?g.id:mode==='region'?g.region:g.subbasin,sum=0;
      for(const col of columns)sum+=data[i*position+col];values[id]=(values[id]||0)+sum;
    }
    if(pct)for(const id of Object.keys(values))values[id]=100*values[id]/staticGroupArea(mode,+id).acres;
    return {values};
  }
  if(route==='/api/elements'){
    let ids=[...new Set(u.searchParams.get('ids').split(',').map(Number))].sort((a,b)=>a-b),year=u.searchParams.get('year');
    if(!ids.length||ids.length>500||ids.some(id=>!staticIndex.has(id)))throw Error('Unknown or oversized element selection');
    let shards=[...new Set(ids.map(id=>Math.floor(staticIndex.get(id)/128).toString().padStart(4,'0')))];
    let loaded=await Promise.all(shards.map(async shard=>[shard,await staticFetch(`./history/elements/${shard}.json.gz`,'gzip')]));
    let byShard=new Map(loaded),totals=Object.fromEntries(Object.keys(manifest.codes).map(s=>[s,new Map()]));
    for(const id of ids){
      let shard=Math.floor(staticIndex.get(id)/128).toString().padStart(4,'0'),raw=byShard.get(shard)[id];
      if(!raw)throw Error('No history for element '+id);
      for(const [source,records] of Object.entries(raw))for(const [effective,values] of records){
        let sum=totals[source].get(effective);
        if(!sum){sum=new Array(values.length).fill(0);totals[source].set(effective,sum)}
        values.forEach((value,i)=>sum[i]+=value);
      }
    }
    let series=Object.fromEntries(Object.entries(totals).map(([source,dates])=>[source,[...dates].sort(([a],[b])=>a.localeCompare(b)).map(([effective,values])=>({effective,values}))]));
    return {ids,acres:ids.reduce((sum,id)=>sum+staticGroupArea('element',id).acres,0),count:ids.length,series,periods:staticPeriods(year)};
  }
  let match=route.match(/^\/api\/(element|region|subbasin)\/(-?\d+)$/);
  if(match){
    let mode=match[1],id=+match[2],year=u.searchParams.get('year'),info=staticGroupArea(mode,id),raw;
    if(mode==='element'){
      let shard=Math.floor(staticIndex.get(id)/128).toString().padStart(4,'0');raw=(await staticFetch(`./history/elements/${shard}.json.gz`,'gzip'))[id];
    }else raw=(await staticFetch(`./history/${mode}.json.gz`,'gzip'))[id];
    if(!raw)throw Error('No history for '+mode+' '+id);
    return {id,region:`Subregion ${info.region}`,name:mode==='subbasin'?manifest.subbasins[id]:null,acres:info.acres,count:info.count,series:staticSeries(raw),periods:staticPeriods(year)};
  }
  throw Error('Unknown data path: '+path);
}
'''

def export_web(args):
    """Export a read-only static site; viewers need a web server, not Python."""
    source, out = Path(args.output), Path(args.web_output)
    manifest = reconcile_dates(json.loads((source/'manifest.json').read_text()))
    out.mkdir(parents=True, exist_ok=True)
    for generated in ('years','history'):
        if (out/generated).exists():shutil.rmtree(out/generated)
    (out/'years').mkdir(exist_ok=True)
    (out/'history'/'elements').mkdir(parents=True, exist_ok=True)
    db = source/'landuse.sqlite'
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    elements = list(conn.execute('SELECT id,region,subbasin,acres,xy FROM elements ORDER BY id'))
    ids = {eid:i for i,(eid,*_) in enumerate(elements)}
    geometry = [{'id':eid,'region':r,'subbasin':b,'acres':a,'xy':json.loads(xy)} for eid,r,b,a,xy in elements]
    (out/'geometry.json').write_text(json.dumps(geometry,separators=(',',':')),encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps(manifest,separators=(',',':')),encoding='utf-8')
    codes = list(manifest['codes']); width = sum(len(manifest['codes'][s]) for s in codes)
    offsets = {}; n=0
    for s in codes: offsets[s]=n; n+=len(manifest['codes'][s])
    for year in range(manifest['start_year'],manifest['last_year']+1):
        t=f'{year}-07-01'; data=bytearray(len(elements)*width*4)
        for s in codes:
            effective=period_end_for_day(manifest['dates'][s],t)
            count=0
            for eid,blob in conn.execute('SELECT element,vals FROM areas WHERE source=? AND effective=?',(s,effective)):
                i=ids[eid]; start=(i*width+offsets[s])*4
                data[start:start+len(blob)]=blob;count+=1
            if count!=len(elements): raise ValueError(f'{s} {effective}: {count} elements')
        with gzip.open(out/'years'/f'{year}.bin.gz','wb',compresslevel=6) as f:f.write(data)
    print(f'Exported {manifest["start_year"]}–{manifest["last_year"]} annual maps',flush=True)
    for table,mode,key in [('region_areas','region','region'),('subbasin_areas','subbasin','subbasin')]:
        if mode=='subbasin' and not manifest.get('subbasins'):continue
        histories={}
        for source,effective,gid,blob in conn.execute(f'SELECT source,effective,{key},vals FROM {table} ORDER BY {key},source,effective'):
            vals=struct.unpack('<'+'d'*len(manifest['codes'][source]),blob)
            histories.setdefault(gid,{}).setdefault(source,[]).append([effective,vals])
        with gzip.open(out/'history'/f'{mode}.json.gz','wt',encoding='utf-8',compresslevel=6) as f:
            json.dump(histories,f,separators=(',',':'))
    shard={}; current=None
    def flush(shard_id):
        if shard_id is None:return
        with gzip.open(out/'history'/'elements'/f'{shard_id:04d}.json.gz','wt',encoding='utf-8',compresslevel=6) as f:
            json.dump(shard,f,separators=(',',':'))
    for eid,source,effective,blob in conn.execute('SELECT element,source,effective,vals FROM areas ORDER BY element,source,effective'):
        shard_id=ids[eid]//128
        if current!=shard_id:
            flush(current);shard={};current=shard_id
        vals=struct.unpack('<'+'f'*len(manifest['codes'][source]),blob)
        shard.setdefault(eid,{}).setdefault(source,[]).append([effective,vals])
    flush(current);conn.close()
    old="async function api(path){let r=await fetch(path);if(!r.ok)throw Error(await r.text());return r.json()}"
    if HTML.count(old)!=1:raise ValueError('Static API insertion point changed')
    (out/'index.html').write_text(HTML.replace(old,STATIC_API),encoding='utf-8')
    (out/'README.txt').write_text('C2VSimFG static land use explorer. Upload this directory to an HTTPS static web host, preserving every relative path. Serve index.html at its root. The site contains model input areas; restrict access to authorized viewers. To test locally: python -m http.server 8000 --directory '+str(out)+'\n',encoding='utf-8')
    print(f'Static browser app written to {out}',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    b=sub.add_parser('build');b.add_argument('--input',required=True);b.add_argument('--output',required=True);b.add_argument('--start-year',type=int,default=2000);b.add_argument('--utm-zone',type=int,default=10,help='Input node UTM zone (default 10N for C2VSimFG)')
    from iwfm_build import add_build_options
    add_build_options(b)
    s=sub.add_parser('serve');s.add_argument('--output',required=True);s.add_argument('--port',type=int,default=8765)
    e=sub.add_parser('export-web');e.add_argument('--output',required=True,help='Built explorer snapshot');e.add_argument('--web-output',required=True,help='Destination static site directory')
    a=parser.parse_args(); {'build':build,'serve':serve,'export-web':export_web}[a.action](a)
