#!/usr/bin/env python3
"""Install the bundled skill at a host-selected destination, without networking."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NAME = 'apple-health-strava-sync'
ROOT = Path(__file__).resolve().parent
REQUIRED = {'SKILL.md', 'VERSION', 'references/harness.md', 'references/cloud-check.md',
            *('scripts/' + n + '.py' for n in (
                'sync_strava', 'setup_project', 'health_to_strava', 'cloud_check',
                'upload_review', 'upload_history', 'sport_types', 'activity_places'))}


def inspect_source(source):
    if sys.version_info < (3, 10):
        raise ValueError('Python 3.10+ is required')
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Missing skill source or symlink source')
    files = {}
    for path in source.rglob('*'):
        if path.is_symlink():
            raise ValueError('Skill source must not contain symlinks')
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            if path.name in ('local.json', '.DS_Store') or '__pycache__' in path.parts:
                raise ValueError('Skill source contains local runtime files')
            files[relative] = path.read_bytes()
            if path.suffix == '.py':
                ast.parse(files[relative], filename=relative)
    if not REQUIRED <= files.keys():
        raise ValueError('Incomplete skill: ' + ', '.join(sorted(REQUIRED - files.keys())))
    if not re.search(r'^name: apple-health-strava-sync\s*$', files['SKILL.md'].decode(), re.M):
        raise ValueError('Unexpected skill name')
    version = files['VERSION'].decode().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid skill version')
    return files, version


def verify(destination):
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    cli = destination / 'scripts/sync_strava.py'
    for command in ('--help', 'sports'):
        result = subprocess.run([sys.executable, str(cli), command], env=env,
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError('Installed CLI validation failed: ' + result.stderr.strip())
        if command == 'sports' and not isinstance(json.loads(result.stdout), list):
            raise ValueError('Invalid sports command result')
    missing = []
    for name in ('UTC', 'America/New_York', 'Asia/Shanghai'):
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError:
            missing.append(name)
    return {'python': sys.executable, 'timezone_ready': not missing, 'missing_timezones': missing}


def install(destination, source=None):
    source = Path(source) if source is not None else ROOT / 'skills' / NAME
    files, version = inspect_source(source)
    destination = Path(destination).expanduser()
    if destination.is_symlink():
        raise ValueError('Refusing a symlink destination')
    destination = destination.resolve()
    source = source.resolve()
    if destination == source or destination in source.parents or source in destination.parents:
        raise ValueError('Installation destination must be separate from package source')
    if destination.exists():
        if not destination.is_dir() or not (destination / 'SKILL.md').is_file():
            raise ValueError('Destination exists but is not an installed skill')
        if any(p.is_symlink() for p in destination.rglob('*')):
            raise ValueError('Existing skill contains symlinks; inspect it before updating')
        if not re.search(r'^name: apple-health-strava-sync\s*$', (destination / 'SKILL.md').read_text(), re.M):
            raise ValueError('Destination belongs to another skill')
        if all((destination / n).is_file() and (destination / n).read_bytes() == data
               for n, data in files.items()):
            return {'status': 'unchanged', 'version': version, 'destination': str(destination),
                    'backup': None, **verify(destination)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.' + NAME + '-stage-', dir=destination.parent))
    backup = None
    published = False
    try:
        if destination.exists():
            shutil.copytree(destination, stage, dirs_exist_ok=True)
        for name, data in files.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        verify(stage)
        if destination.exists():
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            backup = destination.with_name('.' + destination.name + '-backup-' + stamp + '-' + uuid.uuid4().hex[:8])
            destination.rename(backup)
        stage.rename(destination)
        published = True
        result = verify(destination)
    except Exception:
        if published:
            shutil.rmtree(destination)
        if backup is not None and backup.exists():
            backup.rename(destination)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {'status': 'updated' if backup else 'installed', 'version': version,
            'destination': str(destination), 'backup': str(backup) if backup else None, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', required=True, help='Exact skill directory selected using the host installation rules')
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.dest), ensure_ascii=False, indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'Installation failed: {exc}\n')


if __name__ == '__main__':
    main()
