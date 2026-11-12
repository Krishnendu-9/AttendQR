"""Build the stylesheet with the Tailwind CSS standalone CLI (no Node.js needed).

    python scripts/tailwind.py           # build static/css/app.css once (minified)
    python scripts/tailwind.py --watch   # rebuild on every template or source change

The first run downloads the pinned Tailwind binary for this computer into tools/
(ignored by git) and checks it against the SHA-256 published with that release.
The built static/css/app.css is committed, so Docker and CI never need this script.
Run it after changing assets/tailwind.css or the classes used in templates/ or static/js/.
"""
import argparse
import hashlib
import os
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

VERSION = '4.3.3'

# From https://github.com/tailwindlabs/tailwindcss/releases/download/v4.3.3/sha256sums.txt
SHA256 = {
    'tailwindcss-windows-x64.exe': 'e0e260ce048014e9268f6237ff18f8ccf02cef521cbd0ae04e82c2cdf7aa3955',
    'tailwindcss-linux-x64': 'dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a',
    'tailwindcss-linux-arm64': '55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195',
    'tailwindcss-macos-x64': '7922e0953f2110c05976e3bf58f14e643d90427575e766b7d433f5f80cbee7e1',
    'tailwindcss-macos-arm64': 'cdf646702987a743464dff4d9c60fd4480d1c1e73dd819a9a67f1078815dce9d',
}

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'assets' / 'tailwind.css'
OUTPUT = ROOT / 'static' / 'css' / 'app.css'
TOOLS = ROOT / 'tools'


def asset_name():
    system = platform.system()
    machine = platform.machine().lower()
    arch = 'arm64' if machine in ('arm64', 'aarch64') else 'x64'
    if system == 'Windows':
        return 'tailwindcss-windows-x64.exe'
    if system == 'Darwin':
        return f'tailwindcss-macos-{arch}'
    if system == 'Linux':
        return f'tailwindcss-linux-{arch}'
    sys.exit(f"No Tailwind binary for {system} {machine}.")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_binary():
    name = asset_name()
    binary = TOOLS / f'{VERSION}-{name}'
    if binary.exists() and sha256(binary) == SHA256[name]:
        return binary

    url = f'https://github.com/tailwindlabs/tailwindcss/releases/download/v{VERSION}/{name}'
    print(f"Downloading Tailwind CSS {VERSION} ({name})...")
    TOOLS.mkdir(exist_ok=True)
    partial = binary.with_suffix(binary.suffix + '.part')
    with urllib.request.urlopen(url, timeout=120) as response, open(partial, 'wb') as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    actual = sha256(partial)
    if actual != SHA256[name]:
        partial.unlink()
        sys.exit(f"Checksum mismatch for {name}: expected {SHA256[name]}, got {actual}. Nothing was installed.")
    partial.replace(binary)
    if os.name != 'nt':
        binary.chmod(0o755)
    return binary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--watch', action='store_true', help='rebuild whenever a source file changes')
    args = parser.parse_args()

    command = [str(ensure_binary()), '--input', str(SOURCE), '--output', str(OUTPUT), '--cwd', str(ROOT)]
    command.append('--watch' if args.watch else '--minify')
    sys.exit(subprocess.call(command))  # noqa: S603 - our own verified binary and fixed arguments


if __name__ == '__main__':
    main()
