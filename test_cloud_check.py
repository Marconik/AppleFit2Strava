import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cloud_check import browser_time, compare, write_report
import test_sync_strava


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.local = {'sport':'Cycling','start':'2026-10-15T13:57:56-04:00',
                      'end':'2026-10-15T14:11:43-04:00','distance_meters':5152.88,'file':'batch_001/a.tcx'}
        self.card = {'url':'https://www.strava.com/activities/456','sport':'骑行',
                     'athlete_urls':['https://www.strava.com/athletes/123'],
                     'time':'今天于 13:57','text':'Example Athlete\n今天于 13:57\n距离\n5.15 km\n时间\n13m 28s'}
        self.obs = {'schema_version':1,'athlete_id':'123','observed_at':'2026-10-15T23:00:00Z',
                    'timezone':'America/New_York','timezone_evidence':'Test location/timezone',
                    'account_evidence':'Test profile link','cards':[self.card], 'rows':[],
                    'coverage':{'complete':False}}

    def check(self, entries=None):
        return compare(entries or [self.local],set(),self.obs,'123')

    def test_minute_match_distance_recalculation_and_timezone(self):
        self.local['start']='2026-10-15T17:57:56Z'
        self.assertEqual(self.check()['summary'],{'strong_overlap':1})

    def test_same_day_different_time_is_not_a_match(self):
        self.card['time']='今天于 15:57'
        self.assertEqual(self.check()['summary'],{'not_observed_in_partial_snapshot':1})

    def test_partial_and_complete_absence_distinguished(self):
        self.obs['cards']=[]
        self.assertEqual(self.check()['results'][0]['status'],'not_observed_in_partial_snapshot')
        self.obs['coverage']={'complete':True,'start':'2026-10-15','end':'2026-10-15','evidence':'All pages checked'}
        self.assertEqual(self.check()['results'][0]['status'],'not_found_in_covered_range')
        self.obs['coverage']['start']='2026-10-16'
        with self.assertRaises(ValueError): self.check()

    def test_date_only_and_missing_distance_require_review(self):
        self.obs['cards']=[]
        self.obs['rows']=[{'cells':['骑行','2026/10/15周四','骑行','13:28','5.15 km'],
                           'links':[self.card['url']],'text':'Test table row'}]
        self.assertEqual(self.check()['summary'],{'needs_review':1})
        self.obs['cards']=[self.card]; self.local['distance_meters']=None
        self.assertEqual(self.check()['summary'],{'needs_review':1})

    def test_many_to_one_and_one_to_many_are_ambiguous(self):
        other=copy.deepcopy(self.local); other['end']='2026-10-15T14:12:00-04:00'; other['file']='b.tcx'
        self.assertEqual(self.check([self.local,other])['summary'],{'needs_review':2})
        second=copy.deepcopy(self.card); second['url']='https://www.strava.com/activities/789'
        self.obs['cards'].append(second)
        self.assertEqual(self.check()['summary'],{'needs_review':1})

    def test_wrong_account_and_mixed_group_rejected(self):
        self.obs['athlete_id']='999'
        with self.assertRaises(ValueError): self.check()
        self.obs['athlete_id']='123'; self.card['athlete_urls'].append('https://www.strava.com/athletes/999')
        with self.assertRaises(ValueError): self.check()

    def test_electric_and_virtual_rides_not_folded_into_cycling(self):
        for kind in ['模拟骑行','电动自行车骑行','Running']:
            self.card['sport']=kind
            self.assertEqual(self.check()['results'][0]['candidates'],[])

    def test_distance_conflict_needs_review(self):
        self.card['text']='距离\n12.50 km'
        self.assertEqual(self.check()['summary'],{'needs_review':1})

    def test_en_and_relative_dates_and_dst(self):
        z=ZoneInfo('America/New_York'); obs=datetime.fromisoformat('2026-10-15T23:00:00+00:00')
        self.assertEqual(browser_time('September 9, 2026 at 3:48 PM',obs,z),'2026-09-09T15:48:00-04:00')
        self.assertEqual(browser_time('昨天于 11:32',obs,z),'2026-10-14T11:32:00-04:00')
        with self.assertRaises(ValueError): browser_time('2026年11月1日 于 1:30',obs,z)

    def test_report_does_not_overwrite_previous_audit(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'check.json'; write_report(self.check(),p)
            self.assertEqual(list(Path(d).glob('*.csv')), [])
            with self.assertRaises(ValueError): write_report(self.check(),p)
            self.assertEqual(json.loads(p.read_text())['ledger_changed'],False)

    def test_comparison_leaves_real_ledger_unchanged(self):
        fixture=test_sync_strava.SyncTests(); fixture.setUp()
        try:
            from sync_strava import history, queue
            ledger=history(fixture.project)
            before=ledger.path.read_bytes()
            compare(fixture.entries,ledger.uploaded_ids(),self.obs,'123')
            self.assertEqual(ledger.path.read_bytes(),before)
            self.assertEqual(queue(fixture.project,fixture.manifest)['pending'],2)
        finally: fixture.doCleanups()


if __name__=='__main__': unittest.main()
