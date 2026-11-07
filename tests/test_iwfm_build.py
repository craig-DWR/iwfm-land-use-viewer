import json
import sqlite3
import struct
import subprocess
import sys
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import test_iwfm_input_adapter as fixtures
from iwfm_build import AdapterBuild, store_source


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AdapterTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.connection = sqlite3.connect(':memory:')
        self.addCleanup(self.connection.close)
        self.connection.execute('CREATE TABLE areas(source TEXT,effective TEXT,element INTEGER,vals BLOB)')

    def test_normalized_values_are_reordered_by_alias_and_stored_float32(self):
        source = self.fixture.adapter().read_source('nonponded')
        dates = store_source(self.connection, source, ['GR', 'AL', 'OR'], '2019-01-01')
        self.assertEqual(dates, ['2019-10-01', '2020-10-01'])
        blob = self.connection.execute("SELECT vals FROM areas WHERE element=1 AND effective='2019-10-01'").fetchone()[0]
        self.assertEqual(struct.unpack('<fff', blob), (22, 33, 11))

    def test_retention_keeps_separate_source_dates(self):
        urban = self.fixture.adapter().read_source('urban')
        store_source(self.connection, urban, ['U'], '2020-01-01')
        self.assertEqual(self.connection.execute('SELECT DISTINCT effective FROM areas').fetchall(), [('2020-04-01',)])

    def test_alias_mismatch_does_not_fall_back(self):
        source = self.fixture.adapter().read_source('nonponded')
        with self.assertRaisesRegex(ValueError, 'do not match C2VSimFG aliases'):
            store_source(self.connection, source, ['OR', 'GR'], '2019-01-01')
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM areas').fetchone()[0], 0)

    def test_missing_coverage_metadata_is_actionable(self):
        with self.assertRaisesRegex(ValueError, 'Supply --simulation-main or --coverage-start'):
            AdapterBuild(SimpleNamespace(rootzone_main=str(self.fixture.root),
                preprocessor_main='fixture.in', coverage_start=None, simulation_main=None))

    def test_both_default_commands_work_without_adapter_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        for script in ['c2vsim_landuse_explorer_expanded.py', 'c2vsim_landuse_explorer_naip.py']:
            result = subprocess.run([sys.executable, '-S', '-B', str(root / script), 'build', '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--reader', result.stdout)

    def test_opt_in_dependency_failure_is_clear(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-S', '-B', str(root / 'c2vsim_landuse_explorer_expanded.py'),
            'build', '--reader', 'iwfm-io', '--input', str(self.fixture.directory),
            '--output', str(self.fixture.directory / 'unavailable')], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requirements-adapter.txt', result.stderr)
        self.assertFalse((self.fixture.directory / 'unavailable').exists())

    def test_both_builders_keep_geometry_manifest_and_storage_identical(self):
        import c2vsim_landuse_explorer_expanded as expanded
        import c2vsim_landuse_explorer_naip as naip

        directory = self.fixture.directory / 'flat'
        directory.mkdir()
        nodes = directory / 'Fixture_Nodes.dat'
        elements = directory / 'Fixture_Elements.dat'
        nodes.write_text('4\n3.2808\n1 500000 4400000\n2 500100 4400000\n3 500100 4400100\n4 500000 4400100\n', encoding='utf-8')
        elements.write_text('2\n1\nRegion / RNAME1\n1 1 2 3 0 1\n2 1 3 4 0 1\n', encoding='utf-8')
        paths = {'nodes': nodes, 'elements': elements}
        sources = {}
        for name, (stem, aliases) in expanded.SOURCES.items():
            path = directory / f'Fixture_{stem}.dat'
            values = [index + 1.123456789 for index in range(len(aliases))]
            body = ' '.join(map(str, values))
            path.write_text(f'43560\n1\n0\n / DSSFL\nC data\n09/30/2019_24:00 1 {body}\n2 {body}\n', encoding='utf-8')
            paths[name] = path
            sources[name] = SimpleNamespace(source=name, categories=[SimpleNamespace(legacy_alias=alias, id=alias) for alias in aliases],
                interval_ends=(datetime(2019, 10, 1),), component_path=path, area_path=path,
                acres=pd.DataFrame([values, values], columns=aliases,
                    index=pd.MultiIndex.from_tuples([(datetime(2019, 10, 1), 1), (datetime(2019, 10, 1), 2)])))
        prepared = SimpleNamespace(paths=paths, metadata={'name': 'iwfm-io'})
        prepared.parse_area = lambda connection, name, minimum, valid, aliases: store_source(connection, sources[name], aliases, minimum)
        for backend in [expanded, naip]:
            legacy_path = self.fixture.directory / (backend.__name__ + '-legacy')
            adapter_path = self.fixture.directory / (backend.__name__ + '-adapter')
            base = dict(input=str(directory), start_year=2019, utm_zone=10)
            result = subprocess.run([sys.executable, '-S', '-B', backend.__file__, 'build',
                '--input', str(directory), '--output', str(legacy_path), '--start-year', '2019',
                '--utm-zone', '10'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with patch('iwfm_build.AdapterBuild', return_value=prepared):
                backend.build(SimpleNamespace(output=str(adapter_path), reader='iwfm-io', **base))
            first = json.loads((legacy_path / 'manifest.json').read_text())
            second = json.loads((adapter_path / 'manifest.json').read_text())
            second.pop('input_reader')
            self.assertEqual(first, second)
            with closing(sqlite3.connect(legacy_path / 'landuse.sqlite')) as left, closing(sqlite3.connect(adapter_path / 'landuse.sqlite')) as right:
                for table in ['elements', 'areas', 'region_areas']:
                    self.assertEqual(left.execute(f'SELECT * FROM {table}').fetchall(), right.execute(f'SELECT * FROM {table}').fetchall())

    def test_staggered_coverage_does_not_extend_annual_maps(self):
        import c2vsim_landuse_explorer_expanded as viewer

        manifest = {'start_year': 2000, 'dates': {
            'ponded': ['2024-10-01', '2025-04-01'],
            'urban': ['2024-10-01'], 'native': ['2024-10-01'], 'nonponded': ['2024-10-01']}}
        viewer.reconcile_dates(manifest)
        self.assertEqual(manifest['last_year'], 2024)
        self.assertEqual(viewer.period_end_for_day(manifest['dates']['ponded'], '2024-10-01'), '2025-04-01')
        for name in ['urban', 'native', 'nonponded']:
            self.assertIsNone(viewer.period_end_for_day(manifest['dates'][name], '2024-10-01'))

    def test_verified_bdt_and_explicit_coverage_start(self):
        directory = self.fixture.directory
        self.fixture.root.write_text(self.fixture.root.read_text().replace(
            'urban.dat / URBFL', 'urban.dat / URBFL\nponded.dat / PFL\nnative.dat / NVRVFL'), encoding='utf-8')
        (directory / 'elements.dat').write_text('2\n1\nRegion / RNAME1\n1 1 2 3 0 1\n2 1 3 4 0 1\n', encoding='utf-8')
        pp = directory / 'pp.in'
        pp.write_text('Fixture\n / 1: BINARY\nelements.dat / 2: ELEMENT\nnodes.dat / 3: NODE\n'
                      ' / 4: STRATA\n / 5: STREAM\n / 6: LAKE\n0\n0\n1\nFEET\n0.000022957\nACRES\n', encoding='utf-8')
        simulation = directory / 'simulation.in'
        simulation.write_text('Fixture\nroot.dat / 5: ROOTZONE\n09/30/2018_24:00 / BDT\n'
                              '09/30/2020_24:00 / EDT\n1MON / UNITT\n0 / KOPTDV\n', encoding='utf-8')
        args = SimpleNamespace(rootzone_main=str(self.fixture.root), preprocessor_main=str(pp),
                               simulation_main=str(simulation), coverage_start=None, start_year=2019)
        prepared = AdapterBuild(args)
        self.assertEqual(prepared.adapter.coverage_start, datetime(2018, 10, 1))
        self.assertIn('BDT=09/30/2018_24:00', prepared.metadata['coverage_start_evidence'])
        args.simulation_main = None
        args.coverage_start = '2018-10-01'
        self.assertEqual(AdapterBuild(args).metadata['coverage_start_evidence'], 'explicit --coverage-start')


if __name__ == '__main__':
    unittest.main()