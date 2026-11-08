#!/usr/bin/env python3
"""Current-imagery compatibility entry point for the shared land-use viewer."""

import sys
from pathlib import Path

_SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if _SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, _SCRIPT_DIRECTORY)

from landuse_viewer_backend import (
    DATE_RE,
    LABELS,
    SOURCES,
    STATIC_API,
    aggregate_groups,
    build as _build,
    choose,
    datestamp,
    export_web as _export_web,
    geometry,
    hashfile,
    input_periods,
    lines,
    main as _main,
    noncomments,
    parse_area,
    period_end_for_day,
    reconcile_dates,
    serve as _serve,
    subbasins,
    utm_to_web_mercator,
)
from viewer_renderer import render_viewer

HTML = render_viewer(imagery="current")


def build(args):
    return _build(args, imagery="current")


def serve(args):
    return _serve(args, imagery="current")


def export_web(args):
    return _export_web(args, imagery="current")


def main():
    return _main(imagery="current")


if __name__ == "__main__":
    main()
