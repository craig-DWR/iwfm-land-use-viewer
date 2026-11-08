import json
import tempfile
import unittest
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from build_viewer import ProjectError, load_project


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        for name in ["root.dat", "pp.in", "simulation.in", "presentation.json"]:
            (self.directory / name).write_text("{}", encoding="utf-8")
        self.path = self.directory / "project.json"
        self.config = {
            "version": 1,
            "reader": "iwfm-io",
            "inputs": {
                "rootzone_main": "root.dat",
                "preprocessor_main": "pp.in",
                "simulation_main": "simulation.in",
            },
            "presentation_config": "presentation.json",
            "map": {"start_year": 2000},
            "coordinates": {
                "datum": "NAD83",
                "projection": "UTM",
                "hemisphere": "north",
                "zone": 10,
                "units": "metres",
            },
            "units": {"model_area": "square_feet"},
            "imagery": "current",
            "output_html": "output/viewer.html",
        }

    def load(self):
        self.path.write_text(json.dumps(self.config), encoding="utf-8")
        return load_project(self.path)

    def test_relative_paths_are_relative_to_json(self):
        project = self.load()
        self.assertEqual(project.rootzone_main, self.directory / "root.dat")
        self.assertEqual(
            project.presentation_config, self.directory / "presentation.json"
        )
        self.assertEqual(project.output_html, self.directory / "output/viewer.html")

    def test_missing_input_identifies_setting_and_path(self):
        self.config["inputs"]["rootzone_main"] = "missing.dat"
        with self.assertRaisesRegex(ProjectError, r"inputs.rootzone_main.*missing.dat"):
            self.load()

    def test_existing_html_is_not_overwritten(self):
        output = self.directory / "viewer.html"
        output.write_text("keep me", encoding="utf-8")
        self.config["output_html"] = "viewer.html"
        with self.assertRaisesRegex(ProjectError, "already exists.*viewer.html"):
            self.load()
        self.assertEqual(output.read_text(), "keep me")
        self.config["overwrite"] = True
        self.assertTrue(self.load().overwrite)

    def test_unsupported_coordinate_settings_are_explicit(self):
        self.config["coordinates"]["units"] = "feet"
        with self.assertRaisesRegex(ProjectError, "coordinates.units.*metres"):
            self.load()

    def test_nonempty_snapshot_directory_is_protected(self):
        self.config["snapshot_directory"] = "."
        with self.assertRaisesRegex(ProjectError, "snapshot_directory.*new or empty"):
            self.load()

    def test_snapshot_index_cannot_be_the_final_html(self):
        self.config["snapshot_directory"] = "retained"
        self.config["output_html"] = "retained/index.html"
        with self.assertRaisesRegex(ProjectError, "conflicts with snapshot_directory"):
            self.load()

    def test_unsupported_reader_does_not_fall_back(self):
        self.config["reader"] = "other"
        with self.assertRaisesRegex(ProjectError, "reader.*iwfm-io"):
            self.load()

    def prepare_deck(self):
        import test_iwfm_input_adapter as fixtures

        fixture = fixtures.AdapterTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for path in fixture.directory.iterdir():
            shutil.copy2(path, self.directory / path.name)
        (self.directory / "nodes.dat").write_text(
            "4\n3.2808\n1 500000 4400000\n2 500100 4400000\n3 500100 4400100\n4 500000 4400100\n",
            encoding="utf-8",
        )
        (self.directory / "elements.dat").write_text(
            "2\n1\nRegion / RNAME1\n1 1 2 3 0 1\n2 1 3 4 0 1\n", encoding="utf-8"
        )
        (self.directory / "pp.in").write_text(
            "Fixture\n / 1: BINARY\nelements.dat / 2: ELEMENT\nnodes.dat / 3: NODE\n"
            " / 4: STRATA\n / 5: STREAM\n / 6: LAKE\n0\n0\n1\nFEET\n0.000022957\nACRES\n",
            encoding="utf-8",
        )
        presentation = {
            "version": 1,
            "model_title": "Fixture land use",
            "categories": {},
            "groups": [],
            "initial_map_category": "nonponded:OR",
        }
        (self.directory / "presentation.json").write_text(
            json.dumps(presentation), encoding="utf-8"
        )
        self.config["inputs"].pop("simulation_main")
        self.config["coverage_start"] = "2018-10-01"
        self.config["map"]["start_year"] = 2019

    def test_one_command_from_another_working_directory(self):
        self.prepare_deck()
        project = self.load()
        command = Path(__file__).resolve().parents[1] / "build_viewer.py"
        with tempfile.TemporaryDirectory() as working:
            result = subprocess.run(
                [sys.executable, "-B", str(command), "--config", str(self.path)],
                cwd=working,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(project.output_html), result.stdout)
        self.assertIn("Intermediate snapshot removed", result.stdout)
        html = project.output_html.read_text(encoding="utf-8")
        payload = re.search(
            r'<script id="data-manifest" type="application/json">(.*?)</script>',
            html,
            re.S,
        ).group(1)
        manifest = json.loads(payload)
        self.assertEqual(
            manifest["model_configuration"]["model_title"], "Fixture land use"
        )
        self.assertEqual(
            manifest["codes"]["nonponded"],
            ["nonponded:OR", "nonponded:GR", "nonponded:AL"],
        )
        self.assertEqual((manifest["start_year"], manifest["last_year"]), (2019, 2019))
        self.assertFalse(list(project.output_html.parent.glob(".iwfm-viewer-export-*")))

    def test_requested_snapshot_is_retained(self):
        from build_viewer import build_project

        self.prepare_deck()
        self.config["snapshot_directory"] = "retained"
        project = self.load()
        build_project(project)
        self.assertTrue((project.snapshot_directory / "landuse.sqlite").is_file())
        self.assertTrue(project.output_html.is_file())

    def test_unsupported_source_factor_fails_before_snapshot_creation(self):
        from build_viewer import build_project

        self.prepare_deck()
        area = self.directory / "nonponded_area.dat"
        area.write_text(
            area.read_text().replace("43560 / FACTLNNP", "0 / FACTLNNP"),
            encoding="utf-8",
        )
        self.config["snapshot_directory"] = "retained"
        project = self.load()
        with self.assertRaisesRegex(
            ProjectError, "unsupported nonponded area settings.*nonponded_area.dat"
        ):
            build_project(project)
        self.assertFalse(project.snapshot_directory.exists())
        self.assertFalse(project.output_html.exists())

    def test_timezone_coverage_start_is_actionable(self):
        from build_viewer import preflight

        self.prepare_deck()
        self.config["coverage_start"] = "2018-10-01T00:00:00+00:00"
        with self.assertRaisesRegex(ProjectError, "coverage_start.*without a timezone"):
            preflight(self.load())

    def test_missing_dependencies_name_the_install_command(self):
        project = self.load()
        command = Path(__file__).resolve().parents[1] / "build_viewer.py"
        result = subprocess.run(
            [sys.executable, "-S", "-B", str(command), "--config", str(self.path)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pip install -r requirements-adapter.txt", result.stderr)
        self.assertFalse(project.output_html.exists())

    def fake_build_and_export(self, command, *, check):
        if "build" in command:
            snapshot = Path(command[command.index("--output") + 1])
            snapshot.mkdir(parents=True, exist_ok=True)
            (snapshot / "retained-marker").write_text(
                "user directory must remain", encoding="utf-8"
            )
        else:
            html = Path(command[command.index("--html") + 1])
            html.write_text("complete HTML", encoding="utf-8")

    def test_failed_export_preserves_existing_html_and_user_snapshot_directory(self):
        from build_viewer import build_project

        self.config.update(
            output_html="existing.html", overwrite=True, snapshot_directory="retained"
        )
        output = self.directory / "existing.html"
        output.write_bytes(b"original HTML")
        retained = self.directory / "retained"
        retained.mkdir()
        project = self.load()

        def fail_export(command, *, check):
            self.fake_build_and_export(command, check=check)
            if "--html" in command:
                Path(command[command.index("--html") + 1]).write_text(
                    "partial export", encoding="utf-8"
                )
                raise subprocess.CalledProcessError(1, command)

        with (
            patch("build_viewer.preflight"),
            patch("build_viewer.subprocess.run", side_effect=fail_export),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                build_project(project)
        self.assertEqual(output.read_bytes(), b"original HTML")
        self.assertTrue(retained.is_dir())
        self.assertTrue((retained / "retained-marker").is_file())
        self.assertFalse(list(self.directory.glob(".iwfm-viewer-export-*")))

    def test_failed_export_does_not_create_final_html(self):
        from build_viewer import build_project

        project = self.load()

        def fail_export(command, *, check):
            self.fake_build_and_export(command, check=check)
            if "--html" in command:
                raise subprocess.CalledProcessError(1, command)

        with (
            patch("build_viewer.preflight"),
            patch("build_viewer.subprocess.run", side_effect=fail_export),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                build_project(project)
        self.assertFalse(project.output_html.exists())
        self.assertFalse(list(project.output_html.parent.glob(".iwfm-viewer-export-*")))

    def test_publication_failure_cannot_leave_a_partial_final_file(self):
        from build_viewer import build_project

        project = self.load()
        with (
            patch("build_viewer.preflight"),
            patch(
                "build_viewer.subprocess.run", side_effect=self.fake_build_and_export
            ),
            patch(
                "build_viewer.os.link", side_effect=OSError("unsupported filesystem")
            ),
        ):
            with self.assertRaisesRegex(
                ProjectError, "atomic no-overwrite publication failed"
            ):
                build_project(project)
        self.assertFalse(project.output_html.exists())

    def test_output_created_during_build_is_not_overwritten(self):
        from build_viewer import build_project

        project = self.load()

        def racing_export(command, *, check):
            self.fake_build_and_export(command, check=check)
            if "--html" in command:
                project.output_html.write_text("other process HTML", encoding="utf-8")

        with (
            patch("build_viewer.preflight"),
            patch("build_viewer.subprocess.run", side_effect=racing_export),
        ):
            with self.assertRaisesRegex(ProjectError, "appeared during build"):
                build_project(project)
        self.assertEqual(project.output_html.read_text(), "other process HTML")

    def test_snapshot_conflict_during_preflight_is_rejected_without_cleanup(self):
        from build_viewer import build_project

        self.config["snapshot_directory"] = "retained"
        project = self.load()

        def changed_directory(project):
            project.snapshot_directory.mkdir()
            (project.snapshot_directory / "landuse.sqlite").write_text(
                "do not replace", encoding="utf-8"
            )

        with (
            patch("build_viewer.preflight", side_effect=changed_directory),
            patch("build_viewer.subprocess.run") as run,
        ):
            with self.assertRaisesRegex(ProjectError, "became nonempty"):
                build_project(project)
        run.assert_not_called()
        self.assertEqual(
            (project.snapshot_directory / "landuse.sqlite").read_text(),
            "do not replace",
        )


if __name__ == "__main__":
    unittest.main()
