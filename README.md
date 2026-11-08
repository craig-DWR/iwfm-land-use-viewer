# IWFM Land Use Viewer

Explore land-use inputs by model element, subregion, and optional subbasin.
The viewer was developed for C2VSimFG and has also been checked with SVSim v1.0.
It displays input acreages, not budget-derived areas, and does not run IWFM.

## I Already Have A Standalone HTML Viewer

Double-click the HTML file, or open it from a current Edge, Chrome, or Firefox
browser. You do not need Python, Git, the model deck, or a server. Keep a local
copy if you want to use it without an internet connection.

The mesh, land-use data, chart history, and CSV/PNG export functions are embedded
in the standalone file. They work offline. Aerial imagery comes from external
services and needs internet access; turn it off if it cannot load. Internet
access is not needed to select elements or inspect their land-use data.

### Read The Map And Chart Together

- **Map year:** year Y means the condition on **July 1 of Y**, not an annual
  average or water-year total. Each source uses its own input interval covering
  that day, so rice and nonponded crops can come from different update dates.
- **Geography:** choose Element or Subregion. Subbasin mode appears only when
  an element-to-zone mapping was included. Subregions and subbasins need not
  coincide. Click one element, Shift-click to add/remove elements, or use box
  selection. Element selections are limited to 500 elements.
- **Chart:** shows the retained time series for the selection, not just July 1
  samples. Category/group controls determine the series shown. Input date
  details explain the intervals used for the map.
- **Acres and percentages:** acreage is summed over the selection. Percentages
  use its model-element area as the denominator, not the sum of visible crops.
  Missing input coverage is not zero; charts stop and CSV values remain blank
  where the selected source has ended.
- **Downloads:** CSV can contain charted groups or all individual categories;
  it includes selection identity, intervals, acres, and percentages. PNG saves
  the chart with its title, context, and legend. A download is a snapshot of
  the current selection, not a new IWFM input file.
- **Imagery year:** historical NAIP year is independent of land-use year. Latest
  NAIP and the current USGS basemap are updating services, not fixed historical
  archives. A photograph is context, not evidence of the model's acreage date.

Changing an input file does not update an existing HTML viewer. Rebuild it when
the source data change. Treat the embedded model data as part of the file when
deciding whom to share it with.

## I Have An IWFM Deck And Want To Build A Viewer

The recommended route is one project JSON and one build command. The
[SVSim project example](examples/svsim-project.json) describes paths and build
settings; the [SVSim presentation example](examples/svsim-presentation.json)
describes labels, groups, colors, and branding. Keep your edited copies and
outputs outside this repository.

### 1. Get The Files And Set Up Python

Use Python 3.10 or newer. If you downloaded a repository ZIP, extract it first;
you do not need Git to build a viewer. Open PowerShell and change to the folder
containing this README and [build_viewer.py](build_viewer.py).

Replace the first path below with your extracted repository folder. The virtual
environment is a separate Python installation for this tool; the commands use
it directly, so no activation or PowerShell execution-policy change is needed.

```powershell
Set-Location 'PATH_TO_IWFM_LAND_USE_VIEWER_REPOSITORY'
$Repo = (Get-Location).Path
$Environment = Join-Path $env:LOCALAPPDATA 'IWFM-Land-Use-Viewer/python-env'
python -m venv $Environment
$Python = Join-Path $Environment 'Scripts/python.exe'
& $Python -m pip install -r (Join-Path $Repo 'requirements-adapter.txt')
```

If Python is installed as `py` rather than `python`, use `py -3` in the
environment-creation command. Package installation requires internet access.
Do not upgrade the pinned reader independently of this repository.

### 2. Copy And Edit The SVSim Examples

Run this in the same PowerShell session. The new project folder is outside the
repository. Use another new folder name for a separate model or repeat build.

```powershell
$Project = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'IWFM-viewer-project'
New-Item -ItemType Directory -Path $Project -ErrorAction Stop | Out-Null
Copy-Item (Join-Path $Repo 'examples/svsim-project.json') (Join-Path $Project 'project.json')
Copy-Item (Join-Path $Repo 'examples/svsim-presentation.json') (Join-Path $Project 'presentation.json')
notepad (Join-Path $Project 'project.json')
```

In your project JSON, edit:

| Setting | What To Supply Or Check |
|---|---|
| `inputs.rootzone_main` | Replace `PATH_TO_SVSIM_MODEL` with the extracted deck path; preserve the root-zone filename for SVSim. |
| `inputs.preprocessor_main` | Point to the preprocessor control file, which references the node and element files. |
| `inputs.simulation_main` | Point to the simulation control file; its `BDT` supplies the beginning of coverage. |
| `presentation_config` | Replace the placeholder with `presentation.json`, the copy beside your project JSON. |
| `map.start_year` | First display year; 2000 is covered by the tested SVSim v1.0 inputs. The final year comes from input coverage. |
| `coordinates` | Verify datum, projection, hemisphere, zone, and native coordinate units from model documentation or GIS metadata. |
| `units.model_area` | Verify the model uses square feet; also check node and area conversion factors described below. |
| `imagery` | `current` for USGS imagery only, or `historical-naip` for the historical/latest selector. |
| `output_html` | Keep `output/svsim-land-use.html`, or choose a new external output path. |

All relative paths are relative to **project.json**, even when you run the
command from another directory. In JSON, write Windows paths with forward
slashes, such as `C:/Models/SVSim`, or escape backslashes as `C:\\Models\\SVSim`.

The checked SVSim v1.0 deck uses NAD83 UTM Zone 10N in metres, node `FACT=3.2808`,
model feet, and area `FACT=43560`. Its node-file documentation and GIS projection
metadata agree. Do not apply those settings to another model without checking.

Optionally open your `presentation.json` in Notepad to change `model_title` and
`export_filename_prefix`. That file uses the existing category schema; labels
do not assign acreage columns. See the [configuration reference](README_landuse_regeneration.md#category-and-branding-configuration)
for group definitions and a C2VSimFG example.

### 3. Build And Open The Viewer

Run in the same PowerShell session:

```powershell
& $Python (Join-Path $Repo 'build_viewer.py') --config (Join-Path $Project 'project.json')
Invoke-Item (Join-Path $Project 'output/svsim-land-use.html')
```

If you changed `output_html`, open the absolute path printed after
`Finished HTML:` instead. The final file needs no accompanying database or
model files. By default the intermediate snapshot is temporary and removed.

An existing HTML file is protected by default. Choose a new output name rather
than enabling overwrite unless replacement is intentional. A failed export
preserves existing HTML and publishes no partial final file. Default safe
publication needs hard-link support, such as NTFS; see the
[output-safety reference](README_landuse_regeneration.md#project-settings-and-output-safety)
for explicit overwrite, retained snapshots, and retry behavior.

### Supported Inputs And Limitations

The recommended build currently supports:

- Root-zone input format **4.11**, using exactly **iwfm-io 2.15.2**. This is the
  component format header, not the IWFM executable's release number.
- Inline land-use time series with no repetition and no DSS input. Fractions
  (`FACT=0`) and other root-zone formats are not supported by this workflow.
- NAD83/GRS80 UTM north coordinates in metres, with an explicitly supplied zone.
  It is not a general CRS conversion tool and does not infer CRS from filenames.
- Model square feet: preprocessor `UNITLTOU=FEET` and `FACTLTOU=1`, node factor
  near 3.28084, and area `FACT=1` (square feet) or `43560` (acres).
- Different nonponded counts/orders from `NCROP`/`CCODE`; the other categories
  follow the v4.11 slots. Descriptions are labels only. Unknown names use neutral
  labels; categories present with zero acreage remain present.

Subbasins require an optional **verified element-to-zone definition**. They are
not inferred from a GIS boundary or a model name. Leave them off for the SVSim
quick start. The final map year uses common source coverage; charts can show a
later interval for a source that continues after the others end.

The original scripts still default to the legacy C2VSimFG reader. That path has
fixed category and unit assumptions and is not the recommended generic build.
For existing commands, SQLite snapshots, static hosting, and advanced options,
use [the regeneration reference](README_landuse_regeneration.md). For changes
to this repository, see [CONTRIBUTING.md](CONTRIBUTING.md).