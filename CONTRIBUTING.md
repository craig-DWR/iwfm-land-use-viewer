# Contributing

Write for groundwater modelers who understand IWFM conventions but may not
know Python or this codebase. Prefer straightforward code and small changes.
Keep real decks, edited project JSON with personal paths, environments, and
generated databases/HTML/reports outside the repository.

## How The Viewer Is Built

1. Project JSON supplies verified paths, coordinates, units, map start year,
   imagery mode, and the referenced category/branding configuration.
2. The adapter reads land-use **input** files. Each source keeps its own ordered
   categories and interval-end timestamps; acreage is normalized explicitly.
3. The existing builder writes a SQLite snapshot and manifest. Element acreage
   vectors use float32; stored regional totals use float64. Geometry uses the
   existing native-metre area denominator, separately from acreage conversion.
4. The standalone exporter embeds geometry and retained time series in HTML.
   The browser sums element records for selections and regional views. Imagery
   stays online; land-use data stay embedded.

No IWFM simulation is run. Do not substitute budget-derived areas for the
land-use inputs when checking reader parity.

## Code Map

| Topic | Where To Look |
|---|---|
| Project paths, preflight, output protection, temporary cleanup | [build_viewer.py](build_viewer.py): `load_project`, `preflight`, `build_project` |
| Ordered categories, v4.11 validation, acreage conversion, missing coverage | [iwfm_input_adapter.py](iwfm_input_adapter.py): `IWFMInputAdapter`, `UnitBasis`, `LandUseSource` |
| Adapter-to-snapshot wiring and stored precision | [iwfm_build.py](iwfm_build.py): `AdapterBuild`, `store_source` |
| Legacy dates, July 1 selection, geometry, SQLite, HTTP/static export | [c2vsim_landuse_explorer_expanded.py](c2vsim_landuse_explorer_expanded.py): `datestamp`, `period_end_for_day`, `reconcile_dates`, `geometry`, `build`, `serve`, `export_web` |
| Historical-imagery entry point with the same backend contract | [c2vsim_landuse_explorer_naip.py](c2vsim_landuse_explorer_naip.py) |
| Shared map/chart/selection/CSV/PNG template | [viewer_assets/base.html](viewer_assets/base.html) |
| Current/historical imagery fragments and assembly | [viewer_assets/imagery.json](viewer_assets/imagery.json), [viewer_renderer.py](viewer_renderer.py): `render_viewer` |
| Stable IDs, labels/groups/colors, branding, no-mapping hooks | [model_configuration.py](model_configuration.py): `resolve_configuration`, `prepare_template` |
| Single-file HTML encoding and browser data API | [export_landuse_standalone.py](export_landuse_standalone.py): `export`, `API` |

The two entry points expose `HTML`; the standalone/static exporters rely on it
and on the API insertion marker. Shared-template comments and whitespace are
copied into generated HTML. Avoid changing those strings in readability-only
work, and check both `current` and `historical-naip` modes after template edits.

IWFM `24:00` is next-day midnight. Endpoints are exclusive: lookup exactly at an
endpoint moves to the next interval. The caller supplies the first coverage
boundary; area endpoints alone do not establish it. July 1 is a common calendar
query across source schedules, not an average or a resampling of chart history.
Missing coverage must remain distinct from a covered zero.

## Install And Run Tests

From PowerShell in the repository root, use Python 3.10 or newer:

```powershell
$Environment = Join-Path $env:LOCALAPPDATA 'IWFM-Land-Use-Viewer/python-env'
python -m venv $Environment
$Python = Join-Path $Environment 'Scripts/python.exe'
& $Python -m pip install -r .\requirements-adapter.txt
& $Python -B -m unittest discover -s tests -v
```

The repository tests generate small temporary fixtures and require no local
model deck or comparison reports. Keep iwfm-io pinned: the adapter uses private
parsing primitives to provide clear errors before known malformed-code failures.
Real decks and external tile loading are separate checks; report the inputs,
settings, coverage, precision differences, and what was not verified.

## Review A Change

Run the tests before proposing a change. Compare original and new stored values
when touching readers, units, or dates. Preserve stable category IDs even when
labels change. Check group membership explicitly; do not infer irrigation or
idle status from display text. Keep commands/defaults unless the task explicitly
changes them. Leave automated formatting to its own PR.

If using Git, inspect `git status --short` and `git diff --check` before staging;
stage only intended source, tests, examples, and documentation. Do not commit
real model inputs or generated viewer outputs. Read
[the regeneration reference](README_landuse_regeneration.md) for existing
commands and advanced configuration.