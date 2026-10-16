#!/usr/bin/env python3
"""Build an agent installation ZIP from a committed Git snapshot."""
import argparse
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent


def assemble(entries, commit):
    version = entries['VERSION'].decode().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid release version')
    if entries['skills/apple-health-strava-sync/VERSION'].decode().strip() != version:
        raise ValueError('Package and skill versions differ')
    for name in entries:
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name:
            raise ValueError('Unsafe archive path: ' + name)
        if any(p in ('.git', '.conda', '__pycache__') for p in path.parts):
            raise ValueError('Runtime directory in release: ' + name)
        if path.name in ('local.json', 'sync_config.json', '.DS_Store') or path.name.startswith('.env'):
            raise ValueError('Local configuration in release: ' + name)
        if path.suffix.lower() in ('.gpx', '.tcx', '.xml', '.sqlite3', '.sqlite', '.db', '.zip', '.pyc'):
            raise ValueError('Private or generated data in release: ' + name)
        if path.parts[0] in ('state', 'exports', 'apple_health_export', 'input'):
            if path.name != '.gitkeep' or entries[name]:
                raise ValueError('Only empty directory placeholders may ship: ' + name)
    output = dict(entries)
    output['README_USER.md'] = entries['README.md']
    output['README.md'] = entries['packaging/README_AGENT.md']
    output['release.json'] = (json.dumps({
        'format': 1, 'version': version, 'source_commit': commit,
        'skill': 'apple-health-strava-sync', 'entrypoint': 'README.md',
        'files': [{'path': n, 'size': len(b)} for n, b in sorted(output.items())],
    }, ensure_ascii=False, indent=2) + '\n').encode()
    prefix = 'AppleFit2Strava-' + version + '/'
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(output.items()):
            info = zipfile.ZipInfo(prefix + name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if name.endswith('.command') else 0o100644) << 16
            archive.writestr(info, data)
    return buffer.getvalue(), version


def build(output, repo=ROOT):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    raw = subprocess.check_output(['git', 'archive', '--format=zip', commit], cwd=repo)
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        if any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in archive.infolist()):
            raise ValueError('Symlinks are not allowed in the release')
        entries = {i.filename: archive.read(i) for i in archive.infolist() if not i.is_dir()}
    content, version = assemble(entries, commit)
    destination = Path(output) if output else repo / 'dist' / f'AppleFit2Strava-{version}-agent-install.zip'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as stream:
        stream.write(content)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError('Archive CRC verification failed')
    return {'archive': str(destination.resolve()), 'version': version,
            'source_commit': commit, 'size': len(content)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output), indent=2))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'Build failed: {exc}\n')


if __name__ == '__main__':
    main()
