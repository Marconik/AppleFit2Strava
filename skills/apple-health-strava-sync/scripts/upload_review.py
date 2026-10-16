"""Persist a full cloud review and user decisions before exposing upload paths."""
import json
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from cloud_check import compare, timestamp, write_report
from activity_places import enrich
from sport_types import plan, LABELS
from upload_history import activity_id, manifest_selection, now


def manifest_path(value):
    path = Path(value).expanduser().resolve()
    if path.is_dir():
        path /= 'manifest.json'
    if path.name != 'manifest.json':
        raise ValueError('审核需要完整 manifest.json，不能只检查一个批次')
    return path


def fresh(report):
    age = (timestamp(now()) - timestamp(report['observed_at'])).total_seconds()
    if not -300 <= age <= 86400:
        raise ValueError('云端观测已超过 24 小时或时间无效，请重新检查并确认')


def create(project, ledger, manifest, observation, athlete):
    path = manifest_path(manifest)
    entries = manifest_selection(path)
    rows = {r['activity_id']: r['status'] for r in ledger.rows()}
    if any(activity_id(e) not in rows for e in entries):
        raise ValueError('清单包含未登记的训练，请先 prepare')
    report = compare(entries, ledger.skipped_ids(), enrich(project, observation), athlete)
    if not report['results']:
        raise ValueError('没有待上传记录')
    for r in report['results']:
        if (not r['distance_meters'] or any(not c['distance_meters'] for c in r['candidates'])) and (
                not isinstance(r['location_audit'], str) or not r['location_audit'].strip()):
            raise ValueError('无距离记录必须先填写 location_checks 地点排查证据；查不到地点也需如实说明')
    fresh(report)
    coverage = report['coverage']
    zone = ZoneInfo(report['match_rules']['timezone'])
    if coverage.get('complete') is not True or any(
        not coverage['start'] <= timestamp(r['start']).astimezone(zone).date().isoformat() <= coverage['end']
        for r in report['results']
    ):
        raise ValueError('必须完整检查全部待上传记录的日期范围后，才能提交用户确认')
    review_id = uuid.uuid4().hex
    ids = {r['activity_id'] for r in report['results']}
    hints = {k:v for k,v in enrich(project,{})['local_places'].items() if k in ids}
    snapshot = {'manifest': str(path), 'entries': entries, 'report': report, 'place_hints': hints}
    with ledger.connect(write=True) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS upload_reviews (
            review_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL,
            created_at TEXT NOT NULL, confirmed_at TEXT,
            confirmation TEXT, decisions TEXT
        )''')
        db.execute('INSERT INTO upload_reviews VALUES (?, ?, ?, NULL, NULL, NULL)',
                   (review_id, json.dumps(snapshot, ensure_ascii=False), now()))
    destination = Path(project) / 'state/upload_reviews' / (review_id + '.json')
    write_report({**report, 'review_id': review_id}, destination)
    suspects = [r for r in report['results'] if r['candidates']]
    required = [r for r in report['results'] if r['candidates'] or r['upload_type']['type_confirmation_required']]
    template = destination.with_name(review_id + '-decisions.json')
    template.write_text(json.dumps({'review_id': review_id, 'decisions': [
        {'file': r['file'], 'action': '', 'activity_url': '', 'strava_sport': '',
         'proposed_type': r['upload_type']} for r in required
    ]}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'review_id': review_id, 'pending': report['pending_checked'], 'suspected': len(suspects),
            'type_confirmations': sum(r['upload_type']['type_confirmation_required'] for r in report['results']),
            'report': str(destination), 'decisions_template': str(template),
            'upload_ready': False, 'statuses_changed': False}


def load(db, review_id):
    if not db.execute("SELECT name FROM sqlite_master WHERE name='upload_reviews'").fetchone():
        raise ValueError('尚未执行上传前云端审核')
    row = db.execute('SELECT * FROM upload_reviews WHERE review_id=?', (review_id,)).fetchone()
    if row is None:
        raise ValueError('找不到指定审核')
    return row, json.loads(row['snapshot'])


def validate(snapshot, manifest, athlete, project=None):
    path = manifest_path(manifest)
    if snapshot['manifest'] != str(path) or snapshot['entries'] != manifest_selection(path):
        raise ValueError('导出清单已变化，必须重新检查全部待上传记录')
    if str(athlete) != snapshot['report']['athlete_id']:
        raise ValueError('审核账号与当前配置不一致')
    if project is not None and 'place_hints' in snapshot:
        ids = {r['activity_id'] for r in snapshot['report']['results']}
        hints = {k:v for k,v in enrich(project,{})['local_places'].items() if k in ids}
        if hints != snapshot['place_hints']:
            raise ValueError('本地地点补充已变化，请重新检查并确认')
    fresh(snapshot['report'])


def confirm(ledger, manifest, athlete, review_id, decision_file, confirmation):
    if not confirmation.strip():
        raise ValueError('必须记录用户在看过完整疑似清单后的明确确认原文')
    document = json.loads(Path(decision_file).read_text(encoding='utf-8'))
    if document.get('review_id') != review_id or not isinstance(document.get('decisions'), list):
        raise ValueError('决定文件与审核编号不一致或格式错误')
    with ledger.connect(write=True) as db:
        review, snapshot = load(db, review_id)
        validate(snapshot, manifest, athlete, ledger.path.parent.parent)
        if review['confirmed_at']:
            raise ValueError('该审核已经确认，请使用 upload-queue；修改决定须新建审核')
        results = snapshot['report']['results']
        suspects = {r['file']: r for r in results if r['candidates'] or r['upload_type']['type_confirmation_required']}
        decisions = {}
        for item in document['decisions']:
            name = item.get('file')
            if name not in suspects or name in decisions or item.get('action') not in ('exist', 'upload'):
                raise ValueError('每条疑似记录必须且只能选择一次 exist 或 upload')
            url = item.get('activity_url', '').rstrip('/')
            if item['action'] == 'exist' and url not in {c['url'].rstrip('/') for c in suspects[name]['candidates']}:
                raise ValueError('排除记录必须选择报告中的实际云端活动链接')
            mapping = suspects[name]['upload_type']
            target = item.get('strava_sport') or (None if mapping['type_confirmation_required'] else mapping['strava_sport'])
            if item['action'] == 'upload' and (target not in LABELS or target not in mapping['possible_strava_sports'] + [mapping['strava_sport']]):
                raise ValueError('上传前必须确认报告中建议的运动类型；近似类型不能静默选择')
            decisions[name] = {'action': item['action'], 'activity_url': url, 'strava_sport': target}
        if set(decisions) != set(suspects):
            raise ValueError('仍有疑似记录未确认，不能上传或部分修改状态')
        # All checks precede mutation. The transaction also protects the whole review.
        statuses = {r['activity_id']: r['status'] for r in db.execute('SELECT activity_id,status FROM activities')}
        pending = {activity_id(e) for e in snapshot['entries'] if statuses.get(activity_id(e)) == 'exported'}
        if pending != {r['activity_id'] for r in results}:
            raise ValueError('待上传状态已变化，请重新检查并确认')
        stamp = now()
        for r in results:
            decision = decisions.setdefault(r['file'], {'action': 'upload', 'activity_url': '', 'strava_sport': r['upload_type']['strava_sport']})
            if decision['action'] == 'exist':
                db.execute("UPDATE activities SET status='exist', uploaded_at=NULL WHERE activity_id=?", (r['activity_id'],))
                db.execute("INSERT INTO events(activity_id,action,time,file) VALUES (?, 'user_confirmed_exist', ?, ?)",
                           (r['activity_id'], stamp, r['file']))
        db.execute('UPDATE upload_reviews SET confirmed_at=?, confirmation=?, decisions=? WHERE review_id=?',
                   (stamp, confirmation.strip(), json.dumps(decisions, ensure_ascii=False), review_id))
    ledger.write_csv()
    excluded = sum(d['action'] == 'exist' for d in decisions.values())
    return {'review_id': review_id, 'exist': excluded, 'approved_upload': len(results)-excluded}


def authorize(ledger, manifest, athlete, review_id):
    with ledger.connect() as db:
        review, snapshot = load(db, review_id)
        validate(snapshot, manifest, athlete, ledger.path.parent.parent)
        if not review['confirmed_at']:
            raise ValueError('用户尚未确认全部疑似记录，不能生成上传队列')
        decisions = json.loads(review['decisions'])
        allowed = {r['activity_id'] for r in snapshot['report']['results'] if decisions[r['file']]['action'] == 'upload'}
        pending = {activity_id(e) for e in snapshot['entries']} - ledger.skipped_ids()
        if not pending <= allowed:
            raise ValueError('待上传列表出现未经确认的记录，请重新检查并确认')
        return allowed


def approved_types(ledger, review_id):
    with ledger.connect() as db:
        review, snapshot = load(db, review_id)
        if not review['confirmed_at']:
            raise ValueError('尚未确认运动类型')
        decisions = json.loads(review['decisions'])
        return {r['file']: decisions[r['file']].get('strava_sport') or plan(r['sport'])['strava_sport']
                for r in snapshot['report']['results'] if decisions[r['file']]['action']=='upload'}
