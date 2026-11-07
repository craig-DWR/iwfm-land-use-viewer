import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from iwfm_input_adapter import C2VSIMFG_ALIASES, IWFMInputAdapter, InputValidationError, UnitBasis


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / 'root.dat'
        self.root.write_text(
            '#4.11\n0.001\n100\n0.083333\n0\n'
            'nonponded.dat / AGNPFL\nurban.dat / URBFL\n'
            '1 0.1 0.2 0.4 0.5 1 1 1 1 1 0 0 0 1\n'
            '2 0.1 0.2 0.4 0.5 1 1 1 1 1 0 0 0 1\n', encoding='utf-8')
        (self.directory / 'nonponded.dat').write_text(
            '3 / NCROP\n1 / FLDMD\nOR / CCODE[1]\nGR / CCODE[2]\nAL / CCODE[3]\n'
            'nonponded_area.dat / LUFLNP\n2 / NBCROP\nAL / BCCODE[1]\nOR / BCCODE[2]\n'
            ' / CLWUBUDFL\n / CRZBUDFL\nroots.dat / RZFRACFL\n1 / FACT\n'
            '1 2 1\n2 2 1\n3 2 1\n'
            '0 60 60 60\n0 1 1 1\n0 0 0 0\n0 1 1 1\n'
            'min.dat / MINSMFL\n0 1 1 1\n / TRGSMFL\n'
            '0 0 0 0\n0 0 0 0\n / DPFL\n0 0.5 0.2 0.2 0.2\n', encoding='utf-8')
        (self.directory / 'urban.dat').write_text(
            'urban_area.dat / LUFLU\n1\n2\npop.dat\nwater.dat\nspec.dat\n'
            '0 0.3 60 1 1 0.5 1 0 0 1\n0 0.5 0.2\n', encoding='utf-8')
        self.area_path = self.directory / 'nonponded_area.dat'
        self.area_path.write_text(
            '43560 / FACTLNNP\n1 / NSPLNNP\n0 / NFQLNNP\n / DSSFL\nC data\n'
            '09/30/2019_24:00 2 44 55 66\n1 11 22 33\n'
            '09/30/2020_24:00 1 0 2 3\n2 4 5 6\n', encoding='utf-8')
        (self.directory / 'urban_area.dat').write_text(
            '43560\n1\n0\n / DSSFL\nC data\n'
            '03/31/2019_24:00 1 0\n2 10\n'
            '03/31/2020_24:00 2 20\n1 0\n', encoding='utf-8')

    def adapter(self, **kwargs):
        return IWFMInputAdapter(
            self.root, working_directory=self.directory, element_ids={1, 2},
            unit_basis=UnitBasis('square_feet', 'synthetic fixture declared square feet'),
            coverage_start=datetime(2018, 10, 1), legacy_aliases=C2VSIMFG_ALIASES, **kwargs)

    def test_reordered_crops_without_names(self):
        source = self.adapter().read_source('nonponded')
        self.assertEqual([category.code for category in source.categories], ['OR', 'GR', 'AL'])
        self.assertEqual([category.acreage_column for category in source.categories], [1, 2, 3])
        self.assertEqual([category.label for category in source.categories],
                         ['Nonponded crop 1', 'Nonponded crop 2', 'Nonponded crop 3'])
        self.assertEqual([category.legacy_alias for category in source.categories], ['OR', 'GR', 'AL'])
        self.assertEqual(source.budget_codes, ('AL', 'OR'))
        self.assertEqual(source.values_at(datetime(2019, 7, 1)).loc[1].tolist(), [11, 22, 33])

    def test_names_only_change_labels(self):
        original = self.adapter().read_source('nonponded')
        renamed = self.adapter(labels={'nonponded:OR': 'Shared name', 'nonponded:GR': 'Shared name'}).read_source('nonponded')
        self.assertTrue(original.acres.equals(renamed.acres))
        self.assertEqual([category.id for category in original.categories], [category.id for category in renamed.categories])
        self.assertEqual([category.label for category in renamed.categories][:2], ['Shared name', 'Shared name'])

    def test_exact_boundary_moves_to_next_block(self):
        source = self.adapter().read_source('nonponded')
        self.assertEqual(source.endpoints, ('09/30/2019_24:00', '09/30/2020_24:00'))
        self.assertEqual(source.endpoint_for(datetime(2019, 10, 1)), '09/30/2020_24:00')
        self.assertEqual(source.values_at(datetime(2019, 10, 1)).loc[1].tolist(), [0, 2, 3])

    def test_coverage_contract_at_both_ends(self):
        source = self.adapter().read_source('nonponded')
        start = datetime(2018, 10, 1)
        interior = datetime(2019, 10, 1)
        final = datetime(2020, 10, 1)
        before = start - timedelta(microseconds=1)
        self.assertIsNone(source.endpoint_for(before))
        self.assertIsNone(source.values_at(before))
        self.assertEqual(source.endpoint_for(start), '09/30/2019_24:00')
        self.assertEqual(source.values_at(start).loc[1].tolist(), [11, 22, 33])
        self.assertEqual(source.endpoint_for(interior - timedelta(microseconds=1)), '09/30/2019_24:00')
        self.assertEqual(source.endpoint_for(interior), '09/30/2020_24:00')
        self.assertEqual(source.values_at(interior).loc[1].tolist(), [0, 2, 3])
        self.assertIsNotNone(source.values_at(final - timedelta(microseconds=1)))
        self.assertIsNone(source.endpoint_for(final))
        self.assertIsNone(source.values_at(final))
        self.assertIsNone(source.values_at(final + timedelta(microseconds=1)))

    def test_invalid_lookup_instant_is_actionable(self):
        source = self.adapter().read_source('nonponded')
        for instant in [date(2019, 7, 1), datetime(2019, 7, 1, tzinfo=timezone.utc)]:
            with self.subTest(instant=instant):
                with self.assertRaisesRegex(InputValidationError, 'naive model datetime'):
                    source.values_at(instant)

    def test_zero_and_missing_coverage(self):
        source = self.adapter().read_source('urban')
        self.assertEqual(source.values_at(datetime(2019, 7, 1)).loc[1].tolist(), [0])
        self.assertIsNone(source.values_at(datetime(2020, 4, 1)))
        self.assertIsNone(source.values_at(datetime(2018, 9, 30)))

    def test_staggered_sources_are_not_merged(self):
        adapter = self.adapter()
        nonponded = adapter.read_source('nonponded')
        urban = adapter.read_source('urban')
        self.assertIsNotNone(nonponded.values_at(datetime(2020, 7, 1)))
        self.assertIsNone(urban.values_at(datetime(2020, 7, 1)))
        self.assertNotEqual(nonponded.endpoints, urban.endpoints)

    def test_square_feet_conversion(self):
        self.area_path.write_text(self.area_path.read_text().replace('43560 / FACTLNNP', '1 / FACTLNNP'), encoding='utf-8')
        source = self.adapter().read_source('nonponded')
        self.assertEqual(source.input_factor, 1)
        self.assertEqual(source.input_unit, 'square_feet')
        self.assertAlmostEqual(source.values_at(datetime(2019, 7, 1)).loc[1].iloc[0], 11 / 43560)

    def test_unverified_factor_is_rejected(self):
        for factor in [0, 2, -1]:
            with self.subTest(factor=factor):
                self.area_path.write_text(self.area_path.read_text().replace(self.area_path.read_text().splitlines()[0], f'{factor} / FACTLNNP'), encoding='utf-8')
                with self.assertRaisesRegex(InputValidationError, 'FACT|factor'):
                    self.adapter().read_source('nonponded')

    def test_unsupported_format_is_clear(self):
        self.root.write_text(self.root.read_text().replace('#4.11', '#4.13'), encoding='utf-8')
        with self.assertRaisesRegex(InputValidationError, 'unsupported root-zone format.*4.13'):
            self.adapter()

    def test_column_mismatch_is_rejected(self):
        self.area_path.write_text(self.area_path.read_text().replace('1 11 22 33', '1 11 22'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'finite|columns|rectangular'):
            self.adapter().read_source('nonponded')

    def test_unknown_units_are_rejected(self):
        with self.assertRaisesRegex(InputValidationError, 'unit basis'):
            UnitBasis('unknown', '')
        with self.assertRaisesRegex(InputValidationError, 'unit basis'):
            UnitBasis('square_feet', None)

    def test_fixed_slots_do_not_follow_budget_order(self):
        self.root.write_text(self.root.read_text().replace(
            'urban.dat / URBFL', 'urban.dat / URBFL\nponded.dat / PFL\nnative.dat / NVRVFL'), encoding='utf-8')
        (self.directory / 'ponded.dat').write_text(
            'ponded_area.dat / LUFLP\n2\nRICE_NFL\nRICE_FL\n / CLWUBUDFL\n / CRZBUDFL\n'
            '1\n2 / ROOTRI_FL\n2 / ROOTRI_NFL\n2 / ROOTRI_NDC\n2 / ROOTRF_SL\n2 / ROOTRF_PR\n'
            '0 60 60 60 60 60\n0 1 1 1 1 1\n0 0 0 0 0 0\n0 1 1 1 1 1\n'
            'depth.dat\nops.dat\n0 1 1 1 1 1\n0 1\n'
            '0 0 0 0 0 0\n0 0 0 0 0 0\n0 0.5 0.2 0.2 0.2 0.2 0.2\n', encoding='utf-8')
        (self.directory / 'native.dat').write_text(
            'native_area.dat\n1\n2\n2\n0 60 60 1 1 0\n0 0.2 0.2\n', encoding='utf-8')
        for source, values in [('ponded', '11 22 33 44 55'), ('native', '66 77')]:
            (self.directory / f'{source}_area.dat').write_text(
                f'43560\n1\n0\n / DSSFL\nC data\n09/30/2020_24:00 1 {values}\n2 {values}\n', encoding='utf-8')
        ponded = self.adapter().read_source('ponded')
        native = self.adapter().read_source('native')
        self.assertEqual([category.legacy_alias for category in ponded.categories],
                         ['RI_FL', 'RI_NFL', 'RI_NDC', 'RF_SL', 'RF_PR'])
        self.assertEqual(ponded.budget_codes, ('RICE_NFL', 'RICE_FL'))
        self.assertEqual(ponded.values_at(datetime(2020, 7, 1)).loc[1].tolist(), [11, 22, 33, 44, 55])
        self.assertEqual([category.legacy_alias for category in native.categories], ['NV', 'RV'])
        self.assertEqual(native.values_at(datetime(2020, 7, 1)).loc[1].tolist(), [66, 77])

    def test_missing_element_is_rejected(self):
        self.area_path.write_text(self.area_path.read_text().replace('2 4 5 6\n', ''), encoding='utf-8')
        with self.assertRaisesRegex(InputValidationError, 'each expected element'):
            self.adapter().read_source('nonponded')

    def test_unknown_element_is_rejected(self):
        self.area_path.write_text(self.area_path.read_text().replace('2 4 5 6', '3 4 5 6'), encoding='utf-8')
        with self.assertRaisesRegex(InputValidationError, 'each expected element'):
            self.adapter().read_source('nonponded')

    def test_descending_endpoints_are_rejected(self):
        self.area_path.write_text(self.area_path.read_text().replace('09/30/2020_24:00', '09/30/2018_24:00'), encoding='utf-8')
        with self.assertRaisesRegex(InputValidationError, 'strictly increase'):
            self.adapter().read_source('nonponded')

    def test_duplicate_codes_are_rejected(self):
        component_path = self.directory / 'nonponded.dat'
        component_path.write_text(component_path.read_text().replace('GR / CCODE[2]', 'OR / CCODE[2]'), encoding='utf-8')
        with self.assertRaisesRegex(InputValidationError, r'duplicate category codes.*OR.*CCODE\[1\].*CCODE\[2\].*unique'):
            self.adapter().read_source('nonponded')

    def test_absent_source_is_not_zero(self):
        self.root.write_text(self.root.read_text().replace('urban.dat / URBFL', ' / URBFL'), encoding='utf-8')
        adapter = self.adapter()
        self.assertEqual(adapter.sources, ('nonponded',))
        with self.assertRaisesRegex(InputValidationError, 'no input file'):
            adapter.read_source('urban')

    def test_preprocessor_unit_evidence(self):
        path = self.directory / 'pp.in'
        path.write_text('Fixture\n / 1: BINARY\n / 2: ELEMENT\n / 3: NODE\n'
                        ' / 4: STRATA\n / 5: STREAM\n / 6: LAKE\n'
                        '0\n0\n1\nFEET\n0.000022957\nACRES\n', encoding='utf-8')
        self.assertIn('UNITLTOU=FEET', UnitBasis.from_preprocessor(path).evidence)
        path.write_text(path.read_text().replace('FEET', 'METERS'), encoding='utf-8')
        with self.assertRaisesRegex(InputValidationError, 'cannot establish model square feet'):
            UnitBasis.from_preprocessor(path)


if __name__ == '__main__':
    unittest.main()