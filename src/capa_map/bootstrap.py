"""Check the versioned runtime bundle without loading models or contacting APIs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import sys

LFS_POINTER = b'version https://git-lfs.github.com/spec/v1'


def verify_python(version=None, bits=None) -> dict:
    version = tuple(sys.version_info[:3]) if version is None else tuple(version)
    bits = struct.calcsize('P') * 8 if bits is None else bits
    detected = '.'.join(map(str, version))
    if not (version[:2] == (3, 11) and version >= (3, 11, 5) and bits == 64):
        raise ValueError(
            f'MAP needs 64-bit Python 3.11.5 or newer within the 3.11 series; detected Python {detected} ({bits}-bit). '
            'The pinned soupsieve 4.1 dependency requires Python >=3.11.5. '
            'Install a supported Python 3.11 build, then recreate .venv. Upgrading pip alone does not fix the Python version.')
    return {'python': detected, 'architecture_bits': bits}


def initialize_bundled_source(root: Path) -> dict:
    from dotenv import dotenv_values
    from capa_map import cli

    root = root.resolve()
    if root != cli.ROOT:
        raise ValueError('Source initialization must use the checkout containing the installed MAP package.')
    current = dotenv_values(root / '.env').get('CAPA_SOURCE_SNAPSHOT') or ''
    if current and 'REPLACE_WITH_SNAPSHOT_ID' not in current:
        return {'source': 'existing configuration preserved'}
    cli.import_source([str(root / 'data/sources/trackwise_records.csv')])
    return {'source': 'bundled source imported'}


def verify_bundle(root: Path) -> dict:
    root = root.resolve()
    manifest = json.loads((root / 'runtime-assets.json').read_text(encoding='utf-8'))
    errors = []
    total = 0
    for entry in manifest['files']:
        relative = PurePosixPath(entry['path'])
        target = (root / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(root):
            raise ValueError('Runtime manifest paths must stay inside the repository.')
        if not target.is_file():
            errors.append(f"Missing: {entry['path']}")
            continue
        with target.open('rb') as handle:
            if handle.read(len(LFS_POINTER)).startswith(LFS_POINTER):
                errors.append(f"Git LFS pointer, not downloaded: {entry['path']}")
                continue
            handle.seek(0)
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if target.stat().st_size != entry['size_bytes'] or digest != entry['sha256']:
            errors.append(f"Content does not match the runtime manifest: {entry['path']}")
        total += target.stat().st_size
    if errors:
        raise ValueError('\n'.join(errors) + '\nRun git lfs pull and retry setup. Do not load incomplete model files.')
    return {'verified_files': len(manifest['files']), 'total_bytes': total,
            'embedding_model': manifest['embedding_model'],
            'embedding_revision': manifest['embedding_revision']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='Repository checkout containing runtime-assets.json')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--check-python', action='store_true', help='Check interpreter compatibility before downloading files or installing packages')
    action.add_argument('--initialize-source', action='store_true', help='Import the bundled source only when .env has no source configuration')
    args = parser.parse_args()
    try:
        if args.check_python:
            result = verify_python()
        else:
            result = initialize_bundled_source(args.root) if args.initialize_source else verify_bundle(args.root)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'MAP runtime verification failed: {error}\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
