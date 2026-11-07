import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from viewer_renderer import render_viewer


class ViewerRendererTests(unittest.TestCase):
    def test_entry_points_keep_html_interface_and_default_modes(self):
        import c2vsim_landuse_explorer_expanded as expanded
        import c2vsim_landuse_explorer_naip as naip

        self.assertEqual(expanded.HTML, render_viewer(imagery='current'))
        self.assertEqual(naip.HTML, render_viewer(imagery='historical-naip'))

    def test_historical_controls_are_opt_in(self):
        current = render_viewer()
        historical = render_viewer(imagery='historical-naip')
        self.assertNotIn('id="imageryyear"', current)
        self.assertNotIn('function imageryTileUrl(', current)
        self.assertIn('USGS aerial imagery', current)
        self.assertIn('id="imageryyear"', historical)
        self.assertIn('<option value="2022" selected>', historical)
        self.assertIn('Latest NAIP', historical)
        self.assertIn('id="imageryretry"', historical)

    def test_exporter_api_markers_are_preserved(self):
        marker = 'async function api(path){let r=await fetch(path);if(!r.ok)throw Error(await r.text());return r.json()}'
        for imagery in ['current', 'historical-naip']:
            html = render_viewer(imagery=imagery)
            self.assertEqual(html.count(marker), 1)
            self.assertEqual(html.count('<script>'), 1)
            self.assertNotIn('@@IMAGERY_', html)
            self.assertIn('function chartRows(', html)
            self.assertIn('function exportFilename(', html)

    def test_invalid_imagery_is_actionable(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported imagery mode'):
            render_viewer(imagery='unknown')

    def test_entry_points_load_assets_from_another_working_directory(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            for name in ['c2vsim_landuse_explorer_expanded.py', 'c2vsim_landuse_explorer_naip.py']:
                result = subprocess.run([sys.executable, '-S', '-B', str(root / name), 'build', '--help'],
                                        cwd=directory, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--reader', result.stdout)


if __name__ == '__main__':
    unittest.main()