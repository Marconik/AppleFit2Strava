"""Read-only comparison of browser-observed Strava activities and local exports."""
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from upload_history import activity_id, now


URL = re.compile(r"https://www\.strava\.com/activities/[0-9]+/?$")
from sport_types import canonical, relation, plan
from activity_places import normalize as normalize_place, compare as compare_places

MONTHS = dict(zip(('January February March April May June July August September '
                   'October November December').split(), range(1, 13)))


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('时间必须包含时区')
    return result


def distance(text):
    if text is None or str(text).strip() in ('', '-', '—', '–', 'N/A', '暂无', '无'):
        return None
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*(km|mi|m)', text.strip())
    if not match:
        raise ValueError('无法识别距离及单位: ' + text)
    return float(match[1]) * {'km': 1000, 'mi': 1609.344, 'm': 1}[match[2]]


def duration(text):
    if text is None or str(text).strip() in ('', '-', '—', '–', 'N/A'):
        return None
    text = str(text).strip()
    if re.fullmatch(r'\d+:\d{2}(?::\d{2})?', text):
        parts = list(map(int, text.split(':')))
        if any(p >= 60 for p in parts[1:]):
            raise ValueError('时长分秒无效: ' + text)
        return sum(n * 60**i for i,n in enumerate(reversed(parts)))
    pattern = r'(?:(\d+(?:\.\d+)?)\s*(?:h|hr|小时|时)\s*)?(?:(\d+(?:\.\d+)?)\s*(?:m|min|分钟|分)\s*)?(?:(\d+(?:\.\d+)?)\s*(?:s|sec|秒)\s*)?'
    match = re.fullmatch(pattern, text, re.I)
    if not match or not any(match.groups()):
        raise ValueError('无法识别时长: ' + text)
    return sum(float(n or 0)*factor for n,factor in zip(match.groups(), (3600,60,1)))


def measure(value, label):
    if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0):
        raise ValueError(label+'无效')
    return value


def observed_duration(item, fallback=None):
    value = item.get('duration_seconds')
    kind = item.get('duration_kind', 'unknown')
    if kind not in ('elapsed','active','moving','unknown'):
        raise ValueError('时长口径必须是 elapsed/active/moving/unknown')
    if value is None:
        text = item.get('duration_text', fallback)
        if text is None:
            match = re.search(r'(全程耗时|Elapsed [Tt]ime|活动时间|Active [Tt]ime|移动时间|Moving [Tt]ime|时间|Time|时长)\s*\n([^\n]+)', item.get('text',''))
            if match:
                text = match[2]
                if 'duration_kind' not in item:
                    kind = {'全程耗时':'elapsed','Elapsed Time':'elapsed','Elapsed time':'elapsed',
                            '活动时间':'active','Active Time':'active','Active time':'active',
                            '移动时间':'moving','Moving Time':'moving','Moving time':'moving'}.get(match[1],'unknown')
        value = duration(text)
    return measure(value,'云端时长'),kind


def browser_time(text, observed_at, zone):
    """UI times have minute precision; never invent seconds or a timezone."""
    text = text.strip()
    today = observed_at.astimezone(zone).date()
    for word, delta in [('今天', 0), ('昨天', 1), ('Today', 0), ('Yesterday', 1)]:
        if text.startswith(word):
            d = today - timedelta(days=delta)
            text = str(d) + ' ' + text[len(word):].replace('于', '').replace('at', '').strip()
            break
    cn = re.fullmatch(r'(\d{4})年(\d+)月(\d+)日\s*(?:于|星期[一二三四五六日天])\s*(\d+):(\d+)', text)
    en = re.fullmatch(r'(\w+) (\d+), (\d{4}) at (\d+):(\d+) (AM|PM)', text)
    if cn:
        y, m, d, h, minute = map(int, cn.groups())
    elif en:
        month, day, year, hour, minute, ampm = en.groups()
        y, m, d = int(year), MONTHS[month], int(day)
        h, minute = int(hour) % 12 + (12 if ampm == 'PM' else 0), int(minute)
    else:
        iso = re.fullmatch(r'(\d{4})-(\d+)-(\d+) (\d+):(\d+)(?: (AM|PM))?', text)
        if not iso:
            raise ValueError('无法识别页面开始时间: ' + text)
        y, m, d, h, minute = map(int, iso.groups()[:5])
        if iso[6]:
            h = h % 12 + (12 if iso[6] == 'PM' else 0)
    dt = datetime(y, m, d, h, minute, tzinfo=zone)
    # Fall-back transitions cannot be disambiguated from minute-only UI text.
    if dt.utcoffset() != dt.replace(fold=1).utcoffset():
        raise ValueError('页面时间处于夏令时切换区间，需要带 UTC 偏移的详情时间')
    return dt.isoformat()


def normalize(observation, expected_athlete):
    if observation.get('schema_version') != 1:
        raise ValueError('云端观测版本不支持')
    if str(observation.get('athlete_id')) != str(expected_athlete):
        raise ValueError('云端观测账号与项目配置不一致')
    observed = timestamp(observation['observed_at'])
    zone = ZoneInfo(observation['timezone'])
    if not observation.get('timezone_evidence') or not observation.get('account_evidence'):
        raise ValueError('缺少账号或页面时间所属时区的核对说明')
    activities = {}
    # Complete table pagination supplies date-only entries when the feed is truncated.
    for row in observation.get('rows', []):
        cells = row.get('cells', [])
        if not cells:  # Header row
            continue
        if len(cells) < 5 or '加载' in row.get('text', ''):
            raise ValueError('活动列表尚未加载完整')
        links = [u.rstrip('/') for u in row.get('links', []) if URL.fullmatch(u)]
        if len(set(links)) != 1:
            raise ValueError('活动行缺少唯一详情链接')
        match = re.match(r'(\d{4})/(\d+)/(\d+)', cells[1])
        if not match:
            raise ValueError('无法识别活动日期: ' + cells[1])
        day = date(*map(int, match.groups())).isoformat()
        entry = {'url': links[0], 'sport': canonical(cells[0]),
                 'date': day, 'start': None, 'precision': 'day',
                 'distance_meters': distance(cells[4]), 'evidence': row['text']}
        entry['duration_seconds'], entry['duration_kind'] = observed_duration(row, cells[3])
        entry['place'] = normalize_place(row.get('place'))
        entry['environment'] = row.get('environment', 'unknown')
        old = activities.get(links[0])
        if old and old != entry:
            raise ValueError('同一云端活动存在冲突观测')
        activities[links[0]] = entry
    for card in observation.get('cards', []):
        url = card['url'].rstrip('/')
        if not URL.fullmatch(url):
            raise ValueError('云端卡片详情链接无效')
        owners = set(card.get('athlete_urls', []))
        if owners != {'https://www.strava.com/athletes/' + str(expected_athlete)}:
            raise ValueError('活动卡片账号不匹配或存在团体活动，需要单独读取详情')
        start = browser_time(card['time'], observed, zone)
        match = re.search(r'(?:距离|Distance)\s*\n([^\n]+)', card['text'])
        if not card.get('sport'):
            raise ValueError('活动卡片缺少运动类型')
        entry = {'url': url, 'sport': canonical(card['sport']),
                 'date': start[:10], 'start': start, 'precision': 'minute',
                 'distance_meters': distance(match[1]) if match else None, 'evidence': card['text']}
        entry['duration_seconds'], entry['duration_kind'] = observed_duration(card)
        entry['place'] = normalize_place(card.get('place'))
        entry['environment'] = card.get('environment', 'unknown')
        old = activities.get(url)
        if old and (old['date'] != entry['date'] or old['sport'] != entry['sport'] or
                    (old['start'] is not None and old['start'] != entry['start']) or
                    (old['distance_meters'] is not None and entry['distance_meters'] is not None and
                     abs(old['distance_meters'] - entry['distance_meters']) > 20)):
            raise ValueError('列表和详情/卡片观测冲突: ' + url)
        if old:
            if (old.get('duration_kind') == entry['duration_kind'] != 'unknown'
                    and old.get('duration_seconds') is not None and entry['duration_seconds'] is not None
                    and abs(old['duration_seconds']-entry['duration_seconds']) > 2):
                raise ValueError('同一活动的同口径时长冲突: ' + url)
            if compare_places(old.get('place'), entry['place'])['status']=='conflict':
                raise ValueError('同一活动的地点观测冲突: ' + url)
            for field in ('distance_meters','place'):
                if entry[field] is None: entry[field] = old.get(field)
            if entry['duration_seconds'] is None:
                entry['duration_seconds'],entry['duration_kind'] = old.get('duration_seconds'),old.get('duration_kind','unknown')
        activities[url] = entry
    coverage = observation.get('coverage', {})
    complete = coverage.get('complete') is True
    if complete:
        if not coverage.get('evidence'):
            raise ValueError('完整覆盖声明缺少翻页/筛选核对证据')
        if date.fromisoformat(coverage['start']) > date.fromisoformat(coverage['end']):
            raise ValueError('覆盖日期范围错误')
    return list(activities.values()), zone, observed, coverage


def compare(entries, uploaded_ids, observation, expected_athlete):
    cloud, zone, observed, coverage = normalize(observation, expected_athlete)
    candidates = {}
    usage = Counter()
    # Include already uploaded entries in ambiguity detection.
    for local in entries:
        matches = []
        local_start = timestamp(local['start'])
        local_day = local_start.astimezone(zone).date().isoformat()
        meters = measure(local.get('distance_meters'), '本地距离')
        elapsed = (timestamp(local['end'])-local_start).total_seconds()
        active = measure(local.get('duration_seconds'), '本地时长')
        local_place = normalize_place(local.get('place'))
        supplied_place = normalize_place(observation.get('local_places', {}).get(activity_id(local)))
        if supplied_place and local_place and compare_places(local_place,supplied_place)['status']=='conflict':
            raise ValueError('补充地点与本地 GPX 范围冲突，请核对')
        local_place = supplied_place or local_place
        for remote in cloud:
            type_relation = relation(local['sport'], remote['sport'])
            if not type_relation:
                continue
            has_distance = meters is not None and meters>0 and remote['distance_meters'] is not None and remote['distance_meters']>0
            near_distance = has_distance and abs(meters-remote['distance_meters']) <= max(100,meters*.05)
            remote_duration = remote['duration_seconds']
            comparable = elapsed if remote['duration_kind']=='elapsed' else active if remote['duration_kind']=='active' else None
            duration_close = (comparable is not None and comparable>0 and remote_duration is not None and remote_duration>0
                              and abs(comparable-remote_duration)<=max(120,comparable*.05))
            place_check = compare_places(local_place,remote['place'])
            environment_conflict = local.get('location') in ('indoor','outdoor') and remote['environment'] in ('indoor','outdoor') and local['location']!=remote['environment']
            strong = False
            reason = None
            if remote['start']:
                start = timestamp(remote['start'])
                delta = abs((local_start-start).total_seconds())
                same_minute = local_start.astimezone(timezone.utc).replace(second=0,microsecond=0)==start.astimezone(timezone.utc)
                if same_minute or delta<=120:
                    if has_distance:
                        strong = same_minute and near_distance
                        reason = '同一分钟、同类型且距离接近' if strong else '开始时间接近，但时间或距离仍需核对'
                    else:
                        strong = same_minute and duration_close and place_check['status'] in ('same_label','nearby')
                        reason = '无有效距离：开始时间接近；' + ('同口径时长接近' if duration_close else '时长缺失、口径不同或差异较大') + '；' + place_check['reason']
            elif local_day==remote['date'] and (near_distance or not has_distance):
                reason = '同日同类型，缺少开始时间；' + ('距离接近' if has_distance else '无有效距离，需核对时长和地点；'+place_check['reason'])
            if reason:
                if type_relation!='exact':
                    strong=False
                    reason += '；类型为通用/近似对应，需确认具体运动'
                if place_check['status']=='conflict' or environment_conflict:
                    strong=False
                    reason += '；地点或室内外信息冲突'
                matches.append({'url':remote['url'],'strong':strong,'reason':reason,
                                'start':remote['start'],'date':remote['date'],'sport':remote['sport'],
                                'distance_meters':remote['distance_meters'],'duration_seconds':remote_duration,
                                'duration_kind':remote['duration_kind'],'duration_comparable':comparable is not None,
                                'place':remote['place'],'local_place':local_place,'place_check':place_check,
                                'type_relation':type_relation,'environment':remote['environment'],
                                'evidence':remote['evidence']})
                usage[remote['url']]+=1
        candidates[activity_id(local)] = matches
    results = []
    for local in entries:
        uid = activity_id(local)
        if uid in uploaded_ids:
            continue
        found = candidates[uid]
        day = timestamp(local['start']).astimezone(zone).date().isoformat()
        covered = coverage.get('complete') is True and coverage['start'] <= day <= coverage['end']
        if len(found) == 1 and found[0]['strong'] and usage[found[0]['url']] == 1:
            status = 'strong_overlap'
        elif found:
            status = 'needs_review'
        else:
            status = 'not_found_in_covered_range' if covered else 'not_observed_in_partial_snapshot'
        location_audit = observation.get('location_checks', {}).get(uid)
        results.append({'activity_id': uid, 'file': local['file'], 'start': local['start'],
                        'end': local['end'], 'sport': local['sport'],
                        'distance_meters': local.get('distance_meters'), 'duration_seconds': local.get('duration_seconds'),
                        'elapsed_seconds': (timestamp(local['end'])-timestamp(local['start'])).total_seconds(),
                        'place': normalize_place(observation.get('local_places',{}).get(uid) or local.get('place')),
                        'location_audit': location_audit,
                        'upload_type': plan(local['sport']), 'status': status,
                        'candidates': found})
    return {'checked_at': now(), 'observed_at': observed.isoformat(), 'athlete_id': str(expected_athlete),
            'read_only': True, 'ledger_changed': False, 'coverage': coverage,
            'snapshot_age_hours': round((timestamp(now()) - observed).total_seconds() / 3600, 2),
            'match_rules': {'strong_start': 'same UTC minute', 'candidate_time_seconds': 120,
                            'distance_tolerance': 'max(100 meters, 5% of local distance)',
                            'no_distance_duration_tolerance': 'max(120 seconds, 5%); elapsed/active only',
                            'no_distance_place': 'time candidates retained when place unknown/conflicting; no auto exclusion',
                            'timezone': str(zone), 'timezone_evidence': observation['timezone_evidence']},
            'cloud_activities_observed': len(cloud), 'pending_checked': len(results),
            'summary': dict(Counter(r['status'] for r in results)), 'results': results,
            'note': '高置信重合仍为比对结果，不自动登记已上传；未找到仅适用于本次观测范围。网页移动时间不与Apple有效时长直接比较。'}


def write_report(report, output):
    path = Path(output).expanduser().resolve()
    if path.suffix != '.json':
        raise ValueError('报告路径必须以 .json 结尾')
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite an earlier audit report or another project file.
    if path.exists():
        raise ValueError('报告已存在，请使用新文件名')
    with path.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return str(path)
