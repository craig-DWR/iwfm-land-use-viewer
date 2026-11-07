# iwfm-land-use-viewer
Build standalone interactive maps from IWFM land-use inputs. Initially developed for C2VSimFG.

## Experimental Input Adapter

`iwfm_input_adapter.py` is independently usable and is not connected to either
viewer entry point, the standalone exporter, or the existing build commands.
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
