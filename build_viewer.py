"""Build a standalone viewer from one project JSON using the existing workflow."""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    config_path: Path
    rootzone_main: Path
    preprocessor_main: Path
    simulation_main: Path | None
    presentation_config: Path
    zone_definition: Path | None
    coverage_start: str | None
    start_year: int
    utm_zone: int
    imagery: str
    output_html: Path
    snapshot_directory: Path | None
    overwrite: bool


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProjectError(f"config: cannot read JSON at {path}: {error}") from error
    if not isinstance(value, dict):
        raise ProjectError(f"config: expected a JSON object at {path}")
    return value


def fields(value, required, optional, setting):
    if not isinstance(value, dict):
        raise ProjectError(f"{setting}: expected an object")
    missing = set(required) - set(value)
    unknown = set(value) - set(required) - set(optional)
    if missing or unknown:
        raise ProjectError(
            f"{setting}: missing fields {sorted(missing)}; unsupported fields {sorted(unknown)}"
        )


def resolve_path(base, value, setting, *, kind=None):
    """Interpret project paths beside the JSON, not beside the Python script."""
    if not isinstance(value, str) or not value.strip():
        raise ProjectError(
            f"{setting}: supply a nonempty path relative to the project JSON or an absolute path"
        )
    path = Path(value.replace("\\", "/"))
    path = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if kind == "file" and not path.is_file():
        raise ProjectError(f"{setting}: input file does not exist: {path}")
    return path


def load_project(config_path):
    path = Path(config_path).resolve()
    config = read_json(path)
    fields(
        config,
        [
            "version",
            "reader",
            "inputs",
            "presentation_config",
            "map",
            "coordinates",
            "units",
            "imagery",
            "output_html",
        ],
        ["coverage_start", "snapshot_directory", "overwrite"],
        "project",
    )
    if type(config["version"]) is not int or config["version"] != 1:
        raise ProjectError("version: only project format 1 is supported")
    if config["reader"] != "iwfm-io":
        raise ProjectError(
            "reader: supported value is iwfm-io (exactly 2.15.2); existing legacy commands remain available"
        )
    inputs = config["inputs"]
    fields(
        inputs,
        ["rootzone_main", "preprocessor_main"],
        ["simulation_main", "zone_definition"],
        "inputs",
    )
    base = path.parent
    root = resolve_path(
        base, inputs["rootzone_main"], "inputs.rootzone_main", kind="file"
    )
    pp = resolve_path(
        base, inputs["preprocessor_main"], "inputs.preprocessor_main", kind="file"
    )
    simulation = (
        resolve_path(
            base, inputs["simulation_main"], "inputs.simulation_main", kind="file"
        )
        if inputs.get("simulation_main")
        else None
    )
    zone = (
        resolve_path(
            base, inputs["zone_definition"], "inputs.zone_definition", kind="file"
        )
        if inputs.get("zone_definition")
        else None
    )
    presentation = resolve_path(
        base, config["presentation_config"], "presentation_config", kind="file"
    )
    if not simulation and not config.get("coverage_start"):
        raise ProjectError(
            "inputs.simulation_main or coverage_start: supply an authoritative coverage beginning"
        )
    if config.get("coverage_start") is not None and (
        not isinstance(config["coverage_start"], str)
        or not config["coverage_start"].strip()
    ):
        raise ProjectError(
            "coverage_start: expected a nonempty ISO date/datetime or IWFM timestamp string"
        )
    fields(config["map"], ["start_year"], [], "map")
    year = config["map"]["start_year"]
    if type(year) is not int or not 1 <= year <= 9999:
        raise ProjectError("map.start_year: expected an integer calendar year")
    coordinates = config["coordinates"]
    fields(
        coordinates,
        ["datum", "projection", "hemisphere", "zone", "units"],
        [],
        "coordinates",
    )
    for key, expected in [
        ("datum", "NAD83"),
        ("projection", "UTM"),
        ("hemisphere", "north"),
        ("units", "metres"),
    ]:
        if coordinates[key] != expected:
            raise ProjectError(
                f"coordinates.{key}: current geometry requires {expected!r}; found {coordinates[key]!r}"
            )
    utm_zone = coordinates["zone"]
    if type(utm_zone) is not int or not 1 <= utm_zone <= 60:
        raise ProjectError(
            "coordinates.zone: expected an integer UTM zone from 1 through 60"
        )
    fields(config["units"], ["model_area"], [], "units")
    if config["units"]["model_area"] != "square_feet":
        raise ProjectError(
            "units.model_area: current input normalization requires verified square_feet"
        )
    if config["imagery"] not in ("current", "historical-naip"):
        raise ProjectError("imagery: supported values are current and historical-naip")
    output = resolve_path(base, config["output_html"], "output_html")
    overwrite = config.get("overwrite", False)
    if type(overwrite) is not bool:
        raise ProjectError("overwrite: expected true or false")
    if output.suffix.lower() not in (".html", ".htm"):
        raise ProjectError(f"output_html: use an .html or .htm filename: {output}")
    if output.exists() and (not output.is_file() or not overwrite):
        raise ProjectError(
            f"output_html: already exists: {output}; choose a new path or explicitly set overwrite=true"
        )
    for parent in output.parents:
        if parent.exists() and not parent.is_dir():
            raise ProjectError(f"output_html: parent is not a directory: {parent}")
    snapshot = (
        resolve_path(base, config["snapshot_directory"], "snapshot_directory")
        if config.get("snapshot_directory")
        else None
    )
    if (
        snapshot
        and snapshot.exists()
        and (not snapshot.is_dir() or any(snapshot.iterdir()))
    ):
        raise ProjectError(f"snapshot_directory: must be new or empty: {snapshot}")
    if snapshot:
        if (
            snapshot == output
            or snapshot.is_relative_to(output)
            or output == snapshot / "index.html"
        ):
            raise ProjectError(
                f"output_html: conflicts with snapshot_directory or its index.html: {output}"
            )
        for parent in snapshot.parents:
            if parent.exists() and not parent.is_dir():
                raise ProjectError(
                    f"snapshot_directory: parent is not a directory: {parent}"
                )
    if output in [path, root, pp, simulation, zone, presentation]:
        raise ProjectError(
            f"output_html: conflicts with a configuration or input path: {output}"
        )
    return Project(
        path,
        root,
        pp,
        simulation,
        presentation,
        zone,
        config.get("coverage_start"),
        year,
        utm_zone,
        config["imagery"],
        output,
        snapshot,
        overwrite,
    )


def preflight(project):
    """Reject unverified build settings before creating intermediate snapshots."""
    context = f"inputs in {project.config_path}"
    try:
        from iwfm_build import AdapterBuild
        from iwfm_input_adapter import Category, FIXED_SLOTS, SOURCE_READERS
        from iwfm_io import parse_iwfm_date
        from iwfm_io._tokens import (
            is_comment,
            is_iwfm_date,
            is_version_header,
            split_keyed_line,
        )
        from iwfm_io.readers import preprocessor
        from model_configuration import resolve_configuration

        if project.coverage_start:
            context = f"coverage_start in {project.config_path}"
            start = (
                parse_iwfm_date(project.coverage_start)
                if "_" in project.coverage_start
                else datetime.fromisoformat(project.coverage_start)
            )
            if start.tzinfo is not None:
                raise ProjectError(
                    f"coverage_start: use a naive model datetime without a timezone in {project.config_path}"
                )
        context = f"inputs in {project.config_path}"
        args = SimpleNamespace(
            rootzone_main=str(project.rootzone_main),
            preprocessor_main=str(project.preprocessor_main),
            simulation_main=str(project.simulation_main)
            if project.simulation_main
            else None,
            coverage_start=project.coverage_start,
            start_year=project.start_year,
            model_config=str(project.presentation_config),
        )
        prepared = AdapterBuild(args)
        protected = [
            project.config_path,
            project.presentation_config,
            project.rootzone_main,
            project.preprocessor_main,
            project.simulation_main,
            project.zone_definition,
            Path(__file__).resolve().parent / "viewer_assets/base.html",
        ]
        for name in ["nodes", "elements"]:
            if not prepared.paths[name].is_file():
                raise ProjectError(
                    f"inputs.preprocessor_main: referenced {name} file unavailable: {prepared.paths[name]}"
                )
            protected.append(prepared.paths[name].resolve())
        nodes = preprocessor.read_nodes(prepared.paths["nodes"])
        if not math.isclose(
            float(nodes.factor.value), 1 / 0.3048, rel_tol=0, abs_tol=0.0001
        ):
            raise ProjectError(
                f"coordinates.units: declared metres with model feet requires node FACT approximately 3.28084; "
                f"found {nodes.factor.value} in {prepared.paths['nodes']}"
            )
        categories = []
        for source in prepared.adapter.sources:
            role, read_component = SOURCE_READERS[source]
            component_path = prepared.adapter._path(
                prepared.adapter.rootzone.file_paths.get(role),
                f"inputs.rootzone_main references {source}",
            )
            context = (
                f"inputs.rootzone_main references {source} component {component_path}"
            )
            component = read_component(
                component_path, n_elements=len(prepared.adapter.element_ids)
            )
            area_path = prepared.adapter._path(
                component.file_paths.get("land_use_area"), f"{source} area reference"
            )
            context = f"inputs.rootzone_main references {source} acreage {area_path}"
            protected.extend([component_path.resolve(), area_path.resolve()])
            spec = []
            final_endpoint = None
            with area_path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if is_comment(line) or is_version_header(line):
                        continue
                    value, annotation = split_keyed_line(line)
                    if len(spec) < 4:
                        spec.append(value.strip())
                        continue
                    token = line.split()[0] if line.split() else ""
                    if is_iwfm_date(token):
                        endpoint = parse_iwfm_date(token)
                        final_endpoint = endpoint
            if (
                len(spec) != 4
                or float(spec[0]) not in (1.0, 43560.0)
                or int(spec[2]) != 0
                or spec[3]
            ):
                raise ProjectError(
                    f"inputs.rootzone_main: unsupported {source} area settings in {area_path}; "
                    "require FACT=1 or 43560, no repetition, and blank DSSFL"
                )
            if final_endpoint is None or final_endpoint <= datetime(
                project.start_year, 7, 1
            ):
                raise ProjectError(
                    f"map.start_year: {project.start_year} July 1 is outside {source} final coverage: {area_path}"
                )
            codes = (
                tuple(component.crop_codes)
                if source == "nonponded"
                else tuple(code for code, label in FIXED_SLOTS[source])
            )
            categories.extend(
                Category(f"{source}:{code}", source, column, code, code, "preflight")
                for column, code in enumerate(codes, 1)
            )
        zone_ids = None
        zone_labels = None
        if project.zone_definition:
            context = f"inputs.zone_definition: {project.zone_definition}"
            name = project.zone_definition.stem.lower()
            if project.zone_definition.suffix.lower() not in (".dat", ".zip") or not (
                "zbudget" in name or "zonedef" in name
            ):
                raise ProjectError(
                    f"inputs.zone_definition: existing builder requires a ZBudget/ZoneDef DAT or ZIP filename: {project.zone_definition}"
                )
            from c2vsim_landuse_explorer_expanded import subbasins

            zone_labels, _ = subbasins(
                project.zone_definition, prepared.adapter.element_ids
            )
            zone_ids = set(zone_labels) - {-99}
            zone_labels = {
                zone_id: label
                for zone_id, label in zone_labels.items()
                if zone_id != -99
            }
        context = f"presentation_config: {project.presentation_config}"
        resolve_configuration(
            read_json(project.presentation_config),
            categories,
            zone_ids=zone_ids,
            zone_labels=zone_labels,
            zone_definition=project.zone_definition,
        )
        if project.output_html in protected:
            raise ProjectError(
                f"output_html: conflicts with an input or runtime asset: {project.output_html}"
            )
    except ProjectError:
        raise
    except ModuleNotFoundError as error:
        raise ProjectError(
            f"reader: install the pinned dependencies with python -m pip install -r requirements-adapter.txt; "
            f"project {project.config_path}: {error}"
        ) from error
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ProjectError(f"{context}: {error}") from error


def build_project(project):
    """Reuse build/export commands; publish completed HTML and own only temp cleanup."""
    preflight(project)
    if project.snapshot_directory:
        snapshot = project.snapshot_directory
        if snapshot.exists() and (not snapshot.is_dir() or any(snapshot.iterdir())):
            raise ProjectError(
                f"snapshot_directory: became nonempty or unavailable during preflight: {snapshot}"
            )
    scripts = Path(__file__).resolve().parent
    entry = scripts / (
        "c2vsim_landuse_explorer_naip.py"
        if project.imagery == "historical-naip"
        else "c2vsim_landuse_explorer_expanded.py"
    )
    with ExitStack() as stack:
        scratch = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="iwfm-viewer-"))
        )
        inputs = scratch / "inputs"
        inputs.mkdir()
        if project.zone_definition:
            shutil.copy2(project.zone_definition, inputs / project.zone_definition.name)
        snapshot = project.snapshot_directory or scratch / "snapshot"
        command = [
            sys.executable,
            "-B",
            str(entry),
            "build",
            "--reader",
            "iwfm-io",
            "--input",
            str(inputs),
            "--output",
            str(snapshot),
            "--start-year",
            str(project.start_year),
            "--utm-zone",
            str(project.utm_zone),
            "--rootzone-main",
            str(project.rootzone_main),
            "--preprocessor-main",
            str(project.preprocessor_main),
            "--model-config",
            str(project.presentation_config),
        ]
        if project.simulation_main:
            command += ["--simulation-main", str(project.simulation_main)]
        if project.coverage_start:
            command += ["--coverage-start", project.coverage_start]
        subprocess.run(command, check=True)
        project.output_html.parent.mkdir(parents=True, exist_ok=True)
        export_directory = Path(
            stack.enter_context(
                tempfile.TemporaryDirectory(
                    prefix=".iwfm-viewer-export-", dir=project.output_html.parent
                )
            )
        )
        html = export_directory / "viewer.html"
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(scripts / "export_landuse_standalone.py"),
                "--data",
                str(snapshot),
                "--viewer",
                str(entry),
                "--html",
                str(html),
            ],
            check=True,
        )
        if project.overwrite:
            os.replace(html, project.output_html)
        else:
            try:
                # Publish a complete file without clobbering another process's output.
                os.link(html, project.output_html)
            except FileExistsError as error:
                raise ProjectError(
                    f"output_html: appeared during build and was not overwritten: {project.output_html}"
                ) from error
            except OSError as error:
                raise ProjectError(
                    f"output_html: atomic no-overwrite publication failed at {project.output_html}: {error}; "
                    "use a filesystem supporting hard links or explicitly enable overwrite"
                ) from error
    print(f"Finished HTML: {project.output_html}")
    print(
        f"Snapshot retained: {project.snapshot_directory}"
        if project.snapshot_directory
        else "Intermediate snapshot removed (temporary build)."
    )
    return project.output_html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Project JSON; paths resolve relative to this file",
    )
    args = parser.parse_args()
    try:
        build_project(load_project(args.config))
    except (ProjectError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"build_viewer: {error}\n")


if __name__ == "__main__":
    main()
