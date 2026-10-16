"""Transactional local upload register. No network calls or content hashes."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def canonical_time(value):
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("上传清单中的时间必须包含时区")
    return t.astimezone(timezone.utc).isoformat(timespec="microseconds")


def activity_id(item):
    """Identity survives filename/order/source/UTC offset changes in later exports."""
    start, end = canonical_time(item["start"]), canonical_time(item["end"])
    if start >= end or not isinstance(item["sport"], str) or not item["sport"]:
        raise ValueError("上传清单中的训练类型或起止时间无效")
    return json.dumps([item["sport"], start, end], ensure_ascii=False, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UploadHistory:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()

    @contextmanager
    def connect(self, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self.path), timeout=30)
        else:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2) or (not write and version == 0):
                raise ValueError("上传记录数据库版本不兼容，请保留原文件并检查")
            if write:
                db.execute("BEGIN IMMEDIATE")
                if version == 1:
                    db.execute("ALTER TABLE activities RENAME TO activities_v1")
                db.execute('''CREATE TABLE IF NOT EXISTS activities (
                        activity_id TEXT PRIMARY KEY,
                        sport TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
                        source TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('exported', 'uploaded', 'exist')),
                        first_exported_at TEXT NOT NULL, last_exported_at TEXT NOT NULL,
                        uploaded_at TEXT, last_file TEXT NOT NULL
                    )''')
                if version == 1:
                    db.execute("INSERT INTO activities SELECT * FROM activities_v1")
                    db.execute("DROP TABLE activities_v1")
                db.execute('''CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY, activity_id TEXT NOT NULL,
                        action TEXT NOT NULL, time TEXT NOT NULL, file TEXT NOT NULL
                    )''')
                db.execute("PRAGMA user_version = 2")
            with db:
                yield db
        finally:
            db.close()

    def rows(self):
        if not self.path.exists():
            return []
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM activities ORDER BY start, sport")]

    def uploaded_ids(self):
        return {r["activity_id"] for r in self.rows() if r["status"] == "uploaded"}

    def skipped_ids(self):
        return {r["activity_id"] for r in self.rows() if r["status"] in ("uploaded", "exist")}

    def register_export(self, entries, directory):
        stamp = now()
        # Validate all entries before opening a write transaction.
        prepared = [(activity_id(e), e, str(Path(directory) / e["file"])) for e in entries]
        with self.connect(write=True) as db:
            for uid, e, path in prepared:
                db.execute('''INSERT INTO activities VALUES (?, ?, ?, ?, ?, 'exported', ?, ?, NULL, ?)
                    ON CONFLICT(activity_id) DO UPDATE SET
                    last_exported_at=excluded.last_exported_at, last_file=excluded.last_file
                    ''', (uid, e["sport"], canonical_time(e["start"]), canonical_time(e["end"]), e.get("source", ""), stamp, stamp, path))
                db.execute("INSERT INTO events(activity_id, action, time, file) VALUES (?, 'exported', ?, ?)", (uid, stamp, path))

    def set_uploaded(self, entries, uploaded):
        prepared = [(activity_id(e), e) for e in entries]
        if not prepared:
            raise ValueError("没有选中要登记的训练")
        if not self.path.exists():
            raise ValueError("没有导出登记记录。请使用同一上传记录库重新导出后，再登记已上传。")
        changed = 0
        stamp = now()
        desired = "uploaded" if uploaded else "exported"
        with self.connect(write=True) as db:
            for uid, entry in prepared:
                old = db.execute("SELECT status FROM activities WHERE activity_id=?", (uid,)).fetchone()
                if old is None:
                    raise ValueError("清单含未登记的训练，本次操作已回滚。请检查 --history-file 或重新导出。")
                if old["status"] == desired:
                    continue
                db.execute("UPDATE activities SET status=?, uploaded_at=? WHERE activity_id=?", (desired, stamp if uploaded else None, uid))
                db.execute("INSERT INTO events(activity_id, action, time, file) VALUES (?, ?, ?, ?)", (uid, "confirmed_uploaded" if uploaded else "unmarked_uploaded", stamp, entry["file"]))
                changed += 1
        return changed

    def write_csv(self):
        """Human-readable view; SQLite remains authoritative if this view is edited."""
        fields = ["status", "sport", "start", "end", "source", "uploaded_at", "first_exported_at", "last_exported_at", "last_file", "activity_id"]
        target = self.path.with_suffix(".csv")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".upload-history-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(self.rows())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return target


def manifest_selection(target, names=None):
    """Accept an export directory, manifest, one batch directory, or one TCX."""
    target = Path(target).expanduser().resolve()
    if not target.exists():
        raise ValueError(f"找不到导出结果: {target}")
    if target.is_dir() and (target / "manifest.json").is_file():
        base = target
        mode = "all"
    elif target.name == "manifest.json" and target.is_file():
        base = target.parent
        mode = "all"
    elif target.is_dir() and (target.parent / "manifest.json").is_file():
        base = target.parent
        mode = "batch"
    elif target.suffix.lower() == ".tcx" and (target.parent.parent / "manifest.json").is_file():
        base = target.parent.parent
        mode = "file"
    else:
        raise ValueError("请指定本程序生成的导出目录、manifest.json、batch 文件夹或单个 TCX")
    if (base / "INCOMPLETE.txt").exists():
        raise ValueError("该次导出不完整，不能据此登记上传状态")
    report = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    entries = report.get("workouts")
    if report.get("status") != "complete" or not isinstance(entries, list) or report.get("count") != len(entries):
        raise ValueError("导出清单不完整或格式不正确")
    selected = []
    seen = set()
    for e in entries:
        if not isinstance(e, dict):
            raise ValueError("导出清单中的训练条目格式不正确")
        uid = activity_id(e)
        if e.get("activity_id", uid) != uid or uid in seen:
            raise ValueError("导出清单中的训练标识不一致或重复")
        seen.add(uid)
        rel = Path(e["file"])
        path = (base / rel).resolve()
        if rel.is_absolute() or not path.is_relative_to(base) or path.suffix.lower() != ".tcx":
            raise ValueError("导出清单中的文件路径无效")
        if mode == "batch" and path.parent != target:
            continue
        if mode == "file" and path != target:
            continue
        selected.append(e)
    if names:
        requested = set(names)
        matched = set()
        subset = []
        for e in selected:
            keys = {e["file"], Path(e["file"]).name}
            if keys & requested:
                matched.update(keys & requested)
                subset.append(e)
        if matched != requested:
            raise ValueError("有指定文件不在所选清单内：" + ", ".join(sorted(requested-matched)))
        selected = subset
    if not selected:
        raise ValueError("所选范围没有训练记录")
    return selected
