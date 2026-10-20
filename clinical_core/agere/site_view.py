"""Serve the packaged design on the same origin as the existing workspace API."""
import mimetypes
from pathlib import Path
from urllib.parse import unquote

DIST = Path(__file__).with_name('site_dist')
ALLOWED = {'.html', '.js', '.css', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.woff', '.woff2', '.json', '.txt'}


def asset(request_path, *, root=DIST):
    """Only packaged assets, including after URL decoding and symlink resolution."""
    if request_path in {'/', '/site', '/site/'}:
        relative = 'index.html'
    elif request_path.startswith('/site/'):
        relative = unquote(request_path[len('/site/'):])
    else:
        raise FileNotFoundError('Unknown site route')
    if '\x00' in relative or '\\' in relative:
        raise FileNotFoundError('Unknown site asset')
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in ALLOWED or not path.is_file():
        raise FileNotFoundError('Unknown site asset')
    return path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
