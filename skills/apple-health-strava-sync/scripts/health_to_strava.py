#!/usr/bin/env python3
"""Local Apple Health export → individually uploadable TCX files. Python 3.11+."""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import re
import shlex
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo
from upload_history import UploadHistory, activity_id, manifest_selection
from sport_types import selected as sport_selected, plan as sport_plan
from activity_places import from_points

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = PROJECT_DIR / "apple_health_export"
DEFAULT_HISTORY = PROJECT_DIR / "state" / "upload_history.sqlite3"

TCX = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
EXT = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"
ET.register_namespace("", TCX)
ET.register_namespace("ae", EXT)
PREFIX = "HKWorkoutActivityType"
METRICS = {
    "HKQuantityTypeIdentifierHeartRate": ("hr", {"count/min": 1, "bpm": 1}, 1, 255),
    "HKQuantityTypeIdentifierCyclingCadence": ("cadence", {"count/min": 1, "rpm": 1}, 0, 254),
    "HKQuantityTypeIdentifierCyclingPower": ("power", {"W": 1, "watt": 1}, 0, 65534),
    "HKQuantityTypeIdentifierCyclingSpeed": ("speed", {"m/s": 1, "km/hr": 1/3.6, "km/h": 1/3.6, "mi/hr": .44704}, 0, 100),
}



def localtag(tag):
    return tag.rsplit("}", 1)[-1]


def instant(value):
    result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError(f"时间缺少时区: {value}")
    return result


def day(value):
    try:
        y, m, d = map(int, value.split("-"))
        return date(y, m, d)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("日期应为 YYYY-MM-DD") from None


def utc(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("非有限数值")
    return value


def converted(value, unit, factors):
    if value is None:
        return None
    if unit not in factors:
        raise ValueError(f"不支持的单位: {unit}")
    result = number(value) * factors[unit]
    if result < 0:
        raise ValueError("数值为负")
    return result


def progress(message):
    print(message, file=sys.stderr, flush=True)


class HealthInput:
    """Open ZIP members in place; never extract arbitrary archive paths."""
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.archive = None
        if not self.path.exists():
            raise ValueError(f"找不到输入文件: {self.path}")
        if self.path.is_file() and zipfile.is_zipfile(self.path):
            self.archive = zipfile.ZipFile(self.path)
            names = self.archive.namelist()
            candidates = [n for n in names if PurePosixPath(n).name.lower() in ("export.xml", "导出.xml") and not n.startswith("__MACOSX/")]
            if len(candidates) != 1:
                self.archive.close()
                raise ValueError("ZIP 中必须恰有一个 export.xml / 导出.xml；也可解压后直接指定 XML。")
            self.xml = candidates[0]
            self.base = PurePosixPath(self.xml).parent
        else:
            if self.path.is_dir():
                candidates = sorted({*self.path.rglob("export.xml"), *self.path.rglob("导出.xml")})
                if len(candidates) != 1:
                    raise ValueError("文件夹中必须恰有一个 export.xml / 导出.xml。")
                self.path = candidates[0]
            self.xml = str(self.path)
            self.base = self.path.parent

    def close(self):
        if self.archive:
            self.archive.close()

    @contextmanager
    def open(self, route=None):
        if route is not None:
            # Apple exports use a leading slash for paths relative to the export root.
            # Only normalize Apple's known directory, not arbitrary absolute paths.
            if route.startswith("/workout-routes/"):
                route = route[1:]
            rel = PurePosixPath(route)
            if rel.is_absolute() or ".." in rel.parts or "\\" in route:
                raise ValueError("路线引用越出健康导出文件夹")
            target = self.base / rel
        else:
            target = self.xml
        if self.archive:
            with self.archive.open(str(target)) as stream:
                yield stream
        else:
            target = Path(target).resolve()
            if route is not None and not target.is_relative_to(self.base.resolve()):
                raise ValueError("路线引用越出健康导出文件夹")
            with target.open("rb") as stream:
                yield stream

    def elements(self):
        with self.open() as stream:
            context = ET.iterparse(stream, events=("start", "end"))
            _, root = next(context)
            if localtag(root.tag) != "HealthData":
                raise ValueError("不是 Apple 健康导出的 HealthData XML")
            depth = 0
            count = 0
            for event, elem in context:
                if event == "start":
                    depth += 1
                else:
                    if depth == 1:
                        yield elem
                        root.remove(elem)
                        elem.clear()
                        count += 1
                        if count % 250000 == 0:
                            progress(f"已扫描 {count:,} 条数据…")
                    depth -= 1


@dataclass
class Workout:
    kind: str
    start: datetime
    end: datetime
    source: str
    indoor: bool | None
    duration: float
    distance: float | None
    calories: float | None
    routes: list[str]
    warnings: list[str] = field(default_factory=list)
    sensors: dict = field(default_factory=dict)
    place: dict | None = None

    @property
    def location(self):
        if self.indoor is True:
            return "indoor"
        if self.indoor is False or self.routes:
            return "outdoor"
        return "unknown"


def parse_workout(elem):
    a = elem.attrib
    start, end = instant(a["startDate"]), instant(a["endDate"])
    if end <= start:
        raise ValueError("训练结束时间不晚于开始时间")
    metadata = {e.get("key"): e.get("value") for e in elem if localtag(e.tag) == "MetadataEntry"}
    indoor = metadata.get("HKIndoorWorkout", metadata.get("HKMetadataKeyIndoorWorkout", "")).lower()
    indoor = True if indoor in ("1", "true", "yes") else False if indoor in ("0", "false", "no") else None
    stats = {e.get("type"): e.attrib for e in elem if localtag(e.tag) == "WorkoutStatistics"}
    kind = a.get("workoutActivityType", "Unknown").removeprefix(PREFIX)
    distance_type = {"Cycling": "DistanceCycling", "Swimming": "DistanceSwimming", "WheelchairWalkPace": "DistanceWheelchair", "WheelchairRunPace": "DistanceWheelchair" , "Running": "DistanceWalkingRunning", "Walking": "DistanceWalkingRunning", "Hiking": "DistanceWalkingRunning", "Rowing": "DistanceRowing", "Paddling": "DistancePaddleSports", "PaddleSports": "DistancePaddleSports", "CrossCountrySkiing": "DistanceCrossCountrySkiing", "DownhillSkiing": "DistanceDownhillSnowSports", "Snowboarding": "DistanceDownhillSnowSports", "SkatingSports": "DistanceSkatingSports"}.get(kind, "")
    ds = stats.get("HKQuantityTypeIdentifier" + distance_type, {})
    es = stats.get("HKQuantityTypeIdentifierActiveEnergyBurned", {})
    notes = []

    def quantity(value, unit, factors, label):
        try:
            return converted(value, unit, factors)
        except ValueError as exc:
            notes.append(f"{label}未导出: {exc}")
            return None

    distance = quantity(ds.get("sum", a.get("totalDistance")), ds.get("unit", a.get("totalDistanceUnit")), {"m": 1, "km": 1000, "mi": 1609.344, "ft": .3048, "yd": .9144}, "距离")
    calories = quantity(es.get("sum", a.get("totalEnergyBurned")), es.get("unit", a.get("totalEnergyBurnedUnit")), {"kcal": 1, "Cal": 1, "cal": .001, "kJ": 1/4.184, "J": 1/4184}, "热量")
    duration = quantity(a.get("duration"), a.get("durationUnit"), {"s": 1, "min": 60, "hr": 3600, "h": 3600}, "训练时长")
    elapsed = (end-start).total_seconds()
    if duration is None or duration <= 0 or duration > elapsed + 2:
        notes.append("有效训练时长缺失或异常，使用起止时间差")
        duration = elapsed
    refs = [e.get("path") for e in elem.iter() if localtag(e.tag) == "FileReference" and e.get("path", "").lower().endswith(".gpx")]
    if indoor is True and refs:
        notes.append("室内标记与路线同时存在；筛选以室内标记为准")
    return Workout(kind, start, end, a.get("sourceName", ""), indoor, duration, distance, calories, list(dict.fromkeys(refs)), notes)


def scan_workouts(data):
    workouts, errors = [], []
    for elem in data.elements():
        if localtag(elem.tag) != "Workout":
            continue
        try:
            workouts.append(parse_workout(elem))
        except (ValueError, KeyError) as exc:
            errors.append(f"{elem.get('startDate', '?')}: {exc}")
    return sorted(workouts, key=lambda w: w.start), errors


def select_workouts(workouts, args):
    tz = ZoneInfo(args.timezone) if args.timezone else None
    selected, unknown = [], 0
    for w in workouts:
        d = (w.start.astimezone(tz) if tz else w.start).date()
        if not args.start <= d <= args.end or not sport_selected(w.kind, args.sport):
            continue
        if args.source and w.source != args.source:
            continue
        if args.location != "all" and w.location != args.location:
            if w.location == "unknown":
                unknown += 1
                if args.include_unknown:
                    w.warnings.append("室内/室外无法确定，按 include-unknown 选入")
                    selected.append(w)
            continue
        selected.append(w)
    # Exact same sport + start + end across sources: retain the richest original.
    groups = defaultdict(list)
    for w in selected:
        groups[(w.kind, w.start, w.end)].append(w)
    kept, duplicates = [], []
    for group in groups.values():
        ranked = sorted(group, key=lambda w: (w.source.lower() != "strava", bool(w.routes), w.distance is not None, w.calories is not None), reverse=True)
        kept.append(ranked[0])
        for w in ranked[1:]:
            duplicates.append({"start": w.start.isoformat(), "source": w.source, "kept_source": ranked[0].source})
        if len(ranked) > 1:
            ranked[0].warnings.append(f"去除 {len(ranked)-1} 条类型和起止时间完全相同的重复记录")
    return sorted(kept, key=lambda w: w.start), duplicates, unknown


def collect_sensors(data, workouts, sensor_source=None):
    starts = [w.start.timestamp() for w in workouts]
    max_ends = []
    for w in workouts:
        max_ends.append(max(max_ends[-1] if max_ends else 0, w.end.timestamp()))
    pools = [defaultdict(lambda: defaultdict(dict)) for _ in workouts]
    rejected = Counter()
    for elem in data.elements():
        if localtag(elem.tag) != "Record" or elem.get("type") not in METRICS:
            continue
        try:
            t = instant(elem.attrib["startDate"])
            record_end = instant(elem.attrib.get("endDate", elem.attrib["startDate"]))
            stamp = t.timestamp()
            lo = bisect.bisect_left(max_ends, stamp)
            hi = bisect.bisect_right(starts, stamp)
            if lo == hi:
                continue
            metric, units, lower, upper = METRICS[elem.get("type")]
            value = converted(elem.get("value"), elem.get("unit"), units)
            if value is None or not lower <= value <= upper or record_end < t:
                raise ValueError("传感器值或时间异常")
            source = elem.get("sourceName", "")
            if sensor_source and source != sensor_source:
                continue
            for i in range(lo, hi):
                w = workouts[i]
                if w.start <= t <= record_end <= w.end:
                    pools[i][metric][source][t] = (value, (record_end-t).total_seconds())
        except (KeyError, ValueError) as exc:
            rejected[str(exc)] += 1
    for w, pool in zip(workouts, pools):
        for metric, sources in pool.items():
            source = w.source if w.source in sources else max(sorted(sources), key=lambda s: len(sources[s]))
            entries = sources[source]
            w.sensors[metric] = {t: val[0] for t, val in entries.items()}
            if source != w.source:
                w.warnings.append(f"{metric} 采用时间范围内来源 {source} 的数据；非训练同名来源，请核对")
            if any(span > 10 for _, span in entries.values()):
                w.warnings.append(f"{metric} 含区间汇总样本，保留原起始时间；未伪造高频曲线")
        if not w.sensors.get("hr"):
            w.warnings.append("没有匹配的心率样本")
    return dict(rejected)


def read_routes(data, w):
    points = {}
    for ref in w.routes:
        try:
            current = {}
            with data.open(ref) as stream:
                depth = 0
                for event, elem in ET.iterparse(stream, events=("start", "end")):
                    if event == "start":
                        if localtag(elem.tag) == "trkpt":
                            depth = 1
                        elif depth:
                            depth += 1
                        continue
                    if localtag(elem.tag) == "trkpt":
                        try:
                            fields = {localtag(e.tag): e.text for e in elem}
                            t = instant(fields["time"])
                            lat, lon = number(elem.attrib["lat"]), number(elem.attrib["lon"])
                            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                                raise ValueError("GPS 坐标越界")
                            if lon == 180:
                                lon = -180  # TCX uses the half-open longitude interval [-180, 180).
                            if w.start <= t <= w.end:
                                current[t] = {"lat": lat, "lon": lon}
                                if fields.get("ele") is not None:
                                    current[t]["alt"] = number(fields["ele"])
                        except (ValueError, KeyError, TypeError):
                            w.warnings.append("已跳过无效或无时间戳的 GPS 点")
                        depth = 0
                        elem.clear()
                    elif depth:
                        depth -= 1
                    else:
                        elem.clear()
            points.update(current)
        except (OSError, KeyError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
            w.warnings.append(f"路线读取失败 {ref}: {exc}")
    if not points:
        w.warnings.append("没有可用 GPS 路线")
    return points


def haversine(a, b):
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(math.radians(b["lon"]-a["lon"])/2)**2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, h)))


def child(parent, name, text=None, **attrs):
    e = ET.SubElement(parent, f"{{{TCX}}}{name}", attrs)
    if text is not None:
        e.text = str(text)
    return e


def make_tcx(w, points):
    sport = {"Cycling": "Biking", "Running": "Running"}.get(w.kind, "Other")
    if sport == "Other":
        w.warnings.append(f"TCX 类型为 Other；上传时需核对并选择 {w.kind}")
    merged = {t: dict(p) for t, p in points.items()}
    gps_distance = 0.0
    previous = None
    for t in sorted(points):
        if previous is not None:
            gps_distance += haversine(previous, points[t])
        merged[t]["distance"] = gps_distance
        previous = points[t]
    for metric, samples in w.sensors.items():
        for t, value in samples.items():
            merged.setdefault(t, {})[metric] = value
    merged.setdefault(w.start, {})
    merged.setdefault(w.end, {})
    if not points and w.distance is not None:
        merged[w.start]["distance"] = 0
        merged[w.end]["distance"] = w.distance
    if w.distance is None:
        w.warnings.append("原始总距离缺失，使用 GPS 计算距离" if points else "原始总距离缺失，TCX 必填字段使用 0")
    if w.calories is None:
        w.warnings.append("原始热量缺失，TCX 必填字段使用 0")
    root = ET.Element(f"{{{TCX}}}TrainingCenterDatabase")
    activity = child(child(root, "Activities"), "Activity", Sport=sport)
    child(activity, "Id", utc(w.start))
    lap = child(activity, "Lap", StartTime=utc(w.start))
    child(lap, "TotalTimeSeconds", f"{w.duration:.3f}")
    child(lap, "DistanceMeters", f"{w.distance if w.distance is not None else gps_distance:.3f}")
    child(lap, "Calories", max(0, min(65535, round(w.calories or 0))))
    child(lap, "Intensity", "Active")
    child(lap, "TriggerMethod", "Manual")
    track = child(lap, "Track")
    for t, fields in sorted(merged.items()):
        tp = child(track, "Trackpoint")
        child(tp, "Time", utc(t))
        if "lat" in fields:
            pos = child(tp, "Position")
            child(pos, "LatitudeDegrees", f"{fields['lat']:.9f}")
            child(pos, "LongitudeDegrees", f"{fields['lon']:.9f}")
        if "alt" in fields:
            child(tp, "AltitudeMeters", f"{fields['alt']:.3f}")
        if "distance" in fields:
            child(tp, "DistanceMeters", f"{fields['distance']:.3f}")
        if "hr" in fields:
            child(child(tp, "HeartRateBpm"), "Value", round(fields["hr"]))
        if "cadence" in fields:
            child(tp, "Cadence", round(fields["cadence"]))
        if "speed" in fields or "power" in fields:
            ext = ET.SubElement(child(tp, "Extensions"), f"{{{EXT}}}TPX")
            if "speed" in fields:
                ET.SubElement(ext, f"{{{EXT}}}Speed").text = f"{fields['speed']:.4f}"
            if "power" in fields:
                ET.SubElement(ext, f"{{{EXT}}}Watts").text = str(round(fields["power"]))
    child(activity, "Notes", f"Apple Health | {w.kind} | {w.source} | Original start: {w.start.isoformat()}. GPS track distance is calculated from coordinates; lap distance/duration use Apple summaries when available.")
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), gps_distance


def row(w):
    return {"start": w.start.isoformat(), "end": w.end.isoformat(), "sport": w.kind, "location": w.location, "source": w.source, "duration_seconds": round(w.duration, 3), "distance_meters": w.distance, "calories_kcal": w.calories, "route_files": len(w.routes), "elapsed_seconds": (w.end-w.start).total_seconds(), "place": w.place, **sport_plan(w.kind)}


def pending_workouts(workouts, history, include_uploaded=False):
    uploaded = history.skipped_ids()
    skipped = []
    pending = []
    for w in workouts:
        if not include_uploaded and activity_id(row(w)) in uploaded:
            skipped.append({**row(w), "activity_id": activity_id(row(w))})
        else:
            pending.append(w)
    return pending, skipped


def refresh_history_csv(history):
    try:
        history.write_csv()
    except OSError as exc:
        progress(f"数据库已保存，但 CSV 视图刷新失败：{exc}；可用 --history 查看记录。")


def export(data, workouts, args, diagnostics):
    out = Path(args.output).expanduser().resolve()
    if out.exists():
        raise ValueError("输出目录已存在。请指定一个新目录，以免覆盖或混入旧记录。")
    progress("读取选中训练的心率、骑行功率、踏频和速度…")
    rejected = collect_sensors(data, workouts, args.sensor_source)
    out.mkdir(parents=True, exist_ok=False)
    manifest = []
    try:
        for i, w in enumerate(workouts):
            progress(f"导出 {i+1}/{len(workouts)}：{w.start.isoformat()}")
            points = read_routes(data, w)
            w.place = from_points(points)
            if not w.distance and w.place is None:
                w.warnings.append("无有效距离且没有地点证据；云端查重需核对时长、场所，不能推定地点")
            payload, gps_distance = make_tcx(w, points)
            if len(payload) > 25_000_000:
                raise ValueError("单次训练文件超过 Strava 25 MB 上限，请单独处理")
            name = f"{i+1:04d}_{w.start.strftime('%Y%m%d_%H%M%S')}_{re.sub(r'[^A-Za-z0-9_-]', '_', w.kind)}.tcx"
            target = out / f"batch_{i//args.batch_size+1:03d}" / name
            target.parent.mkdir(exist_ok=True)
            with target.open("xb") as stream:
                stream.write(payload)
            manifest.append({**row(w), "activity_id": activity_id(row(w)), "upload_status_at_export": "not_confirmed", "file": target.relative_to(out).as_posix(), "gps_points": len(points), "gps_distance_meters": round(gps_distance, 3), "sensor_points": {k: len(v) for k, v in w.sensors.items()}, "warnings": list(dict.fromkeys(w.warnings))})
        history = UploadHistory(args.history_file)
        known_status = {r["activity_id"]: r["status"] for r in history.rows()}
        for entry in manifest:
            if entry["activity_id"] in known_status:
                entry["upload_status_at_export"] = known_status[entry["activity_id"]]
        report = {"status": "complete", "history_file": str(history.path), "filters": {"sport": args.sport, "location": args.location, "start": str(args.start), "end_inclusive": str(args.end), "timezone": args.timezone or "original_record_offset", "source": args.source, "sensor_source": args.sensor_source}, "count": len(manifest), **diagnostics, "rejected_sensor_records": rejected, "workouts": manifest}
        (out / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        with (out / "activities.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            fields = [*row(workouts[0]).keys(), "activity_id", "upload_status_at_export", "file", "gps_points", "gps_distance_meters", "sensor_points", "warnings"]
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for item in manifest:
                writer.writerow({**item, "warnings": "；".join(item["warnings"]), "sensor_points": json.dumps(item["sensor_points"])})
        (out / "上传说明.txt").write_text("使用 $apple-health-strava-sync：先完整检查云端疑似重合记录，展示全部候选以及时长、地点和运动类型；用户确认后再上传。\n排除项登记为 exist；有真实网页成功回执的条目才登记为 uploaded。\n其他运动 TCX 使用 Other，上传时按审核队列的 strava_sport 核对/修改并保存。\n缺失距离保持缺失；TCX 必填距离为 0 不表示测量得到零距离。地点提示只用于查重，不生成 GPS 轨迹。\n当前状态以 state/upload_history.sqlite3 为准。\n", encoding="utf-8")
        history.register_export(manifest, out)
        refresh_history_csv(history)
    except Exception as exc:
        (out / "INCOMPLETE.txt").write_text(f"导出中断，当前结果不完整，请勿整批上传。\n{exc}\n", encoding="utf-8")
        raise
    return out


def parser():
    p = argparse.ArgumentParser(description="免费本地转换 Apple 健康历史训练；不会联网或上传数据。")
    p.add_argument("input", nargs="?", default=str(DEFAULT_INPUT), help="默认读取项目内 apple_health_export；也可指定文件夹或 XML")
    p.add_argument("--sport", default="cycling", help="中英文运动名、Strava 类型、完整 Apple 类型或 all；可用 sync_strava.py sports 查看")
    p.add_argument("--location", choices=("outdoor", "indoor", "all"), default="outdoor")
    p.add_argument("--start", type=day, default=day("2000-01-01"))
    p.add_argument("--end", type=day, default=day("2099-12-31"), help="包含结束日")
    p.add_argument("--timezone", help="按指定时区筛日期，如 Asia/Shanghai；默认按每条记录原始时区")
    p.add_argument("--source", help="训练来源精确名称，可从 --inventory 查看")
    p.add_argument("--sensor-source", help="传感器来源精确名称；默认优先训练同名来源")
    p.add_argument("--include-unknown", action="store_true", help="同时选入无法确认室内/室外的训练")
    actions = p.add_mutually_exclusive_group()
    actions.add_argument("--inventory", action="store_true", help="只列全部运动类型/来源及日期跨度，不应用筛选")
    actions.add_argument("--preview", action="store_true", help="预览待上传训练")
    actions.add_argument("--export", action="store_true", help="生成 TCX，并登记为待上传")
    actions.add_argument("--history", action="store_true", help="查看已导出/已上传清单")
    actions.add_argument("--mark-uploaded", metavar="PATH", help="确认指定导出目录、batch 或 TCX 已上传成功")
    actions.add_argument("--unmark-uploaded", metavar="PATH", help="撤销上传标记，下次允许再次导出")
    p.add_argument("--files", nargs="+", help="登记/撤销时只处理这些 TCX 文件名；用于部分上传成功")
    p.add_argument("--history-file", default=str(DEFAULT_HISTORY), help="上传记录 SQLite 文件，默认项目内 state/upload_history.sqlite3")
    p.add_argument("--include-uploaded", action="store_true", help="本次也导出 uploaded/exist 记录；不清除原有标记")
    p.add_argument("--output", help="新的输出目录；默认自动创建 exports/时间戳")
    p.add_argument("--batch-size", type=int, default=15, help="每个上传文件夹的条数，1–15")
    return p


def run(args):
    history = UploadHistory(args.history_file)
    if args.files and not (args.mark_uploaded or args.unmark_uploaded):
        raise ValueError("--files 仅适用于 --mark-uploaded / --unmark-uploaded")
    if args.history:
        rows = history.rows()
        for r in rows:
            label = {"uploaded": "已上传", "exist": "exist", "exported": "待上传"}[r["status"]]
            print(f"{label}\t{r['start']}\t{r['sport']}\t{r['last_file']}")
        uploaded = sum(r["status"] == "uploaded" for r in rows)
        exists = sum(r["status"] == "exist" for r in rows)
        print(f"已上传 {uploaded} 次；exist {exists} 次；待上传 {len(rows)-uploaded-exists} 次。记录库：{history.path}")
        return None
    if args.mark_uploaded or args.unmark_uploaded:
        entries = manifest_selection(args.mark_uploaded or args.unmark_uploaded, args.files)
        changed = history.set_uploaded(entries, bool(args.mark_uploaded))
        refresh_history_csv(history)
        print(f"已{'登记上传成功' if args.mark_uploaded else '撤销上传标记'} {changed} 次；另 {len(entries)-changed} 次状态已一致。")
        print(f"上传记录：{history.path.with_suffix('.csv')}")
        return None
    if args.start > args.end:
        raise ValueError("开始日期不能晚于结束日期")
    if not 1 <= args.batch_size <= 15:
        raise ValueError("batch-size 应为 1–15")
    if args.export and not args.output:
        args.output = str(PROJECT_DIR / "exports" / datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    if args.export and args.inventory:
        raise ValueError("--inventory 与 --export 不能同时使用")
    data = HealthInput(args.input)
    try:
        progress("扫描训练记录（大文件可能需要几分钟）…")
        all_workouts, errors = scan_workouts(data)
        if args.inventory:
            counts = Counter((w.kind, w.location, w.source) for w in all_workouts)
            for (kind, location, source), count in sorted(counts.items()):
                print(f"{kind}\t{location}\t{count}\t{source}")
            if all_workouts:
                print(f"记录跨度：{all_workouts[0].start.isoformat()} 至 {all_workouts[-1].start.isoformat()}")
            if errors:
                print(f"无效训练 {len(errors)} 条：" + "；".join(errors[:5]))
            return None
        selected, duplicates, unknown = select_workouts(all_workouts, args)
        matched_count = len(selected)
        selected, skipped = pending_workouts(selected, history, args.include_uploaded)
        print(f"筛选：{args.sport} / {args.location} / {args.start} 至 {args.end}（含首尾日）")
        print(f"符合筛选 {matched_count} 次；跳过 uploaded/exist {len(skipped)} 次；本次待处理 {len(selected)} 次；去除完全重复记录 {len(duplicates)} 条；室内外不明候选 {unknown} 条；无效训练 {len(errors)} 条")
        for w in selected:
            distance = f"{w.distance/1000:.2f} km" if w.distance is not None else "距离缺失"
            print(f"{w.start.isoformat()}  {w.duration/60:.1f} min  {distance}  路线文件 {len(w.routes)}  {w.source}")
        if not selected:
            print("没有待导出的训练。可用 --history 查看已上传记录，或 --inventory 检查类型/来源。")
            return None
        if args.export:
            out = export(data, selected, args, {"uploaded_records_skipped": skipped, "duplicate_records_skipped": duplicates, "unknown_location_candidates": unknown, "invalid_workouts": errors})
            print(f"已生成 {len(selected)} 个 TCX：{out}\n核对 activities.csv 的 warnings 后再上传。")
            return out
        print("当前仅预览。添加 --export --output 新目录 即可生成文件。")
        return None
    finally:
        data.close()


def input_path(prompt, default=None):
    raw = input(prompt).strip()
    if not raw:
        if default is not None:
            return str(default)
        raise ValueError("请提供一个路径")
    # Finder drag-and-drop uses shell escaping; an ordinary pasted path may contain spaces.
    if Path(raw).expanduser().exists():
        return raw
    else:
        parts = shlex.split(raw)
        if len(parts) != 1:
            raise ValueError("请提供一个输入路径")
        return parts[0]


def wizard():
    print("Apple 健康 → Strava（本地转换）\n默认：户外骑行，2000-01-01 至 2099-12-31。")
    action = input("1 预览/导出  2 登记已上传  3 查看上传记录  4 撤销上传标记 [1]：").strip() or "1"
    if action == "3":
        run(parser().parse_args(["--history"]))
        return
    if action in ("2", "4"):
        target = input_path("拖入已生成的 batch 文件夹或单个 TCX（整批均成功才选文件夹）：\n")
        entries = manifest_selection(target)
        for e in entries:
            print(f"{e['start']}  {e['sport']}  {e['file']}")
        label = "确认以上训练都已在 Strava 上传成功" if action == "2" else "撤销以上训练的上传标记"
        if input(f"{label}，共 {len(entries)} 次？输入 y 继续：").strip().lower() == "y":
            flag = "--mark-uploaded" if action == "2" else "--unmark-uploaded"
            run(parser().parse_args([flag, target]))
        return
    if action != "1":
        raise ValueError("请选择 1、2、3 或 4")
    path = input_path(f"健康数据文件夹 [回车使用 {DEFAULT_INPUT}]：\n", DEFAULT_INPUT)
    sport = input("运动类型 [cycling]：").strip() or "cycling"
    location = input("室内外 outdoor/indoor/all [outdoor]：").strip() or "outdoor"
    start = input("开始日期 [2000-01-01]：").strip() or "2000-01-01"
    end = input("结束日期 [2099-12-31]：").strip() or "2099-12-31"
    args = parser().parse_args([path, "--sport", sport, "--location", location, "--start", start, "--end", end])
    run(args)
    if input("按上述条件生成文件？输入 y 继续：").strip().lower() == "y":
        args.export = True
        run(args)


def main():
    try:
        if len(sys.argv) == 1:
            wizard()
        else:
            run(parser().parse_args())
        return 0
    except (OSError, ValueError, KeyError, sqlite3.Error, ET.ParseError, zipfile.BadZipFile) as exc:
        progress(f"错误：{exc}")
        return 1
    except (KeyboardInterrupt, EOFError):
        progress("已取消。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
