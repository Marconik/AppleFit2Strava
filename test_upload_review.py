import csv
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import sync_strava as sync
import upload_review as review
import test_sync_strava
from upload_history import UploadHistory, activity_id, now


class ReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = test_sync_strava.SyncTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.project, self.manifest, self.entries = fixture.project, fixture.manifest, fixture.entries
        self.ledger = sync.history(self.project)
        self.obs = {'schema_version': 1, 'athlete_id': '123', 'observed_at': now(),
                    'timezone': 'Asia/Shanghai', 'timezone_evidence': 'Synthetic timezone',
                    'account_evidence': 'Synthetic own profile', 'rows': [],
                    'cards': [{'url': 'https://www.strava.com/activities/456', 'sport': '骑行',
                               'athlete_urls': ['https://www.strava.com/athletes/123'],
                               'time': '2026年10月11日 于 10:00', 'text': '距离\n2.50 km'}],
                    'coverage': {'complete': True, 'start': '2026-10-11', 'end': '2026-10-12',
                                 'evidence': 'Synthetic all pages, unfiltered'}}

    def create(self):
        return review.create(self.project, self.ledger, self.manifest, self.obs, '123')

    def confirm(self, result, actions=None):
        path = self.project / 'decisions.json'
        if actions is None:
            actions = [{'file': self.entries[0]['file'], 'action': 'exist',
                        'activity_url': 'https://www.strava.com/activities/456'}]
        path.write_text(json.dumps({'review_id': result['review_id'], 'decisions': actions}))
        return review.confirm(self.ledger, self.manifest, '123', result['review_id'], path,
                              'Synthetic user confirmation after reading all suspects')

    def test_end_to_end_exclude_and_upload_remainder(self):
        r = self.create()
        self.assertEqual(r['suspected'], 1)
        review_dir = self.project / 'state/upload_reviews'
        self.assertEqual(list(review_dir.glob('*.csv')), [])
        report = json.loads((review_dir / (r['review_id'] + '.json')).read_text())
        self.assertEqual(len(report['results']), 2)
        self.assertEqual(report['results'][0]['candidates'][0]['url'],
                         'https://www.strava.com/activities/456')
        self.assertEqual(self.ledger.skipped_ids(), set())
        self.assertFalse(sync.queue(self.project, self.manifest)['upload_ready'])
        self.assertEqual(sync.queue(self.project, self.manifest)['batches'][0]['paths'], [])
        with self.assertRaises(ValueError): sync.queue(self.project, self.manifest, r['review_id'])
        self.assertEqual(self.confirm(r)['exist'], 1)
        q = sync.queue(self.project, self.manifest, r['review_id'])
        self.assertTrue(q['upload_ready'])
        self.assertEqual((q['pending'], q['skipped_exist'], q['skipped_uploaded']), (1, 1, 0))
        self.assertTrue(q['batches'][0]['paths'][0].endswith('/12.tcx'))
        sync.record(self.project, self.manifest, self.entries[1]['file'],
                    'https://www.strava.com/activities/789', 'Synthetic saved result',
                    '123', 'saved', r['review_id'])
        self.assertEqual(sync.queue(self.project, self.manifest, r['review_id'])['pending'], 0)
        self.ledger.register_export(self.entries, self.manifest.parent)
        self.assertEqual([r['status'] for r in self.ledger.rows()], ['exist', 'uploaded'])
        self.ledger.write_csv()
        with self.ledger.path.with_suffix('.csv').open(encoding='utf-8-sig') as f:
            self.assertEqual([r['status'] for r in csv.DictReader(f)], ['exist', 'uploaded'])
        with self.ledger.connect() as db:
            audit = db.execute('SELECT * FROM upload_reviews').fetchone()
            self.assertIn('Synthetic user confirmation', audit['confirmation'])
            self.assertIn('/456', audit['decisions'])

    def test_user_can_choose_upload_even_strong_match(self):
        r = self.create()
        self.confirm(r, [{'file': self.entries[0]['file'], 'action': 'upload'}])
        self.assertEqual(sync.queue(self.project, self.manifest, r['review_id'])['pending'], 2)
        self.assertEqual(self.ledger.skipped_ids(), set())

    def test_incomplete_coverage_blocks_even_when_all_have_matches(self):
        self.obs['coverage']['end'] = '2026-10-11'
        with self.assertRaises(ValueError): self.create()
        self.obs['coverage']['complete'] = False
        with self.assertRaises(ValueError): self.create()

    def test_missing_invalid_duplicate_or_wrong_link_decision_is_atomic(self):
        self.obs['cards'].append({**self.obs['cards'][0], 'url': 'https://www.strava.com/activities/789',
                                  'time': '2026年10月12日 于 10:00'})
        r = self.create()
        first = {'file': self.entries[0]['file'], 'action': 'exist',
                 'activity_url': 'https://www.strava.com/activities/456'}
        for decisions in ([], [first], [first, first], [first, {'file': self.entries[1]['file'],
                          'action': 'exist', 'activity_url': 'https://www.strava.com/activities/999'}]):
            with self.assertRaises(ValueError): self.confirm(r, decisions)
            self.assertEqual(self.ledger.skipped_ids(), set())

    def test_no_suspects_still_requires_overall_user_confirmation(self):
        self.obs['cards'] = []
        r = self.create()
        with self.assertRaises(ValueError): sync.queue(self.project, self.manifest, r['review_id'])
        self.confirm(r, [])
        self.assertEqual(sync.queue(self.project, self.manifest, r['review_id'])['pending'], 2)

    def test_wrong_account_stale_snapshot_and_manifest_changes_block(self):
        self.obs['athlete_id'] = '999'
        with self.assertRaises(ValueError): self.create()
        self.obs['athlete_id'] = '123'
        self.obs['observed_at'] = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        with self.assertRaises(ValueError): self.create()
        self.obs['observed_at'] = now()
        r = self.create()
        content = json.loads(self.manifest.read_text())
        content['workouts'][0]['distance_meters'] = 1000
        self.manifest.write_text(json.dumps(content))
        with self.assertRaises(ValueError): self.confirm(r)
        self.assertEqual(self.ledger.skipped_ids(), set())

    def test_state_changes_require_new_review(self):
        r = self.create()
        self.ledger.set_uploaded([self.entries[1]], True)
        with self.assertRaises(ValueError): self.confirm(r)
        self.assertNotIn(activity_id(self.entries[0]), self.ledger.skipped_ids())

    def test_unmark_excluded_does_not_sneak_into_approved_queue(self):
        r = self.create()
        self.confirm(r)
        self.ledger.set_uploaded([self.entries[0]], False)
        with self.assertRaises(ValueError): sync.queue(self.project, self.manifest, r['review_id'])

    def test_record_cannot_override_exclusion(self):
        r = self.create()
        self.confirm(r)
        with self.assertRaises(ValueError):
            sync.record(self.project, self.manifest, self.entries[0]['file'],
                        'https://www.strava.com/activities/456', 'test', '123', 'saved', r['review_id'])

    def test_exist_is_skipped_after_new_health_export(self):
        r = self.create()
        self.confirm(r)
        import health_to_strava as converter
        from types import SimpleNamespace
        from unittest.mock import patch
        workouts = [SimpleNamespace(entry=e) for e in self.entries]
        with patch.object(converter, 'row', side_effect=lambda w: w.entry):
            pending, skipped = converter.pending_workouts(workouts, self.ledger)
        self.assertEqual([w.entry['file'] for w in pending], [self.entries[1]['file']])
        self.assertEqual(len(skipped), 1)

    def test_v1_migration_preserves_events_receipts_and_rolls_back_on_failure(self):
        old = UploadHistory(self.project / 'legacy.sqlite3')
        uid = activity_id(self.entries[0])
        with sqlite3.connect(old.path) as db:
            db.executescript('''CREATE TABLE activities (
              activity_id TEXT PRIMARY KEY,sport TEXT NOT NULL,start TEXT NOT NULL,end TEXT NOT NULL,
              source TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('exported','uploaded')),
              first_exported_at TEXT NOT NULL,last_exported_at TEXT NOT NULL,uploaded_at TEXT,last_file TEXT NOT NULL);
              CREATE TABLE events(id INTEGER PRIMARY KEY,activity_id TEXT NOT NULL,action TEXT NOT NULL,time TEXT NOT NULL,file TEXT NOT NULL);
              CREATE TABLE browser_receipts(activity_id TEXT PRIMARY KEY,activity_url TEXT);
              PRAGMA user_version=1;''')
            db.execute('INSERT INTO activities VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (uid,'Cycling',self.entries[0]['start'],self.entries[0]['end'],'Watch','uploaded',now(),now(),now(),'a.tcx'))
            db.execute('INSERT INTO events VALUES (1,?,?,?,?)',(uid,'browser_saved',now(),'a.tcx'))
            db.execute('INSERT INTO browser_receipts VALUES (?,?)',(uid,'https://www.strava.com/activities/456'))
        before = old.rows()
        with self.assertRaises(RuntimeError):
            with old.connect(write=True) as db:
                db.execute("UPDATE activities SET status='exist'")
                raise RuntimeError('abort')
        with old.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
        self.assertEqual(before,old.rows())
        with old.connect(write=True): pass
        self.assertEqual(before,old.rows())
        with old.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT activity_url FROM browser_receipts').fetchone()[0],
                             'https://www.strava.com/activities/456')


if __name__ == '__main__':
    unittest.main()
