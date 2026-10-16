import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import sync_strava as sync


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / 'sync_config.json').write_text(json.dumps({'input_directory':'apple_health_export','sport':'cycling','location':'outdoor','start':'2026-07-30','end':None,'expected_athlete_id':'123'}))
        self.out = self.project / 'exports/test'
        (self.out / 'batch_001').mkdir(parents=True)
        self.entries = []
        for day in (11,12):
            entry = {'sport':'Cycling','start':f'2026-10-{day}T10:00:00+08:00','end':f'2026-10-{day}T10:10:00+08:00','source':'Watch','file':f'batch_001/{day}.tcx','distance_meters':2500,'duration_seconds':600,'warnings':[]}
            entry['activity_id'] = sync.activity_id(entry)
            (self.out / entry['file']).write_text('<TrainingCenterDatabase/>')
            self.entries.append(entry)
        self.manifest = self.out / 'manifest.json'
        self.manifest.write_text(json.dumps({'status':'complete','count':2,'workouts':self.entries}))
        sync.history(self.project).register_export(self.entries, self.out)

    def record(self, filename='batch_001/11.tcx', url='https://www.strava.com/activities/456', outcome='saved'):
        return sync.record(self.project, self.manifest, filename, url, 'Synthetic test: saved result matched to filename', '123', outcome)

    def test_receipt_atomically_marks_only_corresponding_activity(self):
        self.assertEqual(sync.queue(self.project, self.manifest)['pending'],2)
        result = self.record()
        self.assertTrue(result['newly_marked'])
        self.assertFalse(self.record()['newly_marked'])
        q = sync.queue(self.project, self.manifest)
        self.assertEqual(q['pending'],1)
        self.assertEqual(q['batches'][0]['activities'][0]['file'],'batch_001/12.tcx')
        self.assertEqual(len(sync.receipts(self.project)),1)

    def test_duplicate_cannot_bypass_user_review(self):
        with self.assertRaises(ValueError):
            self.record(outcome='duplicate')
        self.assertEqual(sync.receipts(self.project), [])
        self.assertEqual(sync.queue(self.project, self.manifest)['pending'], 2)

    def test_wrong_account_or_url_does_not_mark_uploaded(self):
        for account, url in [('other','https://www.strava.com/activities/456'),('123','https://example.com/activities/456')]:
            with self.assertRaises(ValueError):
                sync.record(self.project,self.manifest,'batch_001/11.tcx',url,'saved',account,'saved')
        self.assertEqual(sync.history(self.project).uploaded_ids(),set())

    def test_receipt_link_cannot_be_reused_for_other_workout(self):
        self.record()
        with self.assertRaises(ValueError):
            self.record(filename='batch_001/12.tcx')
        self.assertEqual(sync.queue(self.project,self.manifest)['pending'],1)

    def test_conflicting_receipt_keeps_original_state(self):
        self.record()
        with self.assertRaises(ValueError):
            self.record(url='https://www.strava.com/activities/999')
        self.assertEqual(sync.receipts(self.project)[0]['activity_url'],'https://www.strava.com/activities/456')

    def test_missing_file_stops_before_upload(self):
        (self.out / 'batch_001/11.tcx').unlink()
        with self.assertRaises(ValueError):
            sync.queue(self.project,self.manifest)

    def test_preview_uses_latest_export_date_without_generating_output(self):
        source = self.project / 'apple_health_export'
        source.mkdir()
        (source / '导出.xml').write_text('''<HealthData><Workout workoutActivityType="HKWorkoutActivityTypeCycling" startDate="2026-10-14 10:00:00 +0800" endDate="2026-10-14 10:10:00 +0800" duration="10" durationUnit="min" sourceName="Watch"><MetadataEntry key="HKIndoorWorkout" value="0"/></Workout></HealthData>''')
        args=argparse.Namespace(preview=True,sport=None,location=None,start=None,end=None)
        buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer),contextlib.redirect_stderr(io.StringIO()):
            sync.prepare(args,self.project)
        result=json.loads(buffer.getvalue())
        self.assertEqual(result['end_inclusive'],'2026-10-14')
        self.assertEqual(result['pending'],1)
        self.assertEqual(len(list((self.project/'exports').iterdir())),1)
        args.preview=False
        buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer),contextlib.redirect_stderr(io.StringIO()):
            sync.prepare(args,self.project)
        prepared=json.loads(buffer.getvalue())
        q=sync.queue(self.project,prepared['manifest'])
        self.assertEqual(q['pending'],1)
        filename=q['batches'][0]['activities'][0]['file']
        sync.record(self.project,prepared['manifest'],filename,'https://www.strava.com/activities/777','Synthetic test success','123','saved')
        self.assertEqual(sync.queue(self.project,prepared['manifest'])['pending'],0)
        buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer),contextlib.redirect_stderr(io.StringIO()):
            sync.prepare(args,self.project)
        self.assertEqual(json.loads(buffer.getvalue())['pending'],0)


if __name__=='__main__':
    unittest.main()
