from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_release
import install_skill
import setup_project


class InstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='skill-install-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'package' / install_skill.NAME
        shutil.copytree(install_skill.ROOT / 'skills' / install_skill.NAME, self.source)
        self.dest = self.root / 'host skill directory' / install_skill.NAME

    def test_new_install_repeat_and_upgrade_preserve_local_data(self):
        result = install_skill.install(self.dest, self.source)
        self.assertEqual(result['status'], 'installed')
        self.assertTrue((self.dest / 'references/cloud-check.md').is_file())
        self.assertFalse((self.dest / 'sync_config.json').exists())
        local = b'{"project":"/synthetic/user-project"}'
        (self.dest / 'local.json').write_bytes(local)
        (self.dest / 'state').mkdir()
        (self.dest / 'state/private.txt').write_text('synthetic preserved data')
        self.assertEqual(install_skill.install(self.dest, self.source)['status'], 'unchanged')
        (self.source / 'VERSION').write_text('1.0.1\n')
        result = install_skill.install(self.dest, self.source)
        self.assertEqual(result['status'], 'updated')
        self.assertEqual((self.dest / 'local.json').read_bytes(), local)
        self.assertEqual((self.dest / 'state/private.txt').read_text(), 'synthetic preserved data')
        self.assertEqual((Path(result['backup']) / 'VERSION').read_text().strip(), '1.0.0')

    def test_incomplete_source_and_unrelated_destination_are_rejected(self):
        (self.source / 'scripts/cloud_check.py').unlink()
        with self.assertRaises(ValueError): install_skill.install(self.dest, self.source)
        self.assertFalse(self.dest.exists())
        shutil.copy2(install_skill.ROOT / 'skills' / install_skill.NAME / 'scripts/cloud_check.py',
                     self.source / 'scripts/cloud_check.py')
        self.dest.mkdir(parents=True)
        (self.dest / 'keep.txt').write_text('unrelated')
        with self.assertRaises(ValueError): install_skill.install(self.dest, self.source)
        self.assertEqual((self.dest / 'keep.txt').read_text(), 'unrelated')

    def test_failed_verification_rolls_back_existing_install(self):
        install_skill.install(self.dest, self.source)
        (self.source / 'VERSION').write_text('1.0.1\n')
        with patch.object(install_skill, 'verify', side_effect=[{}, ValueError('synthetic failure')]):
            with self.assertRaises(ValueError): install_skill.install(self.dest, self.source)
        self.assertEqual((self.dest / 'VERSION').read_text().strip(), '1.0.0')
        self.assertEqual(list(self.dest.parent.glob('.*-stage-*')), [])

    def test_symlinks_and_source_destination_overlap_rejected(self):
        with self.assertRaises(ValueError): install_skill.install(self.source, self.source)
        try:
            (self.source / 'linked').symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaises(ValueError): install_skill.install(self.dest, self.source)

    def test_first_use_initialization_requires_real_inputs_and_preserves_existing(self):
        project = self.root / 'user data'
        result = setup_project.initialize(project, '123', '2026-01-01')
        self.assertTrue((project / 'apple_health_export/workout-routes').is_dir())
        self.assertTrue((project / 'state').is_dir())
        config = Path(result['config']).read_bytes()
        with self.assertRaises(ValueError): setup_project.initialize(project, '456', '2026-02-01')
        self.assertEqual(Path(result['config']).read_bytes(), config)


class ReleaseTests(unittest.TestCase):
    def entries(self):
        return {'VERSION': b'1.0.0\n', 'skills/apple-health-strava-sync/VERSION': b'1.0.0\n',
                'README.md': b'human instructions', 'packaging/README_AGENT.md': b'agent instructions',
                'apple_health_export/workout-routes/.gitkeep': b'', 'sync_config.example.json': b'{}'}

    def test_agent_readme_metadata_and_empty_routes(self):
        content, version = build_release.assemble(self.entries(), 'synthetic-commit')
        with zipfile.ZipFile(BytesIO(content)) as archive:
            prefix = 'AppleFit2Strava-' + version + '/'
            self.assertEqual(archive.read(prefix + 'README.md'), b'agent instructions')
            self.assertNotIn(prefix + 'README_USER.md', archive.namelist())
            self.assertEqual(archive.read(prefix + 'apple_health_export/workout-routes/.gitkeep'), b'')
            meta = json.loads(archive.read(prefix + 'release.json'))
            for item in meta['files']:
                self.assertEqual(len(archive.read(prefix + item['path'])), item['size'])
            self.assertIsNone(archive.testzip())
        self.assertEqual(content, build_release.assemble(self.entries(), 'synthetic-commit')[0])

    def test_private_files_and_unsafe_paths_never_ship(self):
        for name in ('sync_config.json', 'local.json', 'state/observations.json',
                     'exports/a.tcx', 'anywhere/route.gpx', '.conda/python', '../escape',
                     'state/.gitkeep', '.env', 'private.sqlite3'):
            entries = self.entries()
            entries[name] = b'synthetic private content'
            with self.subTest(name=name), self.assertRaises(ValueError):
                build_release.assemble(entries, 'synthetic-commit')


if __name__ == '__main__':
    unittest.main()
