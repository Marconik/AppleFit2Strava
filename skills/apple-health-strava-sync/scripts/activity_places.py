"""Coarse place evidence only; no geocoding, no inferred home/gym address."""
import json
import math
import re
from pathlib import Path


def normalize(place):
    if place is None or place == {}:
        return None
    if not isinstance(place, dict) or not place.get('source') or not place.get('evidence'):
        raise ValueError('地点必须包含来源 source 和证据 evidence')
    label = str(place.get('label', '')).strip()
    lat, lon = place.get('latitude'), place.get('longitude')
    result = {'label': label, 'source': str(place['source']), 'evidence': str(place['evidence'])}
    if lat is not None or lon is not None:
        if any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) for v in (lat,lon)) or not -90<=lat<=90 or not -180<=lon<=180:
            raise ValueError('地点坐标无效')
        radius = place.get('radius_m', 1000)
        if isinstance(radius, bool) or not isinstance(radius, (int,float)) or not math.isfinite(radius) or radius<0:
            raise ValueError('地点范围半径无效')
        result.update(latitude=round(lat,2), longitude=round(lon,2), radius_m=max(1000,radius))
    if not label and lat is None:
        raise ValueError('地点缺少名称或坐标')
    return result


def meters(a,b):
    x,y=math.radians(a['latitude']), math.radians(b['latitude'])
    h=math.sin((x-y)/2)**2+math.cos(x)*math.cos(y)*math.sin(math.radians(a['longitude']-b['longitude'])/2)**2
    return 12742017.6*math.asin(math.sqrt(min(1,h)))


def compare(a,b):
    a,b=normalize(a),normalize(b)
    if not a or not b:
        return {'status':'unknown','reason':'一侧或双方地点缺失，需核对，不能推定在同一地点'}
    if 'latitude' in a and 'latitude' in b:
        separation=meters(a,b)
        status='nearby' if separation<=a['radius_m']+b['radius_m'] else 'conflict'
        return {'status':status,'separation_m':round(separation),'reason':'大致坐标范围相近' if status=='nearby' else '大致坐标范围不重合，需人工核对'}
    normalize_name=lambda s: re.sub(r'[\s，,。·]+','',s).casefold()
    if a['label'] and b['label'] and normalize_name(a['label'])==normalize_name(b['label']):
        return {'status':'same_label','reason':'双方地点名称相同，仅作地区/场所辅助证据'}
    return {'status':'inconclusive','reason':'地点名称不同或粒度不同，未自动判断为异地'}


def from_points(points):
    if not points:
        return None
    # A rounded real point and a covering radius also work across the date line.
    first=points[min(points)]
    center={'latitude':round(first['lat'],2),'longitude':round(first['lon'],2)}
    radius=max(meters(center,{'latitude':p['lat'],'longitude':p['lon']}) for p in points.values())
    return normalize({**center,'radius_m':max(1000,math.ceil(radius/1000)*1000),
                      'label':'GPX 大致活动范围','source':'gpx','evidence':'本次训练起止时间内的有效 GPX 点；坐标保留两位小数，半径向上取整到公里'})


def enrich(project, observation):
    result=dict(observation)
    path=Path(project)/'state/activity_places.json'
    saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    merged=dict(saved)
    for uid, place in observation.get('local_places',{}).items():
        if uid in saved and normalize(saved[uid])!=normalize(place):
            raise ValueError('本地地点补充与已保存证据冲突，请核对')
        merged[uid]=normalize(place)
    result['local_places']=merged
    return result


def save(project, entries, place):
    from upload_history import activity_id
    place=normalize(place)
    path=Path(project)/'state/activity_places.json'
    data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    for entry in entries:
        data[activity_id(entry)]=place
    path.parent.mkdir(parents=True,exist_ok=True)
    import os,tempfile
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.activity-places-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,ensure_ascii=False,indent=2)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return {'updated':len(entries),'file':str(path)}
