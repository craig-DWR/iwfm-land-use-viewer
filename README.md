# iwfm-land-use-viewer
Build standalone interactive maps from IWFM land-use inputs. Initially developed for C2VSimFG.

## Experimental Input Adapter

`iwfm_input_adapter.py` is independently usable. Both viewer entry points now
offer an opt-in adapter build; the default build still uses the legacy reader.
The standalone exporter and viewer templates are unchanged.
From the repository root, create a separate environment outside the repository.
These PowerShell commands do not require activation or an execution-policy change:

```powershell
python -m venv "$env:TEMP\iwfm-adapter-review-venv"
& "$env:TEMP\iwfm-adapter-review-venv\Scripts\python.exe" -m pip install -r requirements-adapter.txt
& "$env:TEMP\iwfm-adapter-review-venv\Scripts\python.exe" -B -m unittest discover -s tests -v
```

Use Python 3.10 or newer. Repo-only tests require no real model, comparison
reports, or existing viewer outputs. Real-deck comparisons are separate local
diagnostics, not part of this test command.

```python
from datetime import datetime
from iwfm_input_adapter import C2VSIMFG_ALIASES, IWFMInputAdapter, UnitBasis

basis = UnitBasis.from_preprocessor(preprocessor_main_path)
adapter = IWFMInputAdapter(
	rootzone_main_path,
	working_directory=simulation_directory,
	element_ids=element_ids,
	unit_basis=basis,
	coverage_start=datetime(1973, 10, 1),
	legacy_aliases=C2VSIMFG_ALIASES,
)
source = adapter.read_source('nonponded')
snapshot = source.values_at(datetime(2000, 7, 1))
```

The paths, expected element IDs, and authoritative coverage start are supplied
by the caller. Nonponded category order comes from `NCROP`/`CCODE`; the other
sources use v4.11 slots. Names and optional aliases never define column order.
Label overrides use namespaced category IDs, for example `nonponded:OR`.
Input metadata descriptions are labels only; unnamed nonponded crops receive
neutral positional labels. Budget-reporting codes are returned separately.

Each source retains original timestamp strings, normalized exclusive interval
ends, raw values, and float64 acres indexed by endpoint and element ID. Lookup
selects the first endpoint strictly after the requested instant and returns
`None` before the supplied coverage start or at/after the final endpoint.
Sources are read separately, without timeline joins, retention filtering,
extrapolation, or float32 serialization. A blank component reference is absent,
not a zero-valued source.

The accepted root-zone and area inputs contain interval ends, not an authoritative
beginning of coverage. The adapter does not read the simulation main or infer
the first interval's start. The caller must supply a verified `coverage_start`
(for example, the simulation beginning instant). This is an inclusive lower
bound; it is trusted, not independently verified from these inputs. Requests
must use naive model datetimes, including the next-day midnight corresponding
to an IWFM `24:00` stamp.

| Lookup Instant | Result |
|---|---|
| Before `coverage_start` | `None` |
| At `coverage_start` | First block whose end is strictly later |
| At an interior endpoint | Following block |
| At or after the final endpoint | `None` |

`None` means missing coverage; a returned zero remains a covered acreage value.

Initial support is deliberately limited to root-zone v4.11, iwfm-io 2.15.2,
inline nonrecurring data, and verified model square feet with area `FACT` equal
to 1 (square feet) or 43560 (acres). Other formats, unknown units, other factors,
`FACT=0` fractions, and DSS inputs raise errors. Preprocessor evidence requires
`UNITLTOU=FEET` and `FACTLTOU=1`; alternatively, callers can supply an explicit
`UnitBasis` with verified evidence. No node factor, CRS conversion, or geometry
area denominator is changed or calculated by this adapter.

The adapter uses the private `iwfm_io._parser.IWFMFileReader` for the format
header and nonponded declaration preflight. In 2.15.2, duplicate crop codes can
otherwise reach pandas and cause a generic `TypeError` before the full direct
component reader returns. Preflight provides an actionable file/code/column
error without replacing the area reader. The exact dependency pin protects
this private-API contract; upgrades require rechecking it and the repo tests.

Keep real model decks and comparison reports outside this repository. Tests
generate small synthetic reader fixtures in temporary directories; they are
not executable-certified IWFM models.

## Opt-In C2VSimFG Build

Install the pinned adapter requirements using the environment commands above.
Set `$Model`, `$Inputs`, and `$Output` to external folders. `$Inputs` is the
existing flat input folder used for legacy builds; both paths use any optional
ZoneBudget definition found there. Opt-in geometry comes from the referenced
preprocessor node/element files, not filename guessing.

```powershell
& "$env:TEMP\iwfm-adapter-review-venv\Scripts\python.exe" .\c2vsim_landuse_explorer_expanded.py build `
	--reader iwfm-io --input $Inputs --output $Output --start-year 2000 --utm-zone 10 `
	--rootzone-main "$Model\Simulation\RootZone\C2VSimFG_RootZone.dat" `
	--preprocessor-main "$Model\Preprocessor\C2VSimFG_Preprocessor.in" `
	--simulation-main "$Model\Simulation\C2VSimFG.in"
```

The NAIP script accepts the same build options. Alternatively supply an
authoritative `--coverage-start 1973-10-01` instead of `--simulation-main`.
An explicit start takes precedence; otherwise the pinned direct simulation
reader supplies `BDT` and verifies the referenced root-zone main. Never derive
coverage start from an interval end. Coverage must include January 1 of the
display start year, because that is the unchanged chart start.

Omitting `--reader` (or using `--reader legacy`) keeps the original command,
file discovery, reader, and standard-library-only dependencies. Unsupported
opt-in inputs fail without falling back. Without configuration, the build
requires all four sources and the full known alias set, mapped into legacy
alias order. Configured builds use available adapter sources and their verified
column order instead, including different nonponded crop counts.

Opt-in builds preserve the existing geometry-area denominator, labels, groups,
colors, July 1 date selection, January-of-prior-year retention threshold,
float32 element values, and float64 regional aggregates. Source timelines stay
separate; the annual map range uses the common final coverage, while histories
can show later ponded intervals with blanks for sources that have ended.
The manifest adds `input_reader` provenance, factors, and original timestamps;
it does not change the snapshot schema or template data contract.

Use separate output folders for legacy and opt-in comparisons. The existing
standalone exporter accepts the adapter snapshot with either viewer template:

```powershell
& "$env:TEMP\iwfm-adapter-review-venv\Scripts\python.exe" .\export_landuse_standalone.py `
	--data $Output --viewer .\c2vsim_landuse_explorer_expanded.py --html $ExpandedHtml
& "$env:TEMP\iwfm-adapter-review-venv\Scripts\python.exe" .\export_landuse_standalone.py `
	--data $Output --viewer .\c2vsim_landuse_explorer_naip.py --html $NaipHtml
```

Keep `$Output`, `$ExpandedHtml`, `$NaipHtml`, and all comparison reports outside
the repository. The local v2.0 candidate is not authenticated as the exact deck
used for the previously shared viewer; record its provenance separately.

## Model Category Configuration

Add `--model-config .\examples\c2vsimfg.json` to the opt-in build command above.
This presentation-only JSON is supported only with `--reader iwfm-io`. The
C2VSimFG example reproduces the current labels, category/group colors, All rice,
All refuge, All nonponded crops, Native + riparian vegetation, and Total
irrigated agriculture (explicitly excluding Idle and refuge).

```json
{
	"version": 1,
	"categories": {
		"nonponded:OR": {"label": "Orchards", "color": "#ad6a1d"}
	},
	"groups": [
		{
			"id": "group:agriculture",
			"label": "Agriculture",
			"members": ["nonponded:OR", "nonponded:GR"],
			"color": "#267747"
		}
	],
	"initial_map_category": "group:agriculture"
}
```

Category IDs are the adapter's source and code, for example `nonponded:OR`,
`ponded:rice_fl`, `native:riparian`, and `urban:urban`. They are case-sensitive.
The configuration cannot define categories, column numbers, ordering, or
irrigated status. Available IDs and their input columns/codes/legacy aliases
are recorded in the configured manifest's `category_identity` table.

`categories` may override only `label` and `color`. Missing labels use one
unambiguous model description, otherwise neutral positional names such as
Nonponded crop 3. Conflicting descriptions remain neutral; explicit labels
override them. Missing colors use a deterministic ID-based palette, so
renaming or reordering does not change color identity. Duplicate display labels
are allowed and remain separate map/chart/CSV series.
Colors apply to chart/legend series; the existing numerical map ramp is retained.

Group IDs must be unique `group:` identifiers containing lowercase letters,
digits, underscores, or hyphens. Each group needs a nonempty label, six-digit
hex color, and a nonempty list of unique existing category IDs. Groups cannot
contain other groups. Optional `chart_toggle` binds one group to an existing
grouping control: `nonpondedgroup`, `rice`, `refuge`, or `native`; each control
may be bound once. Groups without a binding can still be selected through the
map/chart synchronization option. The initial map value must be an existing
category or group ID. Unknown references/fields, duplicate group IDs/members,
invalid colors, and invalid control bindings raise errors.

Optional `minimum_members` sets the number of selected members required for
checkbox-driven grouping (default 1). Optional `partial_label` names a partial
selection, with its selected count appended; otherwise the group label is
used. The C2VSimFG example requires two selected nonponded crops for grouping,
preserving the individual name and color when only one is selected.

For another supported v4.11 input set, change only the deck paths and JSON:

```powershell
& "$env:TEMP\iwfm-adapter-review-venv\Scripts\python.exe" .\c2vsim_landuse_explorer_expanded.py build `
	--reader iwfm-io --input $Inputs --output $Output --start-year 2019 --utm-zone 10 `
	--rootzone-main $RootZone --preprocessor-main $Preprocessor `
	--coverage-start 2018-10-01 --model-config $Configuration
```

Use the same standalone export commands above with either template. Configured
CSV `category_codes` contains stable category IDs; labels remain display text.
Configuration never changes acreage values, coverage, float32 storage, or the
existing geometry denominator. The two templates and UI layout remain separate.

### Category Data Flow

- Adapter: verified `NCROP`/`CCODE` order or v4.11 slots define identity;
	comments and budget-reporting order never define acreage columns.
- SQLite: configured builds pack adapter order at the existing precision;
	source widths and aggregation come from the verified category list.
- Manifest: `codes` holds ordered IDs; labels/colors/groups are ID-keyed.
	`model_configuration.category_identity` preserves input column/code identity.
- Served/static/standalone readers: decode widths from the manifest, not the
	legacy C2VSimFG lists. The standalone exporter needs no changes.
- Controls and CSV: values and chart row keys use stable IDs; names are text.
	Identical names cannot overwrite distinct series.

The original `SOURCES`, C2VSimFG aliases/labels, and name-based group presets
remain as no-config compatibility defaults. The separate raw templates retain
those presets; shared hooks select ID-based configuration when present.
Geographic names still come from the optional zone mapping. Page/export branding
can be overridden with the optional settings below.

### Model Branding And Subbasins

Two optional top-level JSON settings control presentation:

- `model_title`: nonempty text used for the browser tab, page heading, and
	chart-export title. It is rendered as literal text, never HTML.
- `export_filename_prefix`: 1-80 ASCII letters, digits, underscores, or hyphens,
	starting with a letter or digit. Paths, dots, spaces, and Windows device names
	such as `CON` or `LPT1` are rejected. It prefixes CSV and PNG download names.

Omit either setting to retain its current C2VSimFG default. Existing snapshots
without these settings retain their original browser/page titles and filename
prefix. The settings change no values, source intervals, or category identities.

When the snapshot has no subbasin mapping, subbasin mode and boundary/reference
controls are hidden, selection context omits subbasins, and chart exports omit
the subbasin metadata line. The CSV column schema stays unchanged: subbasin
values are blank. If a mapping is supplied, mapped names and genuinely
unassigned elements retain their existing labels, controls, and CSV values.

### SVSim Build Recipe

A minimal external SVSim configuration is:

```json
{
	"version": 1,
	"model_title": "SVSim land use",
	"export_filename_prefix": "SVSim_landuse",
	"categories": {},
	"groups": [],
	"initial_map_category": "nonponded:GR"
}
```

The existing SVSim configuration can retain its category/group definitions and
add only the two branding settings. Replace the placeholder paths below; keep
the JSON, snapshot, and HTML outside this repository. SVSim v1.0's node
documentation and GIS projection metadata verify NAD83 UTM Zone 10N in metres.
Using its root-zone input directory leaves subbasins off; do not add a zone file
without verifying its element-to-zone mapping.

```powershell
$Model = 'PATH_TO_EXTRACTED_SVSIM_MODEL'
$Configuration = 'PATH_TO_EXTERNAL_SVSIM_JSON'
$Output = 'PATH_TO_EXTERNAL_SNAPSHOT'
$Html = 'PATH_TO_EXTERNAL_SVSIM_HTML'
& "$env:TEMP\iwfm-adapter-review-venv\Scripts\python.exe" .\c2vsim_landuse_explorer_expanded.py build `
	--reader iwfm-io --input "$Model\Simulation\RootZone" --output $Output `
	--start-year 2000 --utm-zone 10 `
	--rootzone-main "$Model\Simulation\RootZone\SVSim_RootZone.dat" `
	--preprocessor-main "$Model\PreProcessor\SVSim_Preprocessor.in" `
	--simulation-main "$Model\Simulation\SVSim.in" --model-config $Configuration
& "$env:TEMP\iwfm-adapter-review-venv\Scripts\python.exe" .\export_landuse_standalone.py `
	--data $Output --viewer .\c2vsim_landuse_explorer_expanded.py --html $Html
```

For the NAIP variant, use `c2vsim_landuse_explorer_naip.py` as the export template
and a separate external HTML path. The same branding and subbasin rules apply.
