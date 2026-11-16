import tempfile
import unittest
from pathlib import Path
from clinical_core.agere.site_view import asset, DIST


class SiteAssets(unittest.TestCase):
    def test_packaged_home_and_dependencies(self):
        import re
        body, mime = asset('/site/')
        self.assertEqual(mime, 'text/html')
        self.assertIn(b'id="root"', body)
        for path in re.findall(r'(?:src|href)="(/site/[^"]+)"', body.decode()):
            raw, _ = asset(path)
            self.assertTrue(raw, path)
        self.assertEqual(asset('/'), asset('/site'))

    def test_rejects_path_escape_source_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'dist'
            root.mkdir()
            (Path(tmp) / 'private.json').write_text('{"private":true}')
            (root / 'escape.json').symlink_to(Path(tmp) / 'private.json')
            (root / 'server.py').write_text('secret')
            for path in ('/site/../private.json', '/site/%2e%2e/private.json',
                         '/site/%2fprivate.json', '/site/escape.json', '/site/server.py',
                         '/site/%00.json', '/site/..%5cprivate.json', '/api/stack/meta', '/site/missing.js'):
                with self.subTest(path=path), self.assertRaises(FileNotFoundError):
                    asset(path, root=root)

    def test_javascript_and_css_types(self):
        for suffix, expected in (('*.js', ('text/javascript', 'application/javascript')), ('*.css', ('text/css',))):
            files = list((DIST / 'assets').glob(suffix))
            self.assertTrue(files)
            for path in files:
                _, mime = asset('/site/' + str(path.relative_to(DIST)))
                self.assertIn(mime, expected)


if __name__ == '__main__': unittest.main()
