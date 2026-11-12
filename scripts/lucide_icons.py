"""Copy the Lucide icons the templates use into templates/components/icons/.

    python scripts/lucide_icons.py

Each icon is stored as the inner shapes of its SVG; the icon() macro in
templates/components/ui.html wraps them in one <svg> element. To add an icon,
put its name (as listed on https://lucide.dev/icons) in ICONS and run this again.
The package is downloaded from npm at a pinned version and checked against
its published integrity hash before anything is written.
"""
import base64
import hashlib
import io
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

VERSION = '1.62.0'
TARBALL = f'https://registry.npmjs.org/lucide-static/-/lucide-static-{VERSION}.tgz'
INTEGRITY = 'sha512-WBZA/m2PvkFpMGHDtsBck5P2A9G4ehLlYu4GwwICcmEm1BvYVgj00lCDxKRWLlbMlOibLaiwdQ88G5pHkSWgwA=='

ICONS = sorted({
    'activity', 'arrow-left', 'arrow-right', 'badge-check', 'bell', 'book-open', 'building-2', 'calendar-check',
    'calendar-clock', 'calendar-range', 'chart-column', 'check', 'chevron-down', 'chevron-left', 'chevron-right',
    'chevrons-up-down', 'circle-alert', 'circle-check', 'circle-user-round', 'circle-x', 'clock', 'cloud-off',
    'columns-3', 'copy', 'corner-down-left', 'download', 'ellipsis', 'eye', 'eye-off', 'file-spreadsheet',
    'file-text', 'funnel', 'graduation-cap', 'hash', 'history', 'house', 'inbox', 'info', 'key-round',
    'layout-dashboard', 'list-checks', 'loader-circle', 'lock', 'log-in', 'log-out', 'mail', 'menu', 'monitor',
    'moon', 'panel-left', 'pencil', 'percent', 'plus', 'presentation', 'printer', 'qr-code', 'rotate-cw',
    'scan-line', 'search', 'search-x', 'settings', 'shield-check', 'shield-user', 'smartphone', 'square-check',
    'sun', 'trash-2', 'triangle-alert', 'upload', 'user', 'user-plus', 'user-round', 'users', 'x',
})

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / 'templates' / 'components' / 'icons'


def download():
    with urllib.request.urlopen(TARBALL, timeout=120) as response:
        payload = response.read()
    algorithm, expected = INTEGRITY.split('-', 1)
    actual = base64.b64encode(hashlib.new(algorithm, payload).digest()).decode()
    if actual != expected:
        sys.exit(f"Integrity check failed for lucide-static {VERSION}. Nothing was written.")
    return payload


def inner_markup(svg):
    """The shapes inside <svg>...</svg>, one per line, without the license comment."""
    body = re.search(r'<svg[^>]*>(.*)</svg>', svg, re.S).group(1)
    shapes = [' '.join(line.split()) for line in body.strip().splitlines() if line.strip()]
    return '\n'.join(shapes) + '\n'


def main():
    payload = download()
    TARGET.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
        missing = []
        for name in ICONS:
            try:
                member = archive.extractfile(f'package/icons/{name}.svg')
            except KeyError:
                member = None
            if member is None:
                missing.append(name)
                continue
            (TARGET / f'{name}.svg').write_text(inner_markup(member.read().decode('utf-8')), encoding='utf-8', newline='\n')
        license_text = archive.extractfile('package/LICENSE').read().decode('utf-8')
    (TARGET / 'LICENSE').write_text(license_text, encoding='utf-8', newline='\n')
    if missing:
        sys.exit(f"Not in lucide-static {VERSION}: {', '.join(missing)}")
    print(f"Wrote {len(ICONS)} icons from lucide-static {VERSION} to {TARGET.relative_to(ROOT)}")


if __name__ == '__main__':
    main()
