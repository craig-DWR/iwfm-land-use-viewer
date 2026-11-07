# Regenerating the C2VSimFG land-use viewers

This kit regenerates both self-contained HTML viewers with the latest review changes: subbasin/subregion context, a subbasin reference selector, stable chart colors, CSV exports, and PNG chart exports. Use the Python templates in this kit; older templates will omit those interface improvements.

## Files to keep together

- `c2vsim_landuse_explorer_expanded.py` — builder, local server, and viewer template without the NAIP selector.
- `c2vsim_landuse_explorer_naip.py` — equivalent builder/server/template with the NAIP selector.
- `export_landuse_standalone.py` — compresses a built SQLite snapshot into one HTML file using either template.
- `viewer_renderer.py`, `viewer_assets/base.html`, `viewer_assets/imagery.json`, and `model_configuration.py` — shared viewer rendering and presentation hooks required by both entry points and exporters. Keep both asset files in `viewer_assets/` beside the renderer; paths are resolved relative to the renderer, not the working directory.
- This README.

Use Python 3.10 or newer. Everything uses the Python standard library; no pip packages are required. On Windows, open PowerShell in this folder. If your computer uses `py` instead of `python`, substitute `py` in the commands.

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

For subbasin selections, also include your element-based ZoneBudget definition file, such as `ZBudget_C2VSimFG_GSP_Subbasins.DAT`. Its filename must contain `ZBudget` or `ZoneDef`. Subbasin labels come from that file; the viewer does not add external basin names or boundaries. Include the zone file again on each rebuild to preserve subbasin features.

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

All 45 numbered labels in the supplied snapshot were matched against DWR's Bulletin 118 feature service. Only labels changed; no boundaries, element assignments, subregion relationships, or acreage values changed. The names are embedded in the templates, so no network lookup is needed when rebuilding or opening the viewer. `subbasin_names.json` is a reference copy of the lookup; it is not a runtime dependency. To amend a name, update the `basinNameByCode` constant in both Python templates and export again. Future unrecognized IDs remain visible as IDs.

Source: https://gis.water.ca.gov/arcgis/rest/services/Geoscientific/i08_B118_CA_GroundwaterBasins/FeatureServer/0 (name records retrieved October 14, 2026; source layer describes basin boundaries current December 9, 2022).

## CSV number formatting (October 15 update)

Exported acreage columns (`acres` and `selected_area_acres`) use two decimal places. Percentages use one decimal place. Blank values for missing coverage remain blank. This formatting applies to charted-series and all-category exports for every selection type, in both viewer variants. Values are rounded only when writing the CSV; the embedded input values, aggregation calculations, map and chart data are unchanged. Consequently, displayed rounded components may not sum exactly to a displayed rounded total.

## Navigation fix October 29 2026

The right-panel Go to suggestions now follow Explore by: element IDs in Element mode, subregion numbers in Subregion mode, and subbasin names in Subbasin mode. Type an ID or name to narrow the suggestions, then click Go. Switching modes clears the previous geographic selection. Both standalone HTML files and both Python templates include this fix.
