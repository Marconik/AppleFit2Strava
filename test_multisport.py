import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import activity_places as places
import cloud_check as cloud
import health_to_strava as convert
import sport_types as sports
import sync_strava as sync
import upload_review as review
from upload_history import activity_id
import test_cloud_check
import test_upload_review


class MappingTests(unittest.TestCase):
    def test_common_types_and_aliases(self):
        for raw,target in {'Cycling':'Ride','Running':'Run','Walking':'Walk','Hiking':'Hike',
            'Swimming':'Swim','TraditionalStrengthTraining':'WeightTraining',
            'FunctionalStrengthTraining':'WeightTraining','HIIT':'HighIntensityIntervalTraining',
            '瑜伽':'Yoga','普拉提':'Pilates','椭圆机':'Elliptical','Rowing':'Rowing',
            'StairClimbing':'StairStepper','Climbing':'RockClimbing','DownhillSkiing':'AlpineSki',
            'Snowboarding':'Snowboard','TableTennis':'TableTennis','WheelchairWalkPace':'Wheelchair'}.items():
            with self.subTest(raw=raw):
                self.assertEqual(sports.plan(raw)['strava_sport'],target)
                self.assertEqual(sports.canonical('HKWorkoutActivityType'+raw),target)
        self.assertTrue(sports.selected('FunctionalStrengthTraining','力量训练'))
        self.assertFalse(sports.selected('FunctionalStrengthTraining','TraditionalStrengthTraining'))

    def test_ambiguous_and_unsupported_types_require_confirmation(self):
        for raw in ['PaddleSports','SurfingSports','SnowSports','CrossTraining','Boxing','拳击','FutureType']:
            self.assertTrue(sports.plan(raw)['type_confirmation_required'])
        self.assertEqual(sports.relation('PaddleSports','Kayaking'),'approximate')
        self.assertEqual(sports.relation('SurfingSports','Windsurf'),'approximate')
        self.assertTrue(sports.selected('Boxing','拳击'))
        self.assertFalse(sports.selected('Archery','拳击'))
        for kind in ['EBikeRide','VirtualRide','MountainBikeRide','EMountainBikeRide']:
            self.assertIsNone(sports.relation('Cycling',kind))

    def test_no_distance_workout_export_does_not_invent_route(self):
        w=convert.parse_workout(ET.fromstring('<Workout workoutActivityType="HKWorkoutActivityTypeYoga" startDate="2026-10-15 10:00:00 +0000" endDate="2026-10-15 10:30:00 +0000" duration="25" durationUnit="min"/>'))
        self.assertIsNone(w.distance)
        self.assertEqual(convert.row(w)['strava_sport'],'Yoga')
        payload,_=convert.make_tcx(w,{})
        root=ET.fromstring(payload)
        self.assertFalse(any(e.tag.endswith('Position') for e in root.iter()))
        self.assertEqual(next(e for e in root.iter() if e.tag.endswith('Activity')).attrib['Sport'],'Other')
        self.assertEqual(convert.row(w)['elapsed_seconds'],1800)


class PlaceTests(unittest.TestCase):
    def test_provenance_coarsening_and_conflict(self):
        with self.assertRaises(ValueError): places.normalize({'label':'Gym'})
        p=places.from_points({datetime.now(timezone.utc):{'lat':40.123456,'lon':-73.123456}})
        self.assertEqual((p['latitude'],p['longitude']),(40.12,-73.12))
        self.assertEqual(p['radius_m'],1000)
        self.assertEqual(places.compare(p,{**p,'latitude':41})['status'],'conflict')
        self.assertEqual(places.compare(p,None)['status'],'unknown')
        self.assertEqual(places.compare(p,p)['status'],'nearby')
        with self.assertRaises(ValueError): places.normalize({**p,'latitude':float('nan')})

    def test_place_hints_persist_without_changing_workout(self):
        with tempfile.TemporaryDirectory() as d:
            e={'sport':'Yoga','start':'2026-10-15T10:00:00Z','end':'2026-10-15T10:30:00Z'}
            place={'label':'用户指定健身房','source':'user','evidence':'Synthetic user statement'}
            places.save(d,[e],place)
            self.assertEqual(places.enrich(d,{})['local_places'][activity_id(e)],place)
            self.assertNotIn('place',e)
            with self.assertRaises(ValueError):
                places.enrich(d,{'local_places':{activity_id(e):{**place,'label':'别处'}}})


class NoDistanceTests(unittest.TestCase):
    def setUp(self):
        self.f=test_cloud_check.CloudTests(); self.f.setUp()
        self.f.local.update(sport='Yoga',distance_meters=None,duration_seconds=600)
        self.f.card.update(sport='瑜伽',text='全程耗时\n13:47')
        self.place={'label':'测试公园','source':'user','evidence':'Synthetic location'}

    def result(self): return self.f.check()['results'][0]

    def test_missing_place_keeps_candidate(self):
        r=self.result()
        self.assertEqual(r['status'],'needs_review')
        self.assertEqual(r['candidates'][0]['place_check']['status'],'unknown')
        self.assertIsNone(r['candidates'][0]['distance_meters'])

    def test_elapsed_duration_uses_elapsed_not_active(self):
        self.f.local['place']=self.place
        self.f.card['place']={**self.place,'source':'strava','evidence':'Synthetic actual page'}
        self.assertEqual(self.result()['status'],'strong_overlap')
        self.f.card['text']='移动时间\n10:00'
        self.assertEqual(self.result()['status'],'needs_review')
        self.f.card['text']='活动时间\n10:00'
        self.assertEqual(self.result()['status'],'strong_overlap')

    def test_conflicting_place_keeps_but_downgrades(self):
        self.f.local['place']={**self.place,'latitude':40,'longitude':-73}
        self.f.card['place']={**self.place,'latitude':42,'longitude':-73}
        c=self.result()['candidates'][0]
        self.assertFalse(c['strong'])
        self.assertEqual(c['place_check']['status'],'conflict')

    def test_date_only_zero_and_blank_distance_keep_candidates(self):
        self.f.obs['cards']=[]
        self.f.local['distance_meters']=0
        for value in ('','—','0 m'):
            self.f.obs['rows']=[{'cells':['瑜伽','2026/10/15周四','瑜伽','10:00',value],
                'links':[self.f.card['url']],'text':'Synthetic table'}]
            self.assertEqual(self.result()['status'],'needs_review')

    def test_duration_parser_and_invalid_data(self):
        for value in ('1:30:00','1h 30m','1小时30分钟'):
            self.assertEqual(cloud.duration(value),5400)
        for value in ('-','—','',None): self.assertIsNone(cloud.distance(value))
        with self.assertRaises(ValueError): cloud.duration('1:90')


class MultiReviewTests(unittest.TestCase):
    def fixture(self, kind):
        f=test_upload_review.ReviewTests(); f.setUp(); self.addCleanup(f.doCleanups)
        # Register new fixture types as a new manifest; old identities stay separate.
        for e in f.entries:
            e.update(sport=kind,distance_meters=None)
            e['activity_id']=activity_id(e)
        f.manifest.write_text(json.dumps({'status':'complete','count':2,'workouts':f.entries}))
        f.ledger.register_export(f.entries,f.manifest.parent)
        f.obs['cards']=[]
        f.obs['location_checks']={e['activity_id']:'Synthetic checked: no GPX, no place visible; user review needed' for e in f.entries}
        return f

    def test_missing_location_audit_blocks_review(self):
        f=self.fixture('Yoga')
        f.obs.pop('location_checks')
        with self.assertRaises(ValueError): f.create()

    def test_changed_local_place_invalidates_confirmation(self):
        f=self.fixture('Yoga'); r=f.create()
        places.save(f.project,[f.entries[0]],{'label':'Gym','source':'user','evidence':'Synthetic user supplement'})
        with self.assertRaises(ValueError): f.confirm(r,[])

    def test_unknown_type_requires_explicit_type_even_without_duplicates(self):
        f=self.fixture('Boxing'); r=f.create()
        self.assertEqual(r['type_confirmations'],2)
        with self.assertRaises(ValueError): f.confirm(r,[])
        actions=[{'file':e['file'],'action':'upload'} for e in f.entries]
        with self.assertRaises(ValueError): f.confirm(r,actions)
        for a in actions: a['strava_sport']='Workout'
        f.confirm(r,actions)
        q=sync.queue(f.project,f.manifest,r['review_id'])
        self.assertEqual(q['batches'][0]['activities'][0]['strava_sport'],'Workout')

    def test_ambiguous_type_choice_and_saved_type_verification(self):
        f=self.fixture('PaddleSports'); r=f.create()
        f.confirm(r,[{'file':e['file'],'action':'upload','strava_sport':'Kayaking'} for e in f.entries])
        q=sync.queue(f.project,f.manifest,r['review_id'])
        self.assertEqual(q['batches'][0]['activities'][0]['strava_sport'],'Kayaking')
        args=(f.project,f.manifest,f.entries[0]['file'],'https://www.strava.com/activities/789','Synthetic saved','123','saved',r['review_id'])
        with self.assertRaises(ValueError): sync.record(*args)
        with self.assertRaises(ValueError): sync.record(*args,sport_type='Ride')
        self.assertEqual(sync.record(*args,sport_type='Kayaking')['status'],'uploaded')

    def test_no_distance_exclusion_still_needs_user_confirmation(self):
        f=self.fixture('Yoga')
        f.obs['cards']=[{'url':'https://www.strava.com/activities/456','sport':'Yoga',
            'athlete_urls':['https://www.strava.com/athletes/123'],'time':'2026年10月11日 于 10:00',
            'text':'全程耗时\n10:00'}]
        r=f.create()
        with self.assertRaises(ValueError): sync.queue(f.project,f.manifest,r['review_id'])
        self.assertEqual(f.confirm(r)['exist'],1)
        self.assertEqual(sync.queue(f.project,f.manifest,r['review_id'])['pending'],1)
        report=json.loads(Path(r['report']).read_text())
        candidate=report['results'][0]['candidates'][0]
        self.assertIn('place_check',candidate)
        self.assertIn('unknown',json.dumps(candidate['place_check']))
        self.assertEqual(list(Path(r['report']).parent.glob('*.csv')),[])


if __name__=='__main__': unittest.main()
