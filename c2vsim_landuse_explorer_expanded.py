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
    "nonponded": (
        "NonPondedCrop_Area",
        [
            "GR",
            "CO",
            "SB",
            "CN",
            "DB",
            "SA",
            "FL",
            "AL",
            "PA",
            "TP",
            "TF",
            "CU",
            "OG",
            "PO",
            "TR",
            "AP",
            "OR",
            "CS",
            "VI",
            "ID",
        ],
    ),
    "ponded": ("PondedCrop_Area", ["RI_FL", "RI_NFL", "RI_NDC", "RF_SL", "RF_PR"]),
    "native": ("NativeVeg_Area", ["NV", "RV"]),
    "urban": ("Urban_Area", ["U"]),
}
LABELS = {
    "GR": "Grain",
    "CO": "Cotton",
    "SB": "Sugar beets",
    "CN": "Corn",
    "DB": "Dry beans",
    "SA": "Safflower",
    "FL": "Other field",
    "AL": "Alfalfa",
    "PA": "Pasture",
    "TP": "Processing tomatoes",
    "TF": "Fresh tomatoes",
    "CU": "Cucurbits",
    "OG": "Onions and garlic",
    "PO": "Potatoes",
    "TR": "Other truck",
    "AP": "Almonds and pistachios",
    "OR": "Other deciduous",
    "CS": "Citrus and subtropical",
    "VI": "Vineyards",
    "ID": "Idle",
    "RI_FL": "Rice: flooded decomposition",
    "RI_NFL": "Rice: nonflooded decomposition",
    "RI_NDC": "Rice: no decomposition",
    "RF_SL": "Seasonal refuge",
    "RF_PR": "Permanent refuge",
    "NV": "Native vegetation",
    "RV": "Riparian vegetation",
    "U": "Urban",
}
DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})_(\d{2}):(\d{2})$")


def datestamp(s):
    m = DATE_RE.match(s)
    if not m:
        raise ValueError(f"Invalid IWFM time: {s}")
    mm, dd, yy, hh, minute = map(int, m.groups())
    d = date(yy, mm, dd)
    # IWFM 24:00 closes the named day and is stored as the next day's midnight.
    if hh == 24 and minute == 0:
        d += timedelta(days=1)
    elif hh != 0 or minute != 0:
        raise ValueError(f"Unsupported daily time: {s}")
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
    # July 1 queries sources independently; maps stop at common coverage, not the longest chart.
    ends = [max(ds) for ds in manifest["dates"].values()]
    common_end = min(ends)
    last_day = date.fromisoformat(common_end) - timedelta(days=1)
    manifest["last_year"] = last_day.year - (last_day < date(last_day.year, 7, 1))
    if manifest["last_year"] < manifest["start_year"]:
        raise ValueError("Inputs do not cover July 1 of the selected start year")
    manifest["date_convention"] = "interval_end"
    manifest["coverage_end"] = common_end
    manifest["date_note"] = (
        "Display year Y is July 1 of Y. Each ITLN timestamp ends its block: "
        "values apply after the preceding timestamp through this timestamp. "
        "24:00 closes the stated day. No values are extended past the final block."
    )
    return manifest


def input_periods(manifest, active):
    periods = {}
    for source, end in active.items():
        dates = manifest["dates"][source]
        i = dates.index(end)
        periods[source] = {
            "effective": end,
            "start": dates[i - 1] if i else None,
            "raw": (date.fromisoformat(end) - timedelta(days=1)).isoformat() + " 24:00",
        }
    return periods


def choose(directory, stem):
    matches = [
        p
        for p in directory.iterdir()
        if re.search(r"(^|_)" + re.escape(stem) + r"($|[.(])", p.stem, re.I)
        and p.suffix.lower() in (".dat", ".zip")
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one {stem} DAT or ZIP in {directory}; found {[p.name for p in matches]}"
        )
    return matches[0]


@contextmanager
def lines(path):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            members = [n for n in z.namelist() if n.lower().endswith(".dat")]
            if len(members) != 1:
                raise ValueError(f"Expected one DAT in {path}")
            with z.open(members[0]) as f:
                yield (b.decode("utf-8", "replace") for b in f)
    else:
        with path.open(encoding="utf-8", errors="replace") as f:
            yield f


def noncomments(stream):
    for line in stream:
        s = line.strip()
        if (
            s
            and not re.match(r"^[Cc](?:\s|[*-]|$)", s)
            and not s.startswith(("#", "*"))
        ):
            yield s


def geometry(nodes_path, elements_path):
    # Native metre coordinates place the mesh; node FACT is for model units, not the basemap.
    with lines(nodes_path) as stream:
        rows = iter(noncomments(stream))
        n = int(next(rows).split("/")[0])
        next(rows)  # ND and FACT; coordinates remain in native units.
        nodes = {}
        for _ in range(n):
            v = next(rows).split()
            nodes[int(v[0])] = (float(v[1]), float(v[2]))
    with lines(elements_path) as stream:
        rows = iter(noncomments(stream))
        ne = int(next(rows).split("/")[0])
        next(rows)  # NE, NREGN
        # Subregion names carry /RNAME; element records have 6 integer fields.
        elements = []
        for row in rows:
            v = row.split()
            if len(v) != 6 or not all(x.lstrip("-").isdigit() for x in v):
                continue
            eid, *rest = map(int, v)
            ids, region = rest[:4], rest[4]
            xy = [nodes[i] for i in ids if i]
            if len(xy) < 3:
                raise ValueError(f"Element {eid} has fewer than 3 nodes")
            area = (
                abs(
                    sum(
                        xy[i][0] * xy[(i + 1) % len(xy)][1]
                        - xy[(i + 1) % len(xy)][0] * xy[i][1]
                        for i in range(len(xy))
                    )
                )
                / 2
            )
            elements.append((eid, region, area, xy))
    if len(elements) != ne or len({e[0] for e in elements}) != ne:
        raise ValueError("Element count or IDs inconsistent")
    return elements


def subbasins(path, valid_ids):
    """Read a horizontal-plane IWFM ZBudget zone definition and validate membership."""
    with lines(path) as stream:
        rows = iter(noncomments(stream))
        extent = int(next(rows).split("/")[0].strip())
        if extent != 1:
            raise ValueError(f"{path.name}: expected horizontal ZEXTENT=1")
        names = {}
        assignment = {}
        reading_elements = False
        for line in rows:
            fields = line.split("/")[0].split()
            if len(fields) < 2:
                continue
            if (
                not reading_elements
                and fields[0].lstrip("-").isdigit()
                and not fields[1].lstrip("-").isdigit()
            ):
                zone = int(fields[0])
                names[zone] = " ".join(fields[1:])
                continue
            if len(fields) == 2 and all(x.lstrip("-").isdigit() for x in fields):
                reading_elements = True
                eid, zone = map(int, fields)
                if eid not in valid_ids:
                    raise ValueError(f"{path.name}: unknown element {eid}")
                if eid in assignment:
                    raise ValueError(f"{path.name}: duplicate element {eid}")
                if zone not in names:
                    raise ValueError(f"{path.name}: zone {zone} has no name")
                assignment[eid] = zone
        if not names or not assignment:
            raise ValueError(f"{path.name}: no subbasin names or assignments")
    names[-99] = "Unassigned (ZoneBudget -99)"
    return names, {eid: assignment.get(eid, -99) for eid in valid_ids}


def hashfile(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def utm_to_web_mercator(easting, northing, zone=10):
    """NAD83 GRS80 UTM north to EPSG:3857; suitable for imagery overlay."""
    a = 6378137.0
    f = 1 / 298.257222101
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    k0 = 0.9996
    m = northing / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + 151 * e1**3 / 96 * math.sin(6 * mu)
        + 1097 * e1**4 / 512 * math.sin(8 * mu)
    )
    sinphi = math.sin(phi)
    cosphi = math.cos(phi)
    tanphi = math.tan(phi)
    n = a / math.sqrt(1 - e2 * sinphi**2)
    r = a * (1 - e2) / (1 - e2 * sinphi**2) ** 1.5
    t = tanphi**2
    c = ep2 * cosphi**2
    d = (easting - 500000) / (n * k0)
    lat = phi - (n * tanphi / r) * (
        d * d / 2
        - (5 + 3 * t + 10 * c - 4 * c * c - 9 * ep2) * d**4 / 24
        + (61 + 90 * t + 298 * c + 45 * t * t - 252 * ep2 - 3 * c * c) * d**6 / 720
    )
    lon = (
        math.radians(zone * 6 - 183)
        + (
            d
            - (1 + 2 * t + c) * d**3 / 6
            + (5 - 2 * c + 28 * t - 3 * c * c + 8 * ep2 + 24 * t * t) * d**5 / 120
        )
        / cosphi
    )
    if not -math.radians(85.05112878) < lat < math.radians(85.05112878):
        raise ValueError("Node outside Web Mercator extent; check input CRS")
    return [a * lon, a * math.log(math.tan(math.pi / 4 + lat / 2))]


def parse_area(conn, source, path, min_date, valid_ids):
    codes = SOURCES[source][1]
    inserted = 0
    dates = []
    last_date = None
    count = 0
    pending = []
    with lines(path) as stream:
        for lineno, line in enumerate(stream, 1):
            v = line.split()
            if (
                not v
                or re.match(r"^[Cc](?:\s|[*-]|$)", line.strip())
                or v[0].startswith(("#", "*"))
            ):
                continue
            if DATE_RE.match(v[0]):
                last_date = datestamp(v.pop(0))
                dates.append(last_date)
                count = 0
            if last_date is None:
                continue
            if len(v) != len(codes) + 1:
                raise ValueError(
                    f"{path.name}:{lineno}: expected element plus {len(codes)} areas, got {len(v)} fields"
                )
            eid = int(v[0])
            count += 1
            if eid not in valid_ids:
                raise ValueError(f"{path.name}:{lineno}: unknown element {eid}")
            if last_date >= min_date:
                vals = [float(x) for x in v[1:]]
                if not all(math.isfinite(x) for x in vals):
                    raise ValueError(f"{path.name}:{lineno}: nonfinite area")
                pending.append(
                    (source, last_date, eid, struct.pack("<" + "f" * len(vals), *vals))
                )
                if len(pending) >= 10000:
                    conn.executemany("INSERT INTO areas VALUES (?,?,?,?)", pending)
                    inserted += len(pending)
                    pending.clear()
        if pending:
            conn.executemany("INSERT INTO areas VALUES (?,?,?,?)", pending)
            inserted += len(pending)
    if not dates or len(dates) != len(set(dates)):
        raise ValueError(f"{path.name}: missing or duplicate dates")
    if dates != sorted(dates):
        raise ValueError(f"{path.name}: dates must increase")
    # Retain the prior annual endpoint to describe the initial interval.
    print(
        f"{source}: {len(dates)} update dates, {inserted:,} element records retained",
        flush=True,
    )
    return dates


def aggregate_groups(conn, group, source_codes=None):
    """Sum each source/date/category across member elements once per build."""
    table = "region_areas" if group == "region" else "subbasin_areas"
    conn.execute(
        f"CREATE TABLE {table}(source TEXT, effective TEXT, {group} INTEGER, vals BLOB, PRIMARY KEY(source,effective,{group})) WITHOUT ROWID"
    )
    for source, codes in (
        source_codes or {s: c for s, (_, c) in SOURCES.items()}
    ).items():
        n = len(codes)
        fmt = "<" + "f" * n
        for (effective,) in conn.execute(
            "SELECT DISTINCT effective FROM areas WHERE source=? ORDER BY effective",
            (source,),
        ):
            totals = {}
            for group_id, blob in conn.execute(
                f"SELECT e.{group},a.vals FROM areas a JOIN elements e ON e.id=a.element WHERE a.source=? AND a.effective=?",
                (source, effective),
            ):
                summed = totals.setdefault(group_id, [0.0] * n)
                for i, v in enumerate(struct.unpack(fmt, blob)):
                    summed[i] += v
            conn.executemany(
                f"INSERT INTO {table} VALUES (?,?,?,?)",
                [
                    (source, effective, r, struct.pack("<" + "d" * n, *vals))
                    for r, vals in totals.items()
                ],
            )
        conn.commit()
    print(f"Aggregated {group} time series", flush=True)


def build(args):
    indir, out = Path(args.input), Path(args.output)
    if (
        getattr(args, "model_config", None)
        and getattr(args, "reader", "legacy") != "iwfm-io"
    ):
        raise ValueError("--model-config requires --reader iwfm-io")
    adapter_build = None
    if getattr(args, "reader", "legacy") == "iwfm-io":
        from iwfm_build import AdapterBuild

        adapter_build = AdapterBuild(args)
    out.mkdir(parents=True, exist_ok=True)
    if adapter_build:
        paths = adapter_build.paths
    else:
        paths = {s: choose(indir, stem) for s, (stem, _) in SOURCES.items()}
        paths["nodes"] = choose(indir, "Nodes")
        paths["elements"] = choose(indir, "Elements")
    print("Reading geometry...", flush=True)
    elems = geometry(paths["nodes"], paths["elements"])
    zones = [
        p
        for p in indir.iterdir()
        if p.suffix.lower() in (".dat", ".zip")
        and ("zbudget" in p.stem.lower() or "zonedef" in p.stem.lower())
    ]
    if len(zones) > 1:
        raise ValueError(
            f"Expected at most one ZoneBudget zone definition; found {[p.name for p in zones]}"
        )
    zone_names, zone_by_element = (
        subbasins(zones[0], {e[0] for e in elems}) if zones else ({}, {})
    )
    if zones:
        paths["subbasins"] = zones[0]
    db = out / "landuse.sqlite"
    db.unlink(missing_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(
        "PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; CREATE TABLE areas(source TEXT, effective TEXT, element INTEGER, vals BLOB, PRIMARY KEY(source,effective,element)) WITHOUT ROWID; CREATE TABLE elements(id INTEGER PRIMARY KEY, region INTEGER, subbasin INTEGER, acres REAL, xy TEXT);"
    )
    conn.executemany(
        "INSERT INTO elements VALUES (?,?,?,?,?)",
        [
            (
                i,
                r,
                zone_by_element.get(i),
                a / 4046.8564224,
                json.dumps(
                    [utm_to_web_mercator(x, y, args.utm_zone) for x, y in xy],
                    separators=(",", ":"),
                ),
            )
            for i, r, a, xy in elems
        ],
    )
    valid = {e[0] for e in elems}
    all_dates = {}
    configured = (
        adapter_build is not None and getattr(adapter_build, "config", None) is not None
    )
    for s in adapter_build.adapter.sources if configured else SOURCES:
        # Keep the preceding annual block, including March/September 1999.
        all_dates[s] = (
            adapter_build.parse_area(
                conn, s, f"{args.start_year - 1}-01-01", valid, SOURCES[s][1]
            )
            if adapter_build
            else parse_area(conn, s, paths[s], f"{args.start_year - 1}-01-01", valid)
        )
        retained = [d for d in all_dates[s] if d >= f"{args.start_year - 1}-01-01"]
        for d in retained:
            count = conn.execute(
                "SELECT COUNT(*) FROM areas WHERE source=? AND effective=?", (s, d)
            ).fetchone()[0]
            if count != len(elems):
                raise ValueError(f"{s} {d}: {count} rows, expected {len(elems)}")
        conn.commit()
    codes = (
        adapter_build.codes if configured else {s: c for s, (_, c) in SOURCES.items()}
    )
    aggregate_groups(conn, "region", codes)
    if zones:
        aggregate_groups(conn, "subbasin", codes)
    max_year = 0  # Set from the final interval endpoints below.
    manifest = {
        "start_year": args.start_year,
        "last_year": max_year,
        "count": len(elems),
        "utm_zone": args.utm_zone,
        "map_crs": "EPSG:3857",
        "sources": {
            s: {"file": p.name, "sha256": hashfile(p)} for s, p in paths.items()
        },
        "subbasins": zone_names,
        "dates": {
            s: [d for d in ds if d >= f"{args.start_year - 1}-01-01"]
            for s, ds in all_dates.items()
        },
        "codes": {s: c for s, (_, c) in SOURCES.items()},
        "labels": LABELS,
        "date_note": "",
    }
    reconcile_dates(manifest)
    max_year = manifest["last_year"]
    if adapter_build:
        manifest["input_reader"] = adapter_build.metadata
    if configured:
        presentation = adapter_build.presentation()
        manifest["codes"] = codes
        manifest["labels"] = presentation["labels"]
        manifest["model_configuration"] = presentation
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "index.html").write_text(HTML, encoding="utf-8")
    conn.execute("ANALYZE")
    conn.commit()
    conn.close()
    print(
        f"Built {out}: {len(elems):,} elements; display years {args.start_year}–{max_year}",
        flush=True,
    )


from viewer_renderer import render_viewer

HTML = render_viewer(imagery="current")


def serve(args):
    out = Path(args.output)
    manifest = reconcile_dates(json.loads((out / "manifest.json").read_text()))
    db = out / "landuse.sqlite"

    @lru_cache(maxsize=100)
    def chosen(year):
        t = f"{year}-07-01"
        found = {}
        for s, ds in manifest["dates"].items():
            end = period_end_for_day(ds, t)
            if not end or not manifest["start_year"] <= year <= manifest["last_year"]:
                raise ValueError(f"No {s} input coverage for {t}")
            found[s] = end
        return found

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/":
                    return self.send_html(HTML)
                if u.path == "/api/manifest":
                    return self.send_json(manifest)
                conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                try:
                    if u.path == "/api/geometry":
                        return self.send_json(
                            [
                                {
                                    "id": i,
                                    "region": r,
                                    "subbasin": b,
                                    "xy": json.loads(xy),
                                }
                                for i, r, b, xy in conn.execute(
                                    "SELECT id,region,subbasin,xy FROM elements"
                                )
                            ]
                        )
                    if u.path == "/api/map":
                        year = int(q["year"][0])
                        active = chosen(year)
                        codes = q["codes"][0].split(",")
                        pct = q.get("percent", ["0"])[0] == "1"
                        mode = q.get("mode", ["element"])[0]
                        if mode not in ("element", "region", "subbasin"):
                            raise ValueError("Unknown mode")
                        if mode == "subbasin" and not manifest["subbasins"]:
                            raise ValueError("No subbasin zone file in snapshot")
                        if not codes or any(
                            c
                            not in [c for cs in manifest["codes"].values() for c in cs]
                            for c in codes
                        ):
                            raise ValueError("Unknown map category")
                        result = {}
                        group_field = {
                            "element": "id",
                            "region": "region",
                            "subbasin": "subbasin",
                        }[mode]
                        acres = (
                            dict(
                                conn.execute(
                                    f"SELECT {group_field},SUM(acres) FROM elements GROUP BY {group_field}"
                                )
                            )
                            if pct
                            else {}
                        )
                        for s, d in active.items():
                            indices = [
                                manifest["codes"][s].index(c)
                                for c in codes
                                if c in manifest["codes"][s]
                            ]
                            if not indices:
                                continue
                            n = len(manifest["codes"][s])
                            fmt = "<" + ("f" if mode == "element" else "d") * n
                            table, field = {
                                "element": ("areas", "element"),
                                "region": ("region_areas", "region"),
                                "subbasin": ("subbasin_areas", "subbasin"),
                            }[mode]
                            for eid, blob in conn.execute(
                                f"SELECT {field},vals FROM {table} WHERE source=? AND effective=?",
                                (s, d),
                            ):
                                v = struct.unpack(fmt, blob)
                                result[eid] = result.get(eid, 0) + sum(
                                    v[i] for i in indices
                                )
                        if pct:
                            result = {
                                i: 100 * v / acres[i] if acres[i] else 0
                                for i, v in result.items()
                            }
                        return self.send_json({"values": result, "effective": active})
                    if u.path == "/api/elements":
                        ids = sorted(set(int(x) for x in q["ids"][0].split(",")))
                        if not ids or len(ids) > 500:
                            raise ValueError("Select between 1 and 500 elements")
                        year = int(q["year"][0])
                        active = chosen(year)
                        placeholders = ",".join("?" for _ in ids)
                        count, acres = conn.execute(
                            f"SELECT COUNT(*),SUM(acres) FROM elements WHERE id IN ({placeholders})",
                            ids,
                        ).fetchone()
                        if count != len(ids):
                            raise ValueError("Unknown element in selection")
                        series = {}
                        for s, codes in manifest["codes"].items():
                            n = len(codes)
                            fmt = "<" + "f" * n
                            totals = {}
                            for d, blob in conn.execute(
                                f"SELECT effective,vals FROM areas WHERE source=? AND element IN ({placeholders}) ORDER BY effective",
                                (s, *ids),
                            ):
                                values = totals.setdefault(d, [0.0] * n)
                                for i, v in enumerate(struct.unpack(fmt, blob)):
                                    values[i] += v
                            series[s] = [
                                {"effective": d, "values": values}
                                for d, values in sorted(totals.items())
                            ]
                        periods = input_periods(manifest, active)
                        return self.send_json(
                            {
                                "ids": ids,
                                "acres": acres,
                                "count": count,
                                "series": series,
                                "periods": periods,
                            }
                        )
                    if u.path.startswith(
                        ("/api/element/", "/api/region/", "/api/subbasin/")
                    ):
                        eid = int(u.path.rsplit("/", 1)[1])
                        year = int(q["year"][0])
                        active = chosen(year)
                        is_region = u.path.startswith("/api/region/")
                        is_subbasin = u.path.startswith("/api/subbasin/")
                        field = (
                            "subbasin"
                            if is_subbasin
                            else "region"
                            if is_region
                            else "id"
                        )
                        row = conn.execute(
                            f"SELECT MIN(region),SUM(acres),COUNT(*) FROM elements WHERE {field}=?",
                            (eid,),
                        ).fetchone()
                        if not row or not row[2]:
                            raise ValueError("Unknown selection")
                        series = {}
                        for s in manifest["codes"]:
                            n = len(manifest["codes"][s])
                            fmt = (
                                "<"
                                + ("f" if not (is_region or is_subbasin) else "d") * n
                            )
                            table, key = (
                                ("subbasin_areas", "subbasin")
                                if is_subbasin
                                else ("region_areas", "region")
                                if is_region
                                else ("areas", "element")
                            )
                            series[s] = [
                                {"effective": d, "values": struct.unpack(fmt, b)}
                                for d, b in conn.execute(
                                    f"SELECT effective,vals FROM {table} WHERE source=? AND {key}=? ORDER BY effective",
                                    (s, eid),
                                )
                            ]
                        periods = input_periods(manifest, active)
                        return self.send_json(
                            {
                                "id": eid,
                                "region": f"Subregion {row[0]}",
                                "name": manifest["subbasins"].get(str(eid))
                                if is_subbasin
                                else None,
                                "acres": row[1],
                                "count": row[2],
                                "series": series,
                                "periods": periods,
                            }
                        )
                    self.send_error(404)
                finally:
                    conn.close()
            except (ValueError, KeyError, IndexError) as e:
                self.send_error(400, str(e))

        def send_json(self, obj):
            b = json.dumps(obj, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def send_html(self, html):
            b = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def send_file(self, path, mime):
            b = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    url = f"http://127.0.0.1:{args.port}/"
    print("Open " + url + " (Ctrl+C to stop)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


STATIC_API = r"""
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
"""


def export_web(args):
    """Export a read-only static site; viewers need a web server, not Python."""
    source, out = Path(args.output), Path(args.web_output)
    manifest = reconcile_dates(json.loads((source / "manifest.json").read_text()))
    out.mkdir(parents=True, exist_ok=True)
    for generated in ("years", "history"):
        if (out / generated).exists():
            shutil.rmtree(out / generated)
    (out / "years").mkdir(exist_ok=True)
    (out / "history" / "elements").mkdir(parents=True, exist_ok=True)
    db = source / "landuse.sqlite"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    elements = list(
        conn.execute("SELECT id,region,subbasin,acres,xy FROM elements ORDER BY id")
    )
    ids = {eid: i for i, (eid, *_) in enumerate(elements)}
    geometry = [
        {"id": eid, "region": r, "subbasin": b, "acres": a, "xy": json.loads(xy)}
        for eid, r, b, a, xy in elements
    ]
    (out / "geometry.json").write_text(
        json.dumps(geometry, separators=(",", ":")), encoding="utf-8"
    )
    (out / "manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
    )
    codes = list(manifest["codes"])
    width = sum(len(manifest["codes"][s]) for s in codes)
    offsets = {}
    n = 0
    for s in codes:
        offsets[s] = n
        n += len(manifest["codes"][s])
    for year in range(manifest["start_year"], manifest["last_year"] + 1):
        t = f"{year}-07-01"
        data = bytearray(len(elements) * width * 4)
        for s in codes:
            effective = period_end_for_day(manifest["dates"][s], t)
            count = 0
            for eid, blob in conn.execute(
                "SELECT element,vals FROM areas WHERE source=? AND effective=?",
                (s, effective),
            ):
                i = ids[eid]
                start = (i * width + offsets[s]) * 4
                data[start : start + len(blob)] = blob
                count += 1
            if count != len(elements):
                raise ValueError(f"{s} {effective}: {count} elements")
        with gzip.open(out / "years" / f"{year}.bin.gz", "wb", compresslevel=6) as f:
            f.write(data)
    print(
        f"Exported {manifest['start_year']}–{manifest['last_year']} annual maps",
        flush=True,
    )
    for table, mode, key in [
        ("region_areas", "region", "region"),
        ("subbasin_areas", "subbasin", "subbasin"),
    ]:
        if mode == "subbasin" and not manifest.get("subbasins"):
            continue
        histories = {}
        for source, effective, gid, blob in conn.execute(
            f"SELECT source,effective,{key},vals FROM {table} ORDER BY {key},source,effective"
        ):
            vals = struct.unpack("<" + "d" * len(manifest["codes"][source]), blob)
            histories.setdefault(gid, {}).setdefault(source, []).append(
                [effective, vals]
            )
        with gzip.open(
            out / "history" / f"{mode}.json.gz", "wt", encoding="utf-8", compresslevel=6
        ) as f:
            json.dump(histories, f, separators=(",", ":"))
    shard = {}
    current = None

    def flush(shard_id):
        if shard_id is None:
            return
        with gzip.open(
            out / "history" / "elements" / f"{shard_id:04d}.json.gz",
            "wt",
            encoding="utf-8",
            compresslevel=6,
        ) as f:
            json.dump(shard, f, separators=(",", ":"))

    for eid, source, effective, blob in conn.execute(
        "SELECT element,source,effective,vals FROM areas ORDER BY element,source,effective"
    ):
        shard_id = ids[eid] // 128
        if current != shard_id:
            flush(current)
            shard = {}
            current = shard_id
        vals = struct.unpack("<" + "f" * len(manifest["codes"][source]), blob)
        shard.setdefault(eid, {}).setdefault(source, []).append([effective, vals])
    flush(current)
    conn.close()
    old = "async function api(path){let r=await fetch(path);if(!r.ok)throw Error(await r.text());return r.json()}"
    if HTML.count(old) != 1:
        raise ValueError("Static API insertion point changed")
    (out / "index.html").write_text(HTML.replace(old, STATIC_API), encoding="utf-8")
    (out / "README.txt").write_text(
        "C2VSimFG static land use explorer. Upload this directory to an HTTPS static web host, preserving every relative path. Serve index.html at its root. The site contains model input areas; restrict access to authorized viewers. To test locally: python -m http.server 8000 --directory "
        + str(out)
        + "\n",
        encoding="utf-8",
    )
    print(f"Static browser app written to {out}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    b = sub.add_parser("build")
    b.add_argument("--input", required=True)
    b.add_argument("--output", required=True)
    b.add_argument("--start-year", type=int, default=2000)
    b.add_argument(
        "--utm-zone",
        type=int,
        default=10,
        help="Input node UTM zone (default 10N for C2VSimFG)",
    )
    from iwfm_build import add_build_options

    add_build_options(b)
    s = sub.add_parser("serve")
    s.add_argument("--output", required=True)
    s.add_argument("--port", type=int, default=8765)
    e = sub.add_parser("export-web")
    e.add_argument("--output", required=True, help="Built explorer snapshot")
    e.add_argument(
        "--web-output", required=True, help="Destination static site directory"
    )
    a = parser.parse_args()
    {"build": build, "serve": serve, "export-web": export_web}[a.action](a)
