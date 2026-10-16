#!/usr/bin/env python3
"""Local preparation and browser-confirmed receipts; browser UI is provided by the host harness."""
import argparse
import json
import re
import sqlite3
import sys
from xml.etree import ElementTree as ET
from datetime import datetime
from pathlib import Path

import health_to_strava as converter
from sport_types import plan as sport_plan, canonical, DEFINITIONS, LABELS, FALLBACKS
from activity_places import enrich, save as save_place
from upload_history import UploadHistory, activity_id, manifest_selection, now

PROJECT = Path(__file__).resolve().parent


def config(project):
    result = json.loads((project / "sync_config.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"[0-9]+", str(result.get("expected_athlete_id", ""))):
        raise ValueError("sync_config.json 缺少有效的 Strava athlete ID")
    return result


def history(project):
    return UploadHistory(project / "state/upload_history.sqlite3")


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def queue(project, manifest, review_id=None):
    cfg = config(project)
    entries = manifest_selection(manifest)
    manifest = Path(manifest).expanduser().resolve()
    if manifest.is_dir():
        manifest = manifest / "manifest.json"
    if manifest.name != "manifest.json":
        raise ValueError("queue 需要导出根目录或 manifest.json")
    ledger = history(project)
    types = {}
    if review_id is not None:
        from upload_review import authorize, approved_types
        types = approved_types(ledger, review_id)
        authorize(ledger, manifest, cfg['expected_athlete_id'], review_id)
    statuses = {r['activity_id']: r['status'] for r in ledger.rows()}
    pending = [e for e in entries if statuses.get(activity_id(e)) not in ('uploaded', 'exist')]
    result = []
    for e in pending:
        path = manifest.parent / e["file"]
        if not path.is_file() or path.stat().st_size > 25_000_000:
            raise ValueError(f"待上传文件缺失或超过 25 MB: {path}")
        mapping = sport_plan(e["sport"])
        if e["file"] in types:
            mapping.update(strava_sport=types[e["file"]], strava_label=LABELS[types[e["file"]]])
        result.append({**mapping, "place": e.get("place"), "file": e["file"], "absolute_path": str(path) if review_id else None, "start": e["start"], "end": e["end"], "sport": e["sport"], "distance_meters": e["distance_meters"], "duration_seconds": e["duration_seconds"], "warnings": e["warnings"]})
    batches = [result[i:i+15] for i in range(0, len(result), 15)]
    return {"manifest": str(manifest), "expected_athlete_id": cfg["expected_athlete_id"], "pending": len(result), "skipped_uploaded": sum(statuses.get(activity_id(e)) == 'uploaded' for e in entries), "skipped_exist": sum(statuses.get(activity_id(e)) == 'exist' for e in entries), "upload_ready": review_id is not None, "review_id": review_id, "batches": [{"number": n+1, "paths": [e["absolute_path"] for e in batch] if review_id else [], "activities": batch} for n, batch in enumerate(batches)]}


def prepare(args, project):
    cfg = config(project)
    source = project / cfg["input_directory"]
    data = converter.HealthInput(source)
    try:
        converter.progress("读取更新后的健康导出目录…")
        workouts, errors = converter.scan_workouts(data)
        # Default is the latest date in the export, not the original one-off end date.
        latest = max((w.start.date() for w in workouts), default=converter.day(cfg["start"]))
        opts = converter.parser().parse_args([
            str(source), "--sport", args.sport or cfg["sport"],
            "--location", args.location or ("all" if args.sport and canonical(args.sport) != canonical(cfg["sport"]) else cfg["location"]),
            "--start", args.start or cfg["start"], "--end", args.end or cfg.get("end") or str(latest),
            "--history-file", str(history(project).path)
        ])
        if opts.start > opts.end:
            raise ValueError("开始日期晚于结束日期")
        selected, duplicates, unknown = converter.select_workouts(workouts, opts)
        pending, skipped = converter.pending_workouts(selected, history(project))
        uploaded_ids = history(project).uploaded_ids()
        summary = {"mode": "preview" if args.preview else "prepare", "source": str(source), "sport": opts.sport, "location": opts.location, "start": str(opts.start), "end_inclusive": str(opts.end), "matched": len(selected), "pending": len(pending), "skipped_uploaded": sum(e['activity_id'] in uploaded_ids for e in skipped), "skipped_exist": sum(e['activity_id'] not in uploaded_ids for e in skipped), "unknown_location": unknown, "invalid_workouts": len(errors), "expected_athlete_id": cfg["expected_athlete_id"]}
        if args.preview or not pending:
            output(summary)
            return
        opts.output = str(project / "exports" / ("sync_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")))
        destination = converter.export(data, pending, opts, {"uploaded_records_skipped": skipped, "duplicate_records_skipped": duplicates, "unknown_location_candidates": unknown, "invalid_workouts": errors})
        output({**summary, "manifest": str(destination / "manifest.json"), "next": "queue --manifest " + str(destination / "manifest.json")})
    finally:
        data.close()


def record(project, manifest, filename, url, evidence, athlete_id, outcome, review_id=None, sport_type=None):
    cfg = config(project)
    if str(athlete_id) != str(cfg["expected_athlete_id"]):
        raise ValueError("当前 Strava 账号与配置不一致，不能登记")
    if not re.fullmatch(r"https://www\.strava\.com/activities/[0-9]+/?", url):
        raise ValueError("需要网页上实际看到的 Strava 活动详情链接")
    if not evidence.strip():
        raise ValueError("需要网页成功结果或重复活动的可见证据")
    if outcome != "saved":
        raise ValueError("重复项须经过完整审核和用户确认后登记为 exist")
    entries = manifest_selection(manifest, [filename])
    if len(entries) != 1:
        raise ValueError("每条网页回执必须对应唯一的 TCX")
    e = entries[0]
    uid = activity_id(e)
    ledger = history(project)
    if review_id is not None:
        from upload_review import authorize
        if uid not in authorize(ledger, manifest, athlete_id, review_id):
            raise ValueError('这条训练不在用户确认的上传范围内')
    target = sport_plan(e["sport"])["strava_sport"]
    if review_id:
        from upload_review import approved_types
        target = approved_types(ledger, review_id)[filename]
    if (target not in ("Ride", "Run") and not sport_type) or (sport_type and canonical(sport_type) != target):
        raise ValueError("必须核对网页已保存的运动类型与用户确认的类型一致")
    if sport_type:
        evidence += " | 已核对运动类型: " + target
    if not ledger.path.exists():
        raise ValueError("没有导出登记记录")
    stamp = now()
    with ledger.connect(write=True) as db:
        current = db.execute("SELECT status FROM activities WHERE activity_id=?", (uid,)).fetchone()
        if not current:
            raise ValueError("这条训练不在当前项目的导出记录中")
        if current['status'] == 'exist':
            raise ValueError('已确认排除的 exist 记录不能登记为上传成功')
        db.execute('''CREATE TABLE IF NOT EXISTS browser_receipts (
            activity_id TEXT PRIMARY KEY, athlete_id TEXT NOT NULL,
            activity_url TEXT NOT NULL, outcome TEXT NOT NULL,
            evidence TEXT NOT NULL, confirmed_at TEXT NOT NULL, manifest TEXT NOT NULL
        )''')
        existing = db.execute("SELECT activity_url FROM browser_receipts WHERE activity_id=?", (uid,)).fetchone()
        if existing and existing["activity_url"].rstrip("/") != url.rstrip("/"):
            raise ValueError("已有回执指向另一条 Strava 活动，请人工核对")
        other = db.execute("SELECT activity_id FROM browser_receipts WHERE activity_url IN (?, ?) AND activity_id<>?", (url.rstrip("/"), url.rstrip("/")+"/", uid)).fetchone()
        if other:
            raise ValueError("同一 Strava 活动链接已对应另一条训练，请核对文件与结果的匹配")
        db.execute("INSERT OR REPLACE INTO browser_receipts VALUES (?, ?, ?, ?, ?, ?, ?)", (uid, str(athlete_id), url.rstrip("/"), outcome, evidence.strip(), stamp, str(Path(manifest).resolve())))
        changed = current["status"] != "uploaded"
        db.execute("UPDATE activities SET status='uploaded', uploaded_at=COALESCE(uploaded_at, ?) WHERE activity_id=?", (stamp, uid))
        if changed or not existing:
            db.execute("INSERT INTO events(activity_id, action, time, file) VALUES (?, ?, ?, ?)", (uid, "browser_"+outcome, stamp, e["file"]))
    converter.refresh_history_csv(ledger)
    return {"file": e["file"], "status": "uploaded", "activity_url": url.rstrip("/"), "newly_marked": changed}


def receipts(project):
    ledger = history(project)
    if not ledger.path.exists():
        return []
    with ledger.connect() as db:
        if not db.execute("SELECT name FROM sqlite_master WHERE name='browser_receipts'").fetchone():
            return []
        return [dict(e) for e in db.execute("SELECT * FROM browser_receipts ORDER BY confirmed_at")]


def main():
    p = argparse.ArgumentParser(description="供具备本地执行与浏览器操作能力的宿主使用的本地准备、待上传队列和网页回执工具；自身不访问 Strava。")
    p.add_argument("--project", type=Path, default=PROJECT)
    actions = p.add_subparsers(dest="action", required=True)
    prep = actions.add_parser("prepare")
    prep.add_argument("--preview", action="store_true")
    for key in ("start", "end", "sport", "location"):
        prep.add_argument("--" + key)
    que = actions.add_parser("queue")
    que.add_argument("--manifest", required=True)
    check = actions.add_parser("cloud-check", help="只读比对浏览器观测与本地待上传活动")
    check.add_argument("--manifest", required=True)
    check.add_argument("--observations", required=True)
    check.add_argument("--output", required=True, help="新的内部 JSON 报告路径；确认列表由对话展示")
    review = actions.add_parser("review", help="完整云端查重后生成用户确认清单")
    review.add_argument("--manifest", required=True)
    review.add_argument("--observations", required=True)
    confirm = actions.add_parser("confirm-review", help="记录用户逐条决定，并将排除项标为 exist")
    for key in ("manifest", "review-id", "decisions", "confirmation"):
        confirm.add_argument("--" + key, required=True)
    upload_queue = actions.add_parser("upload-queue", help="仅返回已通过用户确认的上传路径")
    upload_queue.add_argument("--manifest", required=True)
    upload_queue.add_argument("--review-id", required=True)
    rec = actions.add_parser("record")
    rec.add_argument("--review-id", required=True)
    rec.add_argument("--sport-type", help="网页中实际核对的已保存运动类型")
    for key in ("manifest", "file", "activity-url", "athlete-id", "evidence"):
        rec.add_argument("--" + key, required=True)
    rec.add_argument("--outcome", choices=("saved",), required=True)
    actions.add_parser("receipts")
    actions.add_parser("sports", help="列出可用类型与别名")
    place = actions.add_parser("set-place", help="保存用户明确提供的本次活动大致地点；不写入 TCX")
    for key in ("manifest", "file", "label", "evidence"):
        place.add_argument("--" + key, required=True)
    place.add_argument("--latitude", type=float)
    place.add_argument("--longitude", type=float)
    place.add_argument("--radius-m", type=float, default=1000)
    args = p.parse_args()
    project = args.project.expanduser().resolve()
    try:
        if args.action == "sports":
            output([{"strava_sport": t, "label": label, "aliases": names.split("|")} for t,label,names in DEFINITIONS] + [{"apple_sport":t, "label":label, **sport_plan(t)} for t,label in FALLBACKS.items()])
        elif args.action == "set-place":
            entries = manifest_selection(args.manifest, [args.file])
            if len(entries) != 1:
                raise ValueError("地点必须绑定唯一的本地活动")
            output(save_place(project, entries, {"label":args.label, "source":"user", "evidence":args.evidence,
                         "latitude":args.latitude, "longitude":args.longitude, "radius_m":args.radius_m}))
        elif args.action == "prepare":
            prepare(args, project)
        elif args.action == "queue":
            output(queue(project, args.manifest))
        elif args.action == "upload-queue":
            output(queue(project, args.manifest, args.review_id))
        elif args.action == "review":
            from upload_review import create
            observation = json.loads(Path(args.observations).read_text(encoding="utf-8"))
            output(create(project, history(project), args.manifest, observation, config(project)['expected_athlete_id']))
        elif args.action == "confirm-review":
            from upload_review import confirm
            output(confirm(history(project), args.manifest, config(project)['expected_athlete_id'],
                           args.review_id, args.decisions, args.confirmation))
        elif args.action == "record":
            output(record(project, args.manifest, args.file, args.activity_url, args.evidence, args.athlete_id, args.outcome, args.review_id, args.sport_type))
        elif args.action == "cloud-check":
            from cloud_check import compare, write_report
            observation = json.loads(Path(args.observations).read_text(encoding="utf-8"))
            report = compare(manifest_selection(args.manifest), history(project).skipped_ids(),
                             enrich(project, observation), config(project)["expected_athlete_id"])
            destination = write_report(report, args.output)
            output({"report": destination, "pending_checked": report["pending_checked"],
                    "summary": report["summary"], "ledger_changed": False})
        else:
            output(receipts(project))
        return 0
    except (OSError, ValueError, KeyError, sqlite3.Error, ET.ParseError) as exc:
        converter.progress(f"错误：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
