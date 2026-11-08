import copy
import json
import sqlite3
import struct
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from landuse_viewer_backend import reconcile_dates, subbasins, zone_display_name
from iwfm_input_adapter import Category
from model_configuration import apply_legacy_zone_names, resolve_zone_configuration
from viewer_renderer import render_viewer


class ZoneDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "Fixture_ZoneDef.dat"
        self.valid_ids = {1, 2, 3}

    def parse(self, text):
        self.path.write_text(text, encoding="utf-8")
        return subbasins(self.path, self.valid_ids)

    def test_horizontal_numbered_zones_and_file_labels(self):
        names, membership = self.parse(
            "1 / ZEXTENT\n1 Colusa County\n2 Glenn County\n1 1\n2 1\n3 2\n"
        )
        self.assertEqual(names[1], "Colusa County")
        self.assertEqual(names[2], "Glenn County")
        self.assertEqual(membership, {1: 1, 2: 1, 3: 2})

    def test_horizontal_zones_do_not_require_descriptive_labels(self):
        names, membership = self.parse("1 / ZEXTENT\n1 3\n2 4\n")
        self.assertIsNone(names[3])
        self.assertIsNone(names[4])
        self.assertEqual(membership, {1: 3, 2: 4, 3: -99})

    def test_unassigned_sentinel_is_reserved_and_elements_are_not_duplicated(self):
        names, membership = self.parse("1 / ZEXTENT\n1 County A\n1 -99\n2 1\n")
        self.assertEqual(names[-99], "Unassigned (ZoneBudget -99)")
        self.assertEqual(membership, {1: -99, 2: 1, 3: -99})
        self.assertEqual(len(membership), len(self.valid_ids))
        with self.assertRaisesRegex(ValueError, "zone ID -99 is reserved"):
            self.parse("1 / ZEXTENT\n-99 Unassigned\n1 -99\n")

    def test_repeated_assignment_is_rejected_even_for_same_zone(self):
        with self.assertRaisesRegex(
            ValueError, r"row 4: element 1 is assigned more than once"
        ):
            self.parse("1\n1 County A\n1 1\n1 1\n")

    def test_unknown_element_is_rejected_with_row(self):
        with self.assertRaisesRegex(ValueError, r"row 3: unknown model element 9"):
            self.parse("1\n1 County A\n9 1\n")

    def test_undeclared_zone_is_rejected(self):
        with self.assertRaisesRegex(ValueError, r"row 4: zone 2 is not declared"):
            self.parse("1\n1 County A\n1 1\n2 2\n")

    def test_malformed_horizontal_assignment_is_not_skipped(self):
        with self.assertRaisesRegex(
            ValueError, r"row 4: malformed horizontal assignment"
        ):
            self.parse("1\n1 County A\n1 1\n2 1 extra\n")

    def test_empty_and_only_unassigned_definitions_are_rejected(self):
        for definition in ["1 / ZEXTENT\n", "1 / ZEXTENT\n1 -99\n"]:
            with self.subTest(definition=definition):
                with self.assertRaisesRegex(
                    ValueError, "no numbered zones or element assignments"
                ):
                    self.parse(definition)

    def test_layer_specific_definition_requires_horizontal_replacement(self):
        with self.assertRaisesRegex(
            ValueError,
            r"ZEXTENT=0.*whole model elements.*separate horizontal definition",
        ):
            self.parse("0 / ZEXTENT\n1 1 1\n")


class ZonePresentationTests(unittest.TestCase):
    def test_explicit_file_and_generic_name_precedence(self):
        result = resolve_zone_configuration(
            {
                "singular": "County",
                "plural": "Counties",
                "names": {"1": "Colusa County"},
            },
            zone_ids={1, 2, 3},
            zone_labels={1: "File label", 2: "Glenn County", 3: None},
        )
        self.assertEqual(result["names"]["1"], "Colusa County")
        self.assertEqual(result["names"]["2"], "Glenn County")
        self.assertEqual(result["names"]["3"], "County 3")
        self.assertEqual(result["names"]["-99"], "Unassigned (ZoneBudget -99)")

    def test_generic_defaults_and_reusable_names_without_a_definition(self):
        result = resolve_zone_configuration({"names": {"12": "Future County"}})
        self.assertEqual((result["singular"], result["plural"]), ("Zone", "Zones"))
        self.assertEqual(result["names"], {"12": "Future County"})

    def test_unknown_zone_name_id_is_rejected_when_definition_exists(self):
        with self.assertRaisesRegex(ValueError, "not declared or assigned.*2"):
            resolve_zone_configuration(
                {"names": {"2": "Not in this file"}}, zone_ids={1}
            )

    def test_zone_settings_types_ids_and_display_text_are_validated(self):
        invalid = [
            None,
            [],
            {"singular": "  "},
            {"plural": 3},
            {"names": []},
            {"names": {"not-an-id": "Name"}},
            {"names": {"-99": "Not a user zone"}},
            {"names": {"1": " "}},
            {"other": "unsupported"},
        ]
        for definition in invalid:
            with self.subTest(definition=definition):
                with self.assertRaises(ValueError):
                    resolve_zone_configuration(definition)


class LegacyZoneCompatibilityTests(unittest.TestCase):
    def test_old_c2_presentation_gets_standard_terms_only_for_verified_definition(self):
        from model_configuration import resolve_configuration

        categories = [Category("nonponded:GR", "nonponded", 1, "GR", "Grain", "test")]
        config = {
            "version": 1,
            "categories": {},
            "groups": [],
            "initial_map_category": "nonponded:GR",
        }
        c2 = resolve_configuration(
            config,
            categories,
            zone_ids={1, 2},
            zone_labels={1: "5-021.64", 2: "Suisun Bay"},
            zone_definition=Path("C2VSimFG_ZBudget_GWSubbasins.DAT"),
        )
        self.assertEqual(
            (c2["zones"]["singular"], c2["zones"]["plural"]), ("Subbasin", "Subbasins")
        )
        self.assertEqual(c2["zones"]["names"]["1"], "North American (5-021.64)")
        self.assertEqual(c2["zones"]["names"]["2"], "Suisun Bay")

        other = resolve_configuration(
            config,
            categories,
            zone_ids={1},
            zone_labels={1: "5-021.64"},
            zone_definition=Path("County_ZoneDef.DAT"),
        )
        self.assertEqual(
            (other["zones"]["singular"], other["zones"]["plural"]), ("Zone", "Zones")
        )
        self.assertEqual(other["zones"]["names"]["1"], "5-021.64")

    def test_bulletin118_names_are_limited_to_verified_c2_definition(self):
        legacy = {
            "start_year": 2020,
            "dates": {"nonponded": ["2021-10-01"]},
            "sources": {"subbasins": {"file": "C2VSimFG_ZBudget_GWSubbasins.DAT"}},
            "subbasins": {
                "37": "5-021.64",
                "46": "Suisun Bay",
                "-99": "Unassigned (ZoneBudget -99)",
            },
        }
        reconcile_dates(legacy)
        self.assertEqual(legacy["zone_display"]["singular"], "Subbasin")
        self.assertEqual(
            legacy["zone_display"]["names"]["37"], "North American (5-021.64)"
        )
        self.assertEqual(legacy["zone_display"]["names"]["46"], "Suisun Bay")
        self.assertEqual(zone_display_name(legacy, 37), "North American (5-021.64)")

        other = {
            "sources": {"subbasins": {"file": "County_ZoneDef.dat"}},
            "subbasins": {"1": "5-021.64"},
        }
        apply_legacy_zone_names(other)
        self.assertNotIn("zone_display", other)
        self.assertEqual(other["subbasins"]["1"], "5-021.64")
        self.assertEqual(zone_display_name(other, 1), "5-021.64")

        configured = {
            "sources": {"subbasins": {"file": "C2VSimFG_ZBudget_GWSubbasins.DAT"}},
            "subbasins": {"37": "5-021.64"},
            "model_configuration": {
                "zones": {"singular": "County", "names": {"37": "County A"}}
            },
        }
        apply_legacy_zone_names(configured)
        self.assertNotIn("zone_display", configured)
        self.assertEqual(zone_display_name(configured, 37), "County A")

    def test_old_c2_names_are_not_embedded_in_shared_html(self):
        for imagery in ("current", "historical-naip"):
            with self.subTest(imagery=imagery):
                html = render_viewer(imagery=imagery)
                self.assertIn("function zoneName(id)", html)
                self.assertNotIn("const basinNameByCode", html)
                self.assertNotIn('"5-021.64":"North American"', html)


class ZoneBuildTests(unittest.TestCase):
    def setUp(self):
        import test_iwfm_input_adapter as fixtures

        self.fixture = fixtures.AdapterTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.directory = self.fixture.directory
        self.inputs = self.directory / "flat"
        self.inputs.mkdir()
        (self.inputs / "Fixture_Nodes.dat").write_text(
            "4\n3.2808\n1 500000 4400000\n2 500100 4400000\n"
            "3 500100 4400100\n4 500000 4400100\n",
            encoding="utf-8",
        )
        (self.inputs / "Fixture_Elements.dat").write_text(
            "2\n1\nRegion / RNAME1\n1 1 2 3 0 1\n2 1 3 4 0 1\n",
            encoding="utf-8",
        )
        self.preprocessor = self.inputs / "pp.in"
        self.preprocessor.write_text(
            "Fixture\n / 1: BINARY\nFixture_Elements.dat / 2: ELEMENT\n"
            "Fixture_Nodes.dat / 3: NODE\n / 4: STRATA\n / 5: STREAM\n"
            " / 6: LAKE\n0\n0\n1\nFEET\n0.000022957\nACRES\n",
            encoding="utf-8",
        )
        self.zone_file = self.inputs / "Fixture_ZoneDef.dat"
        self.zone_file.write_text(
            "1 / ZEXTENT\n1 File County A\n2 File County B\n1 1\n2 2\n",
            encoding="utf-8",
        )

    def build(self, output, config, *, with_zone=True):
        import c2vsim_landuse_explorer_expanded as viewer

        config_path = self.directory / f"{output.name}-presentation.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        if not with_zone:
            self.zone_file.unlink(missing_ok=True)
        viewer.build(
            SimpleNamespace(
                input=str(self.inputs),
                output=str(output),
                start_year=2019,
                utm_zone=10,
                reader="iwfm-io",
                rootzone_main=str(self.fixture.root),
                preprocessor_main=str(self.preprocessor),
                simulation_main=None,
                coverage_start="2018-10-01",
                model_config=str(config_path),
            )
        )
        return json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    def test_zone_totals_match_element_membership_and_ignore_display_names(self):
        config = {
            "version": 1,
            "categories": {},
            "groups": [],
            "initial_map_category": "nonponded:OR",
            "zones": {
                "singular": "County",
                "plural": "Counties",
                "names": {"1": "Colusa County", "2": "Glenn County"},
            },
        }
        first = self.directory / "zone-first"
        manifest = self.build(first, config)
        self.assertEqual(
            manifest["model_configuration"]["zones"]["names"]["1"], "Colusa County"
        )
        with sqlite3.connect(first / "landuse.sqlite") as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT id,subbasin FROM elements ORDER BY id"
                ).fetchall(),
                [(1, 1), (2, 2)],
            )
            self.assertEqual(
                struct.unpack(
                    "<ddd",
                    connection.execute(
                        "SELECT vals FROM subbasin_areas WHERE source='nonponded' AND effective='2019-10-01' AND subbasin=1"
                    ).fetchone()[0],
                ),
                (11.0, 22.0, 33.0),
            )
            self.assertEqual(
                struct.unpack(
                    "<ddd",
                    connection.execute(
                        "SELECT vals FROM subbasin_areas WHERE source='nonponded' AND effective='2019-10-01' AND subbasin=2"
                    ).fetchone()[0],
                ),
                (44.0, 55.0, 66.0),
            )
            first_rows = connection.execute(
                "SELECT source,effective,subbasin,vals FROM subbasin_areas ORDER BY source,effective,subbasin"
            ).fetchall()
        renamed = copy.deepcopy(config)
        renamed["zones"]["names"] = {"1": "First County", "2": "Second County"}
        second = self.directory / "zone-second"
        self.build(second, renamed)
        with sqlite3.connect(second / "landuse.sqlite") as connection:
            second_rows = connection.execute(
                "SELECT source,effective,subbasin,vals FROM subbasin_areas ORDER BY source,effective,subbasin"
            ).fetchall()
        self.assertEqual(first_rows, second_rows)

    def test_zone_crop_acreage_equals_sum_of_assigned_elements(self):
        self.zone_file.write_text(
            "1 / ZEXTENT\n1 File County A\n2 File County B\n1 1\n2 1\n",
            encoding="utf-8",
        )
        config = {
            "version": 1,
            "categories": {},
            "groups": [],
            "initial_map_category": "nonponded:OR",
            "zones": {"singular": "County", "plural": "Counties"},
        }
        output = self.directory / "zone-summed-crops"
        self.build(output, config)
        element_crops = (
            self.fixture.adapter()
            .read_source("nonponded")
            .values_at(datetime(2019, 7, 1))
        )
        expected = tuple(element_crops.loc[[1, 2]].sum(axis=0))
        with sqlite3.connect(output / "landuse.sqlite") as connection:
            actual = struct.unpack(
                "<ddd",
                connection.execute(
                    "SELECT vals FROM subbasin_areas WHERE source='nonponded' AND effective='2019-10-01' AND subbasin=1"
                ).fetchone()[0],
            )
        self.assertEqual(actual, expected)
        self.assertEqual(actual, (55.0, 77.0, 99.0))

    def test_build_without_zone_file_keeps_reusable_zone_names_and_no_zone_data(self):
        config = {
            "version": 1,
            "categories": {},
            "groups": [],
            "initial_map_category": "nonponded:OR",
            "zones": {
                "singular": "County",
                "plural": "Counties",
                "names": {"77": "Future County"},
            },
        }
        output = self.directory / "without-zone"
        manifest = self.build(output, config, with_zone=False)
        self.assertEqual(manifest["subbasins"], {})
        self.assertEqual(
            manifest["model_configuration"]["zones"]["names"],
            {"77": "Future County"},
        )
        with sqlite3.connect(output / "landuse.sqlite") as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='subbasin_areas'"
                ).fetchone(),
                None,
            )

    def test_unassigned_elements_remain_in_element_and_subregion_data(self):
        self.zone_file.write_text(
            "1 / ZEXTENT\n1 File County A\n1 1\n", encoding="utf-8"
        )
        config = {
            "version": 1,
            "categories": {},
            "groups": [],
            "initial_map_category": "nonponded:OR",
            "zones": {"singular": "County", "plural": "Counties"},
        }
        output = self.directory / "with-unassigned"
        manifest = self.build(output, config)
        self.assertEqual(manifest["subbasins"]["-99"], "Unassigned (ZoneBudget -99)")
        with sqlite3.connect(output / "landuse.sqlite") as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT id,region,subbasin FROM elements ORDER BY id"
                ).fetchall(),
                [(1, 1, 1), (2, 1, -99)],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*),SUM(acres) FROM elements WHERE region=1"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT subbasin FROM subbasin_areas WHERE source='nonponded' AND effective='2019-10-01' ORDER BY subbasin"
                ).fetchall(),
                [(-99,), (1,)],
            )


if __name__ == "__main__":
    unittest.main()
