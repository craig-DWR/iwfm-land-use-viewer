#!/usr/bin/env python3
"""Export a single HTML viewer without losing timestamps or stored float32 acreages.
python export_landuse_standalone.py --data landuse_explorer --viewer c2vsim_landuse_explorer_naip.py --html landuse_standalone_naip.html
Requires Python standard library. Open output in a current Edge/Chrome/Firefox browser.
"""

import argparse, array, base64, gzip, importlib.util, json, sqlite3, sys
from pathlib import Path

API = r"""
const embeddedCache=new Map();let embeddedGeometry,embeddedIndex,embeddedAreas;
async function embeddedBytes(key){
  if(!embeddedCache.has(key))embeddedCache.set(key,(async()=>{
    if(typeof DecompressionStream==='undefined')throw Error('This viewer needs a current browser with gzip decompression support.');
    const encoded=document.getElementById('data-'+key).textContent.trim(),raw=atob(encoded),bytes=new Uint8Array(raw.length);
    for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
    return new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  })());return embeddedCache.get(key);
}
async function embeddedSource(source){
  const [dictionary,indices]=await Promise.all([embeddedBytes(source+'-values'),embeddedBytes(source+'-indices')]);
  return {values:new Float32Array(dictionary),indices:new Uint32Array(indices),columns:manifest.codes[source].length,dates:manifest.dates[source]};
}
function embeddedPeriods(year){
  year=Number(year);if(!Number.isInteger(year)||year<manifest.start_year||year>manifest.last_year)throw Error('Year outside input coverage');
  const day=`${year}-07-01`,result={};
  for(const [source,dates] of Object.entries(manifest.dates)){
    const effective=dates.find(d=>d>day);if(!effective)throw Error('No input coverage for '+source);
    const i=dates.indexOf(effective),raw=new Date(Date.parse(effective+'T00:00:00Z')-86400000).toISOString().slice(0,10)+' 24:00';
    result[source]={effective,raw,start:i?dates[i-1]:null};
  }return result;
}
async function embeddedDetail(ids,year){
  const periods=embeddedPeriods(year),series={};
  for(const source of Object.keys(manifest.codes)){
    const d=await embeddedSource(source),n=d.columns,N=embeddedGeometry.length;
    series[source]=d.dates.map((effective,t)=>{
      const values=new Array(n).fill(0);
      for(const id of ids){const offset=d.indices[t*N+embeddedIndex.get(id)]*n;for(let k=0;k<n;k++)values[k]+=d.values[offset+k]}
      return {effective,values};
    });
  }
  return {ids,acres:ids.reduce((sum,id)=>sum+embeddedAreas.element.get(id).acres,0),count:ids.length,series,periods};
}
async function api(path){
  const u=new URL(path,'https://standalone.invalid'),route=u.pathname;
  if(route==='/api/manifest')return JSON.parse(document.getElementById('data-manifest').textContent);
  if(route==='/api/geometry'){
    embeddedGeometry=JSON.parse(new TextDecoder().decode(await embeddedBytes('geometry')));
    embeddedIndex=new Map(embeddedGeometry.map((g,i)=>[g.id,i]));
    embeddedAreas={element:new Map(),region:new Map(),subbasin:new Map()};
    for(const g of embeddedGeometry)for(const [mode,id] of [['element',g.id],['region',g.region],['subbasin',g.subbasin]]){
      let a=embeddedAreas[mode].get(id)||{acres:0,count:0,region:g.region,ids:[]};a.acres+=g.acres;a.count++;a.ids.push(g.id);embeddedAreas[mode].set(id,a);
    }return embeddedGeometry;
  }
  if(route==='/api/map'){
    const periods=embeddedPeriods(u.searchParams.get('year')),mode=u.searchParams.get('mode')||'element',codes=u.searchParams.get('codes').split(','),pct=u.searchParams.get('percent')==='1',values={};
    if(!embeddedAreas[mode])throw Error('Unknown mode');
    const valid=new Set(Object.values(manifest.codes).flat());if(codes.some(c=>!valid.has(c)))throw Error('Unknown category');
    for(const source of Object.keys(manifest.codes)){
      const columns=codes.filter(c=>manifest.codes[source].includes(c)).map(c=>manifest.codes[source].indexOf(c));if(!columns.length)continue;
      const d=await embeddedSource(source),t=d.dates.indexOf(periods[source].effective),N=embeddedGeometry.length;
      for(let i=0;i<N;i++){
        const g=embeddedGeometry[i],id=mode==='element'?g.id:mode==='region'?g.region:g.subbasin,offset=d.indices[t*N+i]*d.columns;let sum=0;
        for(const k of columns)sum+=d.values[offset+k];values[id]=(values[id]||0)+sum;
      }
    }
    if(pct)for(const id of Object.keys(values)){const acres=embeddedAreas[mode].get(Number(id)).acres;values[id]=acres?100*values[id]/acres:0}
    return {values,effective:Object.fromEntries(Object.entries(periods).map(([s,p])=>[s,p.effective]))};
  }
  if(route==='/api/elements'){
    const ids=[...new Set(u.searchParams.get('ids').split(',').map(Number))].sort((a,b)=>a-b);
    if(!ids.length||ids.length>500||ids.some(id=>!embeddedIndex.has(id)))throw Error('Unknown or oversized selection');
    return embeddedDetail(ids,u.searchParams.get('year'));
  }
  const match=route.match(/^\/api\/(element|region|subbasin)\/(-?\d+)$/);
  if(match){
    const mode=match[1],id=Number(match[2]),info=embeddedAreas[mode].get(id);if(!info)throw Error('Unknown selection');
    const result=await embeddedDetail(info.ids,u.searchParams.get('year'));
    return {...result,id,region:`Subregion ${info.region}`,name:mode==='subbasin'?manifest.subbasins[id]:null};
  }
  throw Error('Unknown data path: '+path);
}
"""


def encode(b):
    return base64.b64encode(gzip.compress(b, compresslevel=9, mtime=0)).decode("ascii")


def export(data, viewer, html):
    """Embed retained input histories rather than replacing them with July 1 samples."""
    spec = importlib.util.spec_from_file_location("viewer_template", viewer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = module.reconcile_dates(json.loads((data / "manifest.json").read_text()))
    conn = sqlite3.connect(
        f"file:{(data / 'landuse.sqlite').resolve()}?mode=ro", uri=True
    )
    elements = conn.execute(
        "SELECT id,region,subbasin,acres,xy FROM elements ORDER BY id"
    ).fetchall()
    geometry = [
        dict(id=i, region=r, subbasin=b, acres=a, xy=json.loads(xy))
        for i, r, b, a, xy in elements
    ]
    ids = {e[0]: i for i, e in enumerate(elements)}
    N = len(elements)
    blocks = []
    blocks.append(
        '<script id="data-manifest" type="application/json">'
        + json.dumps(manifest, separators=(",", ":")).replace("<", "\\u003c")
        + "</script>"
    )

    def add(key, b):
        payload = encode(b)
        blocks.append(
            f'<script id="data-{key}" type="application/octet-stream">{payload}</script>'
        )
        return len(payload)

    add("geometry", json.dumps(geometry, separators=(",", ":")).encode())
    # Each source keeps its own endpoints; dictionary reuse changes storage, not coverage.
    for source, codes in manifest["codes"].items():
        dates = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT effective FROM areas WHERE source=? ORDER BY effective",
                (source,),
            )
        ]
        if dates != manifest["dates"][source]:
            raise ValueError("Manifest/database dates disagree: " + source)
        date_indices = {d: t for t, d in enumerate(dates)}
        indices = array.array("I", [0]) * (N * len(dates))
        dictionary = {}
        values = bytearray()
        seen = 0
        for date, eid, blob in conn.execute(
            "SELECT effective,element,vals FROM areas WHERE source=? ORDER BY effective,element",
            (source,),
        ):
            if len(blob) != 4 * len(codes):
                raise ValueError("Unexpected value size")
            index = dictionary.get(blob)
            if index is None:
                index = len(dictionary)
                dictionary[blob] = index
                values.extend(blob)
            indices[date_indices[date] * N + ids[eid]] = index
            seen += 1
        if seen != len(indices):
            raise ValueError("Missing source records")
        if sys.byteorder != "little":
            indices.byteswap()
        size = add(source + "-values", values) + add(
            source + "-indices", indices.tobytes()
        )
        print(
            f"{source}: {seen:,} records -> {len(dictionary):,} unique vectors; embedded {size / 1048576:.2f} MiB",
            flush=True,
        )
    conn.close()
    old = "async function api(path){let r=await fetch(path);if(!r.ok)throw Error(await r.text());return r.json()}"
    if module.HTML.count(old) != 1:
        raise ValueError("Unsupported viewer template")
    output = module.HTML.replace(old, API).replace(
        "<script>", "\n".join(blocks) + "\n<script>", 1
    )
    output = output.replace(
        "${manifest.count.toLocaleString()} elements · input snapshot ·",
        "Land use input data ·",
    )
    # Catch startup problems and explain them in the existing status line.
    output = output.replace(
        "init();",
        "init().catch(error=>{$('status').textContent='Unable to load viewer: '+error.message;console.error(error)});",
    )
    html.write_text(output, encoding="utf-8")
    print(f"Wrote {html}: {html.stat().st_size / 1048576:.2f} MiB", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--viewer", type=Path, required=True)
    p.add_argument("--html", type=Path, required=True)
    a = p.parse_args()
    export(a.data, a.viewer, a.html)
