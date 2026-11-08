# Regeneration And Advanced Options

Use this reference when you already have a SQLite snapshot, need the original
commands, or want advanced project/category options. For the recommended
model-deck quick start, see [README.md](README.md). For architecture and tests,
see [CONTRIBUTING.md](CONTRIBUTING.md). All generated folders and edited configs
should be outside the repository; the relative paths below illustrate the older
command syntax, not a recommendation to commit model data or outputs.

## Files to keep together

- `c2vsim_landuse_explorer_expanded.py` — builder, local server, and viewer template without the NAIP selector.
- `c2vsim_landuse_explorer_naip.py` — equivalent builder/server/template with the NAIP selector.
- `export_landuse_standalone.py` — compresses a built SQLite snapshot into one HTML file using either template.
- `viewer_renderer.py`, `viewer_assets/base.html`, `viewer_assets/imagery.json`, and `model_configuration.py` — shared viewer rendering and presentation hooks required by both entry points and exporters. Keep both asset files in `viewer_assets/` beside the renderer; paths are resolved relative to the renderer, not the working directory.
- This README.

Use Python 3.10 or newer. The original legacy build and snapshot-to-HTML export
use the Python standard library. The opt-in iwfm-io reader and one-command
project build need [requirements-adapter.txt](requirements-adapter.txt),
installed in an isolated environment. On Windows, open PowerShell in this
folder. If Python is installed as `py`, substitute `py -3` in Python commands.

## Regenerate HTML from an existing database

Place your built snapshot folder beside the three scripts. The commands below assume it is called `landuse_explorer_new` and contains both `landuse.sqlite` and its matching `manifest.json`. The existing `index.html` is not used by the standalone exporter.

```powershell
python .\export_landuse_standalone.py --data .\landuse_explorer_new --viewer .\c2vsim_landuse_explorer_expanded.py --html .\C2VSimFG_landuse_standalone.html
python .\export_landuse_standalone.py --data .\landuse_explorer_new --viewer .\c2vsim_landuse_explorer_naip.py --html .\C2VSimFG_landuse_standalone_naip.html
```

Use your actual snapshot folder name if different. Exporting reads the database without modifying it. Progress messages report the records compressed and the final HTML size. Existing HTML outputs at those paths are replaced.

Open either resulting HTML directly in a current Edge, Chrome, or Firefox browser. No Python server, SQLite file, or other accompanying files need to be distributed with it. The aerial imagery needs an internet connection; the land-use data and charts are embedded.

## Rebuild when the model input data changes

Skip this step if you are only regenerating HTML from an existing SQLite snapshot.

Create a folder named `data` beside the scripts. It must contain one matching file for each of:

- `C2VSimFG_Nodes.dat`
- `C2VSimFG_Elements.dat`
- `C2VSimFG_NonPondedCrop_Area.dat`
- `C2VSimFG_PondedCrop_Area.dat`
- `C2VSimFG_NativeVeg_Area.dat`
- `C2VSimFG_Urban_Area.dat`

The prefixes may differ, but the distinguishing filename portions (Nodes, Elements, NonPondedCrop_Area, and so on) must remain. Each DAT may instead be a ZIP containing one DAT. Do not leave both DAT and ZIP alternatives, or multiple versions of the same input, in the input folder.

For subbasin selections, also include a verified element-based ZoneBudget
definition, such as `ZBudget_C2VSimFG_GSP_Subbasins.DAT`. Its filename must
contain `ZBudget` or `ZoneDef`. Membership and boundaries come from those
element assignments, not external polygons. Matching Bulletin 118 labels can
receive the embedded descriptive names described below. Include the zone file
again on each rebuild to preserve subbasin features.

Build one snapshot using either builder; both viewer variants use the same snapshot format:

```powershell
python .\c2vsim_landuse_explorer_expanded.py build --input .\data --output .\landuse_explorer_new --start-year 2000 --utm-zone 10
```

This creates/replaces the snapshot in `landuse_explorer_new`. Choose another output folder to keep an older snapshot. UTM zone 10N is the existing C2VSimFG default; change the option only if your node coordinates use a different supported UTM north zone.

Then run the two HTML export commands above. Changing an input file does not automatically update an existing snapshot or HTML.

## Optional: view the SQLite snapshot through Python

```powershell
python .\c2vsim_landuse_explorer_expanded.py serve --output .\landuse_explorer_new --port 8766
```

Open http://127.0.0.1:8766. For NAIP, use the other script and a separate port:

```powershell
python .\c2vsim_landuse_explorer_naip.py serve --output .\landuse_explorer_new --port 8767
```

Open http://127.0.0.1:8767. Press Ctrl+C in the corresponding PowerShell window to stop a server. Standalone HTML files do not require these servers.

## Date and data conventions

- The map's display year is the condition on July 1, not a water-year average.
- IWFM input timestamps mark interval ends. For example, `09/30/2024_24:00` ends the interval at midnight starting October 1, 2024.
- A rebuild with start year 2000 retains blocks with normalized endpoints from January 1, 1999 onward. It does not retain the full historical input file.
- The standalone exporter preserves all dates and float32 acreage values stored in the snapshot; it does not replace the time series with July 1 samples.
- Regional totals are calculated from element records in the standalone viewer. Identical acreage vectors are stored once and referenced by compressed indices.
- Imagery year is independent of land-use year. Historical NAIP choices are fixed-year layers; Latest NAIP is an updating USDA service.
- CSV export offers charted series or all individual land-use categories. Dates use inclusive starts and exclusive ends, with blank acreage where a source has no input coverage. The Excel-compatible CSV includes selection identity, subregions, subbasins, crop codes, acres, and percentages. Element IDs are included for element selections; regional exports use their regional identity.
- PNG export includes selection context, units, dates, and the chart legend. Chart colors are tied to categories/groups and stay consistent as visible series change.

## Quick checks after regenerating

Open both HTML files, select a familiar element, and confirm its geography and values. Check a subbasin that spans several subregions. Turn rice categories on/off and verify the group color stays consistent. Download a CSV and PNG. If imagery is unavailable, the embedded data should still work; check the aerial-imagery checkbox or choose another imagery source where available.

No model input data or SQLite database is included in this small regeneration kit. Keep your original inputs and snapshot separately.

## Descriptive basin and subbasin names (October 14 update)

Both viewer templates now embed DWR basin names and show the name first with the Bulletin 118 ID in parentheses, for example North American (5-021.64). This applies to selectors, selection context, map hover text, CSV and PNG exports. The right-hand Go to field accepts either the displayed name with ID or the original basin ID. Labels Suisun Bay and Unassigned remain unchanged.

The earlier C2VSimFG review matched 45 numbered labels against DWR's Bulletin
118 service. Only labels changed, not boundaries, assignments, or acreage.
Names are embedded, so no network lookup is needed. [subbasin_names.json](subbasin_names.json)
is a reference copy, not a runtime dependency. To amend a name, update
`basinNameByCode` in [viewer_assets/base.html](viewer_assets/base.html) and
export again. Both imagery modes use that shared template. Unrecognized IDs
remain visible as IDs.

Source: https://gis.water.ca.gov/arcgis/rest/services/Geoscientific/i08_B118_CA_GroundwaterBasins/FeatureServer/0 (name records retrieved October 14, 2026; source layer describes basin boundaries current December 9, 2022).

## CSV number formatting (October 15 update)

Exported acreage columns (`acres` and `selected_area_acres`) use two decimal places. Percentages use one decimal place. Blank values for missing coverage remain blank. This formatting applies to charted-series and all-category exports for every selection type, in both viewer variants. Values are rounded only when writing the CSV; the embedded input values, aggregation calculations, map and chart data are unchanged. Consequently, displayed rounded components may not sum exactly to a displayed rounded total.

## Navigation fix October 29 2026

The right-panel Go to suggestions now follow Explore by: element IDs in Element mode, subregion numbers in Subregion mode, and subbasin names in Subbasin mode. Type an ID or name to narrow the suggestions, then click Go. Switching modes clears the previous geographic selection. Both standalone HTML files and both Python templates include this fix.

## Opt-In Reader And Static Export

The original scripts default to the legacy C2VSimFG parser: four fixed source
lists, raw input values treated as acres, and native metre geometry. Use the
opt-in path for verified v4.11 decks and model category configuration. No
unsupported input silently falls back to the legacy reader.

With `$Model`, `$Inputs`, and `$Snapshot` set to external folders, run from the
repository root using the isolated Python executable from the main quick start:

```powershell
& $Python .\c2vsim_landuse_explorer_expanded.py build `
	--reader iwfm-io --input $Inputs --output $Snapshot --start-year 2000 --utm-zone 10 `
	--rootzone-main "$Model\Simulation\RootZone\C2VSimFG_RootZone.dat" `
	--preprocessor-main "$Model\Preprocessor\C2VSimFG_Preprocessor.in" `
	--simulation-main "$Model\Simulation\C2VSimFG.in" --model-config $Presentation
```

`$Presentation` names your category/branding JSON. The NAIP entry point accepts
the same build options. `--coverage-start` can replace simulation `BDT` only
when you have verified the actual inclusive starting instant. The display start
year must include January 1 within coverage, because that is the chart start.

A static folder export is different from a standalone HTML file: it needs a
web server and every generated file. It retains histories but precomputes
July 1 maps. Supply external output folders:

```powershell
& $Python .\c2vsim_landuse_explorer_expanded.py export-web --output $Snapshot --web-output $StaticFolder
```

## Project Settings And Output Safety

The one-command project schema is demonstrated by [examples/svsim-project.json](examples/svsim-project.json).
Its paths resolve relative to the JSON, not the launch directory. Required
fields are `version: 1`, `reader: iwfm-io`, input mains, a presentation-config
reference, `map.start_year`, explicit coordinates/units, imagery mode, and
`output_html`. Supply simulation `BDT` or explicit `coverage_start`.

- Supported coordinates are NAD83/GRS80 UTM north in metres, zone 1-60.
	The command trusts the declaration; verify it from model/GIS documentation.
- Model area units must be square feet. Preprocessor `UNITLTOU=FEET` and
	`FACTLTOU=1` establish them. Node factor must be near 3.28084, allowing the
	checked 3.2808 within tolerance 0.0001. Area factors are 1 or 43560.
- Imagery is `current` or `historical-naip`; historical mode defaults to NAIP
	2022. It changes the available imagery controls, not land-use dates.
- `inputs.zone_definition` is optional. It must be a verified horizontal
	element mapping (`ZEXTENT=1`) in a matching ZBudget/ZoneDef DAT or ZIP.
- `snapshot_directory` is optional and must be new or empty. Omit it for
	temporary intermediates. The command never deletes a user-supplied directory;
	a failed retained build may leave intermediates there for inspection.
- Existing final HTML is protected. `overwrite: true` explicitly permits
	replacement only after successful export. Default no-overwrite publication
	needs filesystem hard-link support, such as NTFS, and fails safely otherwise.
	Choose a new filename rather than overriding protection unintentionally.

Project preflight validates the metadata it accepts, then the adapter validates
full area records during the build. The supported reader is pinned to 2.15.2
and root-zone 4.11; fractions (`FACT=0`), DSS, recurring timelines, other factors,
formats, and projections are unsupported. Existing snapshots can still be
exported without using this reader.

## Category And Branding Configuration

Use [examples/c2vsimfg.json](examples/c2vsimfg.json) for C2VSimFG presets or
[examples/svsim-presentation.json](examples/svsim-presentation.json) for a
minimal SVSim file. The project JSON references this separate schema; it does
not duplicate category settings.

| Setting | Meaning |
|---|---|
| `version` | 1 |
| `categories` | Optional label/color overrides keyed by source/code ID, such as `nonponded:OR`. Colors are six-digit hex values. |
| `groups` | List of unique `group:` IDs, labels, colors, and nonempty unique category-ID members. Groups cannot contain groups. |
| `initial_map_category` | Required existing category or group ID. |
| `chart_toggle` | Optional group binding: `nonpondedgroup`, `rice`, `refuge`, or `native`; each existing control may be bound once. |
| `minimum_members` | Optional selected-member threshold for checkbox grouping; default 1. |
| `partial_label` | Optional label for partial groups; selected count is appended. |
| `model_title` | Optional literal-text browser/page/PNG title. Omit to retain defaults. |
| `export_filename_prefix` | Optional 1-80 ASCII letters/digits/underscores/hyphens, starting with a letter/digit. Paths, dots, spaces, and device names are rejected. |

IDs and acreage columns come from the input reader, never labels or group
names. Missing or conflicting descriptions use neutral labels. Duplicate
display names remain distinct series. Configured CSV category codes are stable
IDs; no-config snapshots retain legacy aliases. Colors affect chart/legend
series, not the existing numerical map ramp.

Without a subbasin mapping, controls and context are hidden and CSV subbasin
values are blank, with the same column schema. With a supplied mapping,
genuinely unassigned elements remain labeled Unassigned.
