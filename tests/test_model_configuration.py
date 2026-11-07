import copy
import json
import sqlite3
import struct
from pathlib import Path
from types import SimpleNamespace
import unittest

from iwfm_input_adapter import Category
from model_configuration import resolve_configuration


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.categories = [Category('nonponded:OR', 'nonponded', 1, 'OR', 'Nonponded crop 1', 'CCODE', 'OR'),
                           Category('nonponded:GR', 'nonponded', 2, 'GR', 'Nonponded crop 2', 'CCODE', 'GR')]
        self.config = {'version': 1, 'categories': {}, 'groups': [
            {'id': 'group:ag', 'label': 'Agriculture', 'members': ['nonponded:GR'], 'color': '#123456'}],
            'initial_map_category': 'group:ag'}

    def test_order_and_identity_are_not_configured(self):
        self.config['categories'] = {'nonponded:GR': {'label': 'Shared'}, 'nonponded:OR': {'label': 'Shared'}}
        result = resolve_configuration(self.config, self.categories)
        self.assertEqual(result['codes']['nonponded'], ['nonponded:OR', 'nonponded:GR'])
        self.assertEqual(result['category_identity']['nonponded:GR']['acreage_column'], 2)
        self.assertEqual(result['labels']['nonponded:OR'], result['labels']['nonponded:GR'])
        self.assertEqual(result['groups'][0]['members'], ['nonponded:GR'])

    def test_unknown_category_is_actionable(self):
        self.config['categories']['nonponded:UNKNOWN'] = {'label': 'Unknown'}
        with self.assertRaisesRegex(ValueError, 'Unknown category IDs'):
            resolve_configuration(self.config, self.categories)

    def test_input_columns_and_duplicate_group_ids_cannot_be_configured(self):
        self.config['categories']['nonponded:OR'] = {'acreage_column': 2}
        with self.assertRaisesRegex(ValueError, 'input order is authoritative'):
            resolve_configuration(self.config, self.categories)
        self.config['categories'] = {}
        self.config['groups'].append(copy.deepcopy(self.config['groups'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate group ID'):
            resolve_configuration(self.config, self.categories)

    def test_invalid_groups_are_rejected(self):
        for members in [[], ['nonponded:OR', 'nonponded:OR'], ['nonponded:UNKNOWN']]:
            config = copy.deepcopy(self.config)
            config['groups'][0]['members'] = members
            with self.subTest(members=members), self.assertRaisesRegex(ValueError, 'members|category IDs'):
                resolve_configuration(config, self.categories)

    def test_invalid_initial_and_color_are_rejected(self):
        self.config['initial_map_category'] = 'not-a-category'
        with self.assertRaisesRegex(ValueError, 'initial_map_category'):
            resolve_configuration(self.config, self.categories)
        self.config['initial_map_category'] = 'group:ag'
        self.config['groups'][0]['color'] = 'red'
        with self.assertRaisesRegex(ValueError, 'hex color'):
            resolve_configuration(self.config, self.categories)

    def test_invalid_grouping_threshold_is_rejected(self):
        self.config['groups'][0]['minimum_members'] = 2
        with self.assertRaisesRegex(ValueError, 'minimum_members'):
            resolve_configuration(self.config, self.categories)

    def test_fallback_labels_and_colors_are_identity_based(self):
        first = resolve_configuration(self.config, self.categories)
        second = resolve_configuration(self.config, list(reversed(self.categories)))
        self.assertEqual(first['labels']['nonponded:OR'], 'Nonponded crop 1')
        self.assertEqual(first['category_colors'], second['category_colors'])

    def test_optional_branding_and_default_compatibility(self):
        default = resolve_configuration(self.config, self.categories)
        self.assertNotIn('model_title', default)
        self.assertNotIn('export_filename_prefix', default)
        self.config.update(model_title='SVSim <land use> & inputs', export_filename_prefix='SVSim_landuse')
        result = resolve_configuration(self.config, self.categories)
        self.assertEqual(result['model_title'], 'SVSim <land use> & inputs')
        self.assertEqual(result['export_filename_prefix'], 'SVSim_landuse')
        self.assertEqual(result['codes'], default['codes'])

    def test_invalid_filename_prefixes_are_rejected(self):
        for prefix in ['', '../SVSim', 'folder/SVSim', 'folder\\SVSim', 'SVSim.csv', 'CON', 'LPT1', 'bad name', 'x' * 81, None]:
            with self.subTest(prefix=prefix):
                self.config['export_filename_prefix'] = prefix
                with self.assertRaisesRegex(ValueError, 'export_filename_prefix'):
                    resolve_configuration(self.config, self.categories)

    def test_both_templates_use_safe_title_and_mapping_hooks(self):
        import c2vsim_landuse_explorer_expanded as expanded
        import c2vsim_landuse_explorer_naip as naip

        for module in [expanded, naip]:
            self.assertIn("document.querySelector('header h1').textContent=title", module.HTML)
            self.assertIn('document.title=title', module.HTML)
            self.assertIn('ctx.fillText(modelTitle()', module.HTML)
            self.assertIn("return exportPrefix()+'_'", module.HTML)
            self.assertIn("hasSubbasins()?basins.map", module.HTML)
            self.assertIn("...(hasSubbasins()?['Subbasins: '", module.HTML)

    def test_small_deck_builds_with_config_and_labels_do_not_change_values(self):
        import test_iwfm_input_adapter as fixtures
        import c2vsim_landuse_explorer_expanded as viewer
        import export_landuse_standalone as exporter

        fixture = fixtures.AdapterTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        directory = fixture.directory
        (directory / 'nodes.dat').write_text('4\n3.2808\n1 500000 4400000\n2 500100 4400000\n3 500100 4400100\n4 500000 4400100\n', encoding='utf-8')
        (directory / 'elements.dat').write_text('2\n1\nRegion / RNAME1\n1 1 2 3 0 1\n2 1 3 4 0 1\n', encoding='utf-8')
        pp = directory / 'pp.in'
        pp.write_text('Fixture\n / 1: BINARY\nelements.dat / 2: ELEMENT\nnodes.dat / 3: NODE\n'
                      ' / 4: STRATA\n / 5: STREAM\n / 6: LAKE\n0\n0\n1\nFEET\n0.000022957\nACRES\n', encoding='utf-8')
        config_path = directory / 'config.json'
        config = {'version': 1, 'categories': {}, 'groups': [
            {'id': 'group:ag', 'label': 'Agriculture', 'members': ['nonponded:OR', 'nonponded:GR'], 'color': '#123456'}],
            'initial_map_category': 'nonponded:OR'}
        baseline = None
        for name, labels in [('neutral', {}), ('renamed', {'nonponded:OR': {'label': 'Shared'}, 'nonponded:GR': {'label': 'Shared'}})]:
            config['categories'] = labels
            config_path.write_text(json.dumps(config), encoding='utf-8')
            output = directory / name
            viewer.build(SimpleNamespace(input=str(directory), output=str(output), start_year=2019, utm_zone=10,
                reader='iwfm-io', rootzone_main=str(fixture.root), preprocessor_main=str(pp),
                simulation_main=None, coverage_start='2018-10-01', model_config=str(config_path)))
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['codes']['nonponded'], ['nonponded:OR', 'nonponded:GR', 'nonponded:AL'])
            self.assertEqual(manifest['codes']['urban'], ['urban:urban'])
            with sqlite3.connect(output / 'landuse.sqlite') as connection:
                self.addCleanup(connection.close)
                rows = connection.execute('SELECT * FROM areas ORDER BY source,effective,element').fetchall()
                blob = connection.execute("SELECT vals FROM areas WHERE source='nonponded' AND effective='2019-10-01' AND element=1").fetchone()[0]
                values = struct.unpack('<fff', blob)
                self.assertEqual(values, (11, 22, 33))
                self.assertEqual(sum(values[:2]), 33)
                if baseline is not None:
                    self.assertEqual(rows, baseline)
                baseline = rows
            exporter.export(output, Path(viewer.__file__), directory / (name + '.html'))
        self.assertEqual(manifest['labels']['nonponded:OR'], 'Shared')
        self.assertEqual(manifest['labels']['nonponded:GR'], 'Shared')


if __name__ == '__main__':
    unittest.main()