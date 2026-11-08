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
| Legacy dates, July 1 selection, geometry, SQLite, ZoneBudget membership/aggregation, build, HTTP, and static export | [landuse_viewer_backend.py](landuse_viewer_backend.py): `datestamp`, `period_end_for_day`, `reconcile_dates`, `geometry`, `subbasins`, `build`, `serve`, `export_web` |
| Current-imagery compatibility entry point and `HTML` value | [c2vsim_landuse_explorer_expanded.py](c2vsim_landuse_explorer_expanded.py) |
| Historical-NAIP compatibility entry point and `HTML` value | [c2vsim_landuse_explorer_naip.py](c2vsim_landuse_explorer_naip.py) |
| Shared map/chart/selection/CSV/PNG template | [viewer_assets/base.html](viewer_assets/base.html) |
| Current/historical imagery fragments and assembly | [viewer_assets/imagery.json](viewer_assets/imagery.json), [viewer_renderer.py](viewer_renderer.py): `render_viewer` |
| Stable IDs, labels/groups/colors, branding, zone terminology/names, compatibility hooks | [model_configuration.py](model_configuration.py): `resolve_configuration`, `resolve_zone_configuration`, `prepare_template` |
| Single-file HTML encoding and browser data API | [export_landuse_standalone.py](export_landuse_standalone.py): `export`, `API` |

The two compatibility entry points pass their imagery choice explicitly to the
shared backend and each expose their own `HTML` value. The standalone exporter
loads either entry point by file path and reads that value; static export uses
the corresponding HTML and API insertion marker. Shared-template comments and
whitespace are copied into generated HTML. Avoid changing those strings in
readability-only work, and check both `current` and `historical-naip` modes after
template edits.

Zone membership is read from at most one horizontal ZoneBudget definition per
build and stays keyed by the existing `subbasin` snapshot/API identifiers.
`model_configuration.py` resolves presentation names and terms; it never
changes membership or acreage. C2VSimFG v1.5's verified Bulletin 118 ZID names
are explicit in its presentation example. The shared HTML has only a narrowly
scoped fallback for snapshots created before configurable zone metadata.

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

## Format Python Code

From PowerShell in the repository root, install the development-only formatter
in an external environment. This does not install IWFM readers or plotting
packages, and viewers do not need Ruff to run:

```powershell
$FormatEnvironment = Join-Path $env:LOCALAPPDATA 'IWFM-Land-Use-Viewer/formatter-env'
python -m venv $FormatEnvironment
$FormatPython = Join-Path $FormatEnvironment 'Scripts/python.exe'
& $FormatPython -m pip install -r .\requirements-dev.txt
```

Format Python source and tests, or check without editing files:

```powershell
& $FormatPython -m ruff format .
& $FormatPython -m ruff format --check .
```

[pyproject.toml](pyproject.toml) limits discovery to Python files and sets the
supported Python floor. [requirements-dev.txt](requirements-dev.txt) pins the
same Ruff version used by the pull-request formatting workflow. HTML,
JavaScript, JSON, and generated outputs are not formatting targets. Embedded
HTML/JavaScript strings must remain identical; do not enable docstring-code
formatting or apply lint autofixes as part of a formatting change.

If the PR check fails, run the format command above, review the resulting diff,
run the check again, and run the repository tests. Include only intended Python
formatting changes. No VS Code extension or format-on-save setting is required.
Ruff is a formatter here; this workflow introduces no lint gate.

If using Git, inspect `git status --short` and `git diff --check` before staging;
stage only intended source, tests, examples, and documentation. Do not commit
real model inputs or generated viewer outputs. Read
[the regeneration reference](README_landuse_regeneration.md) for existing
commands and advanced configuration.