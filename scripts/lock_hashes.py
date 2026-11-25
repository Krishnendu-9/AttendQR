"""Add the SHA-256 hash of every published file to each pin in requirements.lock.

With hashes, `pip install -r requirements.lock` checks every download (pip switches to
hash-checking mode on its own), so a package replaced on the index or in a mirror, or
tampered with in transit, is refused instead of installed. The Dockerfile passes
--require-hashes to make that explicit.

Hashes come from PyPI's JSON API for the exact pinned version: every wheel and sdist it
publishes, so the same lock works on Windows, Linux and macOS and on every supported
Python. Lines keep their environment markers. Existing hashes are replaced.

    python scripts/lock_hashes.py            # rewrite requirements.lock
    python scripts/lock_hashes.py --check    # exit 1 if a pin has no hashes (no network)

Run it after changing a pin (see the header of requirements.lock).
"""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

LOCK = Path(__file__).resolve().parents[1] / 'requirements.lock'
PIN = re.compile(r'^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)\s*(?:;\s*(?P<marker>[^\\]+?))?\s*\\?$')
PYPI = 'https://pypi.org/pypi/{name}/{version}/json'


def parse(text):
    """The lock as a list of comment lines (str) and pins (name, version, marker), hashes dropped."""
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('--hash=') or not stripped:
            continue
        if stripped.startswith('#'):
            items.append(line)
            continue
        match = PIN.match(stripped)
        if not match:
            raise SystemExit(f"Cannot read this line of {LOCK.name}: {line!r}")
        items.append((match['name'], match['version'], (match['marker'] or '').strip() or None))
    return items


def hashes(name, version):
    with urllib.request.urlopen(PYPI.format(name=name, version=version), timeout=30) as response:  # noqa: S310 - fixed https URL
        release = json.load(response)
    found = sorted({file['digests']['sha256'] for file in release['urls']})
    if not found:
        raise SystemExit(f"PyPI lists no files for {name}=={version}.")
    return found


def render(items, lookup):
    lines = []
    for item in items:
        if isinstance(item, str):
            lines.append(item)
            continue
        name, version, marker = item
        lines.append(f"{name}=={version}" + (f"; {marker}" if marker else '') + ' \\')
        digests = lookup(name, version)
        lines += [f"    --hash=sha256:{digest}" + (' \\' if index < len(digests) - 1 else '')
                  for index, digest in enumerate(digests)]
    return '\n'.join(lines) + '\n'


def unhashed(text):
    """Pins in the lock that are not followed by at least one --hash line."""
    missing, current = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('--hash='):
            current = None
        elif stripped and not stripped.startswith('#'):
            if current:
                missing.append(current)
            current = stripped.split('==')[0]
    return missing + ([current] if current else [])


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--check', action='store_true', help='only check that every pin has hashes')
    args = parser.parse_args()
    text = LOCK.read_text(encoding='utf-8')
    if args.check:
        missing = unhashed(text)
        if missing:
            print(f"{LOCK.name}: no hashes for {', '.join(missing)}. Run python scripts/lock_hashes.py.")
            return 1
        print(f"{LOCK.name}: every pin has hashes.")
        return 0
    items = parse(text)
    LOCK.write_text(render(items, hashes), encoding='utf-8', newline='\n')
    print(f"Wrote hashes for {sum(1 for item in items if not isinstance(item, str))} pins to {LOCK.name}.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
