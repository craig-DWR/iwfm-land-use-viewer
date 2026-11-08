
# IWFM Land Use Viewer

The IWFM Land Use Viewer maps and charts acreage from your model's land use
input files. It creates an HTML file you can open in a browser and share.
You do not need to run IWFM.

Building on this computer for the first time? Start with the first section,
even if someone already sent you an HTML viewer. If they also sent you the
viewer settings, you can reuse them.

1. [Build a viewer on this computer for the first time](#build-a-viewer-on-this-computer-for-the-first-time)
2. [Rebuild after updating land use](#rebuild-after-updating-land-use)
3. [Open a viewer someone sent me](#open-a-viewer-someone-sent-me)

## Build A Viewer On This Computer For The First Time

### What You Need

- The complete IWFM model package, including the land use area files you want
  to display. Make sure its input references lead to those updated files.
- The viewer tools from this repository. On its GitHub page, choose **Code >
  Download ZIP**, then right-click the downloaded ZIP and choose **Extract All**.
  Keep the extracted folder together. Git and VS Code are not required.
- Viewer settings: a **settings file (project.json)** and the presentation file
  it references. Use the files someone sent you, or start with an example below.
  If saved settings use ZoneBudget zones, also have that definition file available.

**The HTML viewer is the finished output. You don't need the old HTML to build
a new one, and the HTML by itself isn't enough to rebuild.**

### Set Up The Viewer Tools Once Per Windows Account

If Python is not already installed, install Python 3.10 or newer. The commands
below use your existing Python to create a separate environment for the viewer
and install the packages it needs. This keeps those packages separate from
your other modeling tools. The environment is created on your computer; it
is not included in the GitHub download.

For a different IWFM model, read the [compatibility checklist](#a-different-iwfm-model)
before installing. Both supplied-settings and example-settings paths use this
same setup. You do not need to activate the environment.

Open PowerShell from the Windows Start menu. Replace `PATH_TO_VIEWER_REPOSITORY`
with the full path to the extracted folder containing this README and
[build_viewer.py](build_viewer.py). Run this block from that folder:

```powershell
Set-Location 'PATH_TO_VIEWER_REPOSITORY'
$Environment = Join-Path $env:LOCALAPPDATA 'IWFM-Land-Use-Viewer/python-env'
python -m venv $Environment
& (Join-Path $Environment 'Scripts/python.exe') -m pip install -r .\requirements-adapter.txt
```

If Python is installed as `py` rather than `python`, use `py -3 -m venv
$Environment` on one line for the creation step. Package installation needs
internet access. No PowerShell execution-policy change or editor extension is needed.
The shared build command below finds this environment in a new window.

When you edit the paths in project.json, use this Windows path format:
**Inside `project.json`, use forward slashes for Windows paths, such as `C:/Model Work/SVSim`. Keep the quotes around the path.**

For example, in the SVSim project file, replace this complete root-zone line:

```json
"rootzone_main": "PATH_TO_SVSIM_MODEL/Simulation/RootZone/SVSim_RootZone.dat",
```

with this line, using your actual model folder:

```json
"rootzone_main": "C:/Model Work/SVSim/Simulation/RootZone/SVSim_RootZone.dat",
```

### A. I Already Have The Viewer Settings

Keep project.json and the files it references available. It tells the builder
which model files to read and where to save the HTML. Its companion presentation
file controls how crop names and groups appear.

If relative paths still match your folder layout, leave them alone. Otherwise
check `inputs.simulation_main`, `inputs.preprocessor_main`,
`inputs.rootzone_main`, `presentation_config`, and `inputs.zone_definition`
if subbasins were configured. Relative paths start from the folder containing
project.json: for example, `presentation_config: "presentation.json"` in
`C:/Model Work/Viewer Settings/project.json` means
`C:/Model Work/Viewer Settings/presentation.json`.

Choose a distinct `output_html` name if you want to keep the previous viewer.
You do not need to recreate crop labels or groups that already match your model.
Go straight to [C. Build and open the viewer](#c-build-and-open-the-viewer).

### B. I Don't Have The Viewer Settings

If you weren't given viewer settings, start with one of the examples below.
Choose the example for your model version, copy both settings files into a
folder for your project, and edit project.json to point to your local model
files. Keep the two settings files together. Then follow the build command below.

Each pair has a project settings file and its matching presentation file. The
presentation reference already works when the pair is copied together, even
if you rename the project file to project.json. The examples need no ZoneBudget
definition. Do not mix pairs or assume their crop groups, coordinates, or file
layouts fit another model.

#### C2VSimFG v1.5

In File Explorer, open the extracted viewer repository and then its `examples`
folder. Copy [c2vsimfg-v1.5-project.json](examples/c2vsimfg-v1.5-project.json)
and [c2vsimfg.json](examples/c2vsimfg.json) into a new viewer-settings folder
outside the repository. Rename `c2vsimfg-v1.5-project.json` to `project.json`.
Leave `c2vsimfg.json` unchanged, and open `project.json` in Notepad.

Replace `PATH_TO_C2VSIMFG_V1_5_MODEL` in all three input paths with your complete
**v1.5** model folder. Leave `presentation_config: "c2vsimfg.json"` alone.
For that release, the included UTM 10N/metre settings, model square feet, first
display year 2000, current imagery, and crop groups can remain as supplied.
The output is `output/c2vsimfg-v1.5-land-use.html` under your project folder.
Use a new output name if it already exists, then go to
[C. Build and open the viewer](#c-build-and-open-the-viewer).

#### Optional ZoneBudget zones

With no ZoneBudget file, the viewer explores by Element or Subregion. To use
zones, add `zone_definition` inside `inputs` in project.json, for example:

```json
"zone_definition": "PATH_TO_MODEL/ZoneBudget/ZBudget_Counties.DAT"
```

A custom type such as County replaces the Subbasin option; it does not add a
fourth mode. Use a horizontal definition (`ZEXTENT=1`) with at most one zone
assignment per element. The viewer sums whole-element acreage, so it cannot use
layer-varying zones or split an element across boundaries. For a layered budget,
create a separate horizontal definition. Set the display terms and names in the
presentation file; see the [worked ZoneBudget example](README_landuse_regeneration.md#configurable-zonebudget-zones).
The C2VSimFG example's DWR names match its standard v1.5 definition only; update
or remove those names if you use a different assignment file.

#### SVSim v1.0

In File Explorer, open the extracted viewer repository and then its `examples`
folder. Copy [svsim-project.json](examples/svsim-project.json) and
[svsim-presentation.json](examples/svsim-presentation.json) into a new
viewer-settings folder outside the repository. Rename `svsim-project.json` to
`project.json`. Leave `svsim-presentation.json` unchanged, and open `project.json`
in Notepad.

Replace `PATH_TO_SVSIM_MODEL` in all three input paths with your complete
**v1.0** model folder. Leave `presentation_config: "svsim-presentation.json"`
alone. For that release, the included UTM 10N/metre settings, model square feet,
first display year 2000, current imagery, and crop groups can remain as supplied.
The output is `output/svsim-v1.0-land-use.html` under your project folder.
Use a new output name if it already exists, then go to
[C. Build and open the viewer](#c-build-and-open-the-viewer).

#### A Different IWFM Model

Check compatibility before installing or adapting settings:

- The root-zone main input must declare **format 4.11**. This is the component
  format, not the IWFM executable's release number.
- Node coordinates must be **NAD83/GRS80 UTM north in metres**, with the actual
  zone verified from model documentation or GIS projection metadata.
- Model units must be feet/square feet, with preprocessor `UNITLTOU=FEET` and
  `FACTLTOU=1`, a metre-to-foot node factor of about `3.28084`, and area
  `FACT=1` or `43560`. Element-area fractions (`FACT=0`) are unsupported.
- Land use must be inline time-series input, not DSS or a repeating pattern.

See [the full restrictions](README_landuse_regeneration.md#project-settings-and-output-safety)
if any setting differs. Use the following table to identify your own model files:

| Setting | File Or Value To Supply | How To Identify It |
| --- | --- | --- |
| `inputs.simulation_main` | Simulation main input file | Its file list references components, including root zone; `BDT` supplies the coverage beginning. |
| `inputs.preprocessor_main` | Preprocessor main input file | Its file list references nodes/elements; unit settings help verify the model's feet/square feet basis. |
| `inputs.rootzone_main` | Root-zone main input file | The simulation main references it. It declares format 4.11 and references components, which reference the area files. |
| `presentation_config` | A presentation JSON beside project.json, or a valid path to it | Required before building. Use existing settings for your model or the minimal starting file below. |
| `map.start_year` | Your first display year | Verify all sources cover July 1 and the chart's January 1 start; final map year comes from common coverage. |
| `coordinates` | Your verified datum, projection, hemisphere, zone, and node units | Check model/GIS documentation. Neither model name nor filename identifies the CRS for the builder. |
| `units.model_area` | `square_feet`, if verified. | Supported preprocessor declarations are `UNITLTOU=FEET` and `FACTLTOU=1`. Verify node conversion and each area factor against the reference restrictions. |
| `imagery` | `current` or `historical-naip` | Choose current USGS imagery only, or the historical/latest NAIP selector. This does not set the land use year. |
| `output_html` | A new external HTML filename | Relative paths save under the folder containing project.json. The builder prints the absolute result. |
| `inputs.zone_definition` | Optional verified element-to-zone file | Omit for the basic build. Do not infer it from a subbasin polygon. |

A minimal presentation file without model-specific crop labels or groups works
**if your model has the urban component**. Save these exact contents as
`presentation.json` and set `presentation_config` to that filename:

```json
{
  "version": 1,
  "model_title": "IWFM land use",
  "export_filename_prefix": "IWFM_landuse",
  "categories": {},
  "groups": [],
  "initial_map_category": "urban:urban"
}
```

If urban is absent, change only `initial_map_category` to an existing category
ID. For example, `nonponded:GR` is valid only if `GR` is in your nonponded main's
`CCODE` list. There is no universal initial ID for every supported model. The
builder validates these references; missing names use model descriptions or
neutral labels. Do not borrow another model's crop groups without checking them.

### C. Build And Open The Viewer

If you followed the setup above, leave the first part alone. Replace the two
example paths with the locations of [build_viewer.py](build_viewer.py) and your project.json.
The first part finds the Python created during setup; `--config` points to your
saved settings file. Run from **any folder in a new PowerShell window**:

```powershell
& (Join-Path $env:LOCALAPPDATA 'IWFM-Land-Use-Viewer/python-env/Scripts/python.exe') 'PATH_TO_VIEWER_REPOSITORY/build_viewer.py' --config 'PATH_TO_SAVED_VIEWER_SETTINGS/project.json'
```

For example, with these illustrative Windows folders containing spaces:

```powershell
& (Join-Path $env:LOCALAPPDATA 'IWFM-Land-Use-Viewer/python-env/Scripts/python.exe') 'C:\Model Tools\IWFM Land Use Viewer\build_viewer.py' --config 'C:\Model Work\Viewer Settings\project.json'
```

Open the file listed after **`Finished HTML:`**. The builder stops by default if
that output file already exists; a new `output_html` name keeps the previous
viewer available. Spot-check a crop, location, and year you know you changed to
confirm the model references picked up your updated area files. Keep your
settings files for the next update. Advanced output choices and optional colors,
labels, crop groups, and subbasins are in the
[regeneration reference](README_landuse_regeneration.md).

## Rebuild After Updating Land Use

Use these steps if you've already set up the viewer tools on this computer and
have working settings for this model. It doesn't matter who built the original
viewer.

1. Use the complete model folder and make sure its IWFM input references point
   to the updated land use area files. The viewer does not look for the newest
   file in a folder.
2. If files are still in the same places, keep the input paths and change
   `output_html` to save a new copy. For a new model folder, check the simulation,
   preprocessor, and root-zone main paths, the presentation reference, and any
   subbasin definition. Routine acreage updates do not require changing crop
   labels, colors, or groups.
3. Run the [shared build command](#c-build-and-open-the-viewer), open the new
   HTML, and spot-check a known acreage change. Skip Python setup if it is
   already done.

## Open A Viewer Someone Sent Me

Double-click the HTML file or open it in a current Edge, Chrome, or Firefox
browser. No Python, Git, or model files are needed. The map and charts work
offline; aerial imagery needs internet access and can be turned off.

The map year shows the condition on **July 1**, not an annual average. The chart
shows the retained land use time series. Aerial imagery year is independent of
land use year. See [viewer controls and interpretation](#viewer-controls-and-interpretation)
for selection and export details. The HTML contains model data, so consider
that when sharing it.

## Viewer Controls And Interpretation

- Choose Element, Subregion, or Subbasin when a verified mapping was included.
  Subregions and subbasins need not coincide. Go-to suggestions follow the mode.
- Click an element, Shift-click to add/remove elements, or use box selection.
  Element selections are limited to 500 elements.
- The July 1 map queries each source's own interval. The chart keeps source
  update dates rather than replacing the time series with annual samples.
- Acres sum over the selection. Percentages use its model-element area, not the
  sum of the displayed crops. Missing input coverage stays distinct from zero.
- CSV can contain charted groups or all categories, including selection identity,
  intervals, acres, and percentages. PNG saves the chart, context, and legend.
- Historical NAIP choices are independent of land use dates. Latest NAIP and the
  current USGS basemap are updating services, not fixed historical archives.

For existing commands, intermediate snapshots, advanced options, and output
troubleshooting, use [the regeneration reference](README_landuse_regeneration.md).
For repository changes and test commands, see [CONTRIBUTING.md](CONTRIBUTING.md).
