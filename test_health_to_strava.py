import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import health_to_strava as app


def workout(start="2026-07-30 10:00:00 +0800", end="2026-07-30 10:10:00 +0800", kind="Cycling", source="Watch", indoor="0", route="workout-routes/ride.gpx", old=False):
    attributes = 'totalDistance="1" totalDistanceUnit="mi" totalEnergyBurned="100" totalEnergyBurnedUnit="kcal"' if old else ''
    meta = f'<MetadataEntry key="HKIndoorWorkout" value="{indoor}"/>' if indoor is not None else ''
    routes = f'<WorkoutRoute><FileReference path="{route}"/></WorkoutRoute>' if route else ''
    stats = '' if old else '<WorkoutStatistics type="HKQuantityTypeIdentifierDistanceCycling" sum="2.5" unit="km"/><WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned" sum="418.4" unit="kJ"/>'
    return f'<Workout workoutActivityType="HKWorkoutActivityType{kind}" startDate="{start}" endDate="{end}" sourceName="{source}" duration="9" durationUnit="min" {attributes}>{meta}{stats}{routes}</Workout>'


def record(time="2026-07-30 10:01:00 +0800", end=None, metric="HeartRate", value="140", source="Watch", unit="count/min"):
    return f'<Record type="HKQuantityTypeIdentifier{metric}" startDate="{time}" endDate="{end or time}" sourceName="{source}" value="{value}" unit="{unit}"/>'


GPX = '''<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="31.2" lon="121.4"><ele>10</ele><time>2026-07-30T02:00:00Z</time></trkpt>
<trkpt lat="31.201" lon="121.401"><ele>11</ele><time>2026-07-30T02:10:00Z</time></trkpt>
</trkseg></trk></gpx>'''


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def data(self, content, zipped=False, chinese=False):
        name = "导出.xml" if chinese else "export.xml"
        xml = f'<?xml version="1.0" encoding="UTF-8"?><HealthData>{content}</HealthData>'
        if zipped:
            path = self.root / "health.zip"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("apple_health_export/" + name, xml)
                z.writestr("apple_health_export/workout-routes/ride.gpx", GPX)
        else:
            path = self.root / name
            path.write_text(xml)
            routes = self.root / "workout-routes"
            routes.mkdir(exist_ok=True)
            (routes / "ride.gpx").write_text(GPX)
        data = app.HealthInput(path)
        self.addCleanup(data.close)
        return data

    def args(self, *extra):
        return app.parser().parse_args([str(self.root / "export.xml"), "--history-file", str(self.root / "state/history.sqlite3"), "--start", "2026-07-30", "--end", "2026-10-10", *extra])

    def test_inclusive_days_original_timezone_and_indoor_filter(self):
        content = workout(start="2026-07-30 00:00:00 +0800", end="2026-07-30 00:10:00 +0800")
        content += workout(start="2026-10-10 23:55:00 +0800", end="2026-10-11 00:05:00 +0800")
        content += workout(start="2026-07-29 23:55:00 +0800", end="2026-07-30 00:05:00 +0800")
        content += workout(start="2026-10-11 00:00:00 +0800", end="2026-10-11 00:10:00 +0800")
        content += workout(indoor="1") + workout(kind="Running") + workout(indoor=None, route=None)
        ws, errors = app.scan_workouts(self.data(content))
        selected, _, unknown = app.select_workouts(ws, self.args())
        self.assertEqual(len(selected), 2)
        self.assertEqual(unknown, 1)
        self.assertEqual(errors, [])
        self.assertEqual(selected[0].start.day, 30)
        self.assertEqual(selected[-1].start.day, 10)

    def test_explicit_timezone(self):
        ws, _ = app.scan_workouts(self.data(workout(start="2026-07-30 00:00:00 +0800", end="2026-07-30 00:10:00 +0800")))
        selected, _, _ = app.select_workouts(ws, self.args("--timezone", "America/New_York"))
        self.assertEqual(selected, [])

    def test_legacy_and_modern_units(self):
        legacy = app.parse_workout(ET.fromstring(workout(old=True)))
        modern = app.parse_workout(ET.fromstring(workout()))
        self.assertAlmostEqual(legacy.distance, 1609.344)
        self.assertEqual(modern.distance, 2500)
        self.assertAlmostEqual(modern.calories, 100)
        self.assertEqual(modern.duration, 540)

    def test_unknown_location_can_be_opted_in(self):
        ws, _ = app.scan_workouts(self.data(workout(route=None, indoor=None)))
        selected, _, count = app.select_workouts(ws, self.args("--include-unknown"))
        self.assertEqual(len(selected), 1)
        self.assertEqual(count, 1)
        self.assertTrue(selected[0].warnings)

    def test_duplicate_source_preference_and_distinct_sessions(self):
        content = workout(source="Strava") + workout() + workout(start="2026-07-30 11:00:00 +0800", end="2026-07-30 11:10:00 +0800")
        ws, _ = app.scan_workouts(self.data(content))
        selected, duplicate, _ = app.select_workouts(ws, self.args())
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(selected[0].source, "Watch")

    def test_sensors_preserve_times_and_use_one_source(self):
        content = workout() + record() + record(value="90", source="Phone")
        content += record(time="2026-07-30 10:02:00 +0800", source="Phone")
        content += record(time="2026-07-30 10:09:00 +0800", end="2026-07-30 10:11:00 +0800")
        content += record(metric="CyclingPower", value="200", unit="W")
        content += record(metric="CyclingCadence", value="80")
        content += record(metric="CyclingSpeed", value="36", unit="km/hr")
        data = self.data(content)
        ws, _ = app.scan_workouts(data)
        app.collect_sensors(data, ws)
        self.assertEqual(list(ws[0].sensors["hr"].values()), [140])
        self.assertEqual(list(ws[0].sensors["speed"].values()), [10])
        payload, _ = app.make_tcx(ws[0], app.read_routes(data, ws[0]))
        root = ET.fromstring(payload)
        ns = {"t": app.TCX, "a": app.EXT}
        self.assertEqual(root.find(".//t:Activity", ns).get("Sport"), "Biking")
        self.assertEqual(root.find(".//t:HeartRateBpm/t:Value", ns).text, "140")
        self.assertEqual(root.find(".//a:Watts", ns).text, "200")
        self.assertEqual(root.find(".//t:Cadence", ns).text, "80")
        self.assertEqual(len(root.findall(".//t:Position", ns)), 2)
        self.assertEqual(root.find(".//t:Id", ns).text, "2026-07-30T02:00:00.000Z")

    def test_overlapping_workouts_and_unordered_records(self):
        content = workout() + workout(start="2026-07-30 10:05:00 +0800", end="2026-07-30 10:15:00 +0800")
        content += record(time="2026-07-30 10:12:00 +0800") + record(time="2026-07-30 10:06:00 +0800")
        data = self.data(content)
        ws, _ = app.scan_workouts(data)
        app.collect_sensors(data, ws)
        self.assertEqual(len(ws[0].sensors["hr"]), 1)
        self.assertEqual(len(ws[1].sensors["hr"]), 2)

    def test_zip_localized_filename_and_routes(self):
        data = self.data(workout(), zipped=True, chinese=True)
        ws, _ = app.scan_workouts(data)
        points = app.read_routes(data, ws[0])
        self.assertEqual(len(points), 2)

    def test_missing_route_reported_and_no_fabricated_heart_rate(self):
        data = self.data(workout(route="workout-routes/missing.gpx"))
        ws, _ = app.scan_workouts(data)
        app.collect_sensors(data, ws)
        payload, _ = app.make_tcx(ws[0], app.read_routes(data, ws[0]))
        self.assertNotIn(b"HeartRateBpm", payload)
        self.assertNotIn(b"Position", payload)
        self.assertTrue(any("路线读取失败" in s for s in ws[0].warnings))

    def test_zip_and_filesystem_path_traversal_rejected(self):
        for zipped in (False, True):
            data = self.data(workout(route="../secret.gpx"), zipped=zipped)
            with self.assertRaises(ValueError):
                with data.open("../secret.gpx"):
                    pass
            ws, _ = app.scan_workouts(data)
            self.assertEqual(app.read_routes(data, ws[0]), {})

    def test_export_batches_manifest_and_no_overwrite(self):
        content = "".join(workout(start=f"2026-08-{d:02d} 10:00:00 +0800", end=f"2026-08-{d:02d} 10:10:00 +0800", route=None) for d in range(1, 17))
        data = self.data(content)
        args = self.args("--export", "--output", str(self.root / "out"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = app.run(args)
        self.assertEqual(len(list((result / "batch_001").glob("*.tcx"))), 15)
        self.assertEqual(len(list((result / "batch_002").glob("*.tcx"))), 1)
        report = json.loads((result / "manifest.json").read_text())
        self.assertEqual(report["count"], 16)
        self.assertEqual(report["status"], "complete")
        self.assertTrue(all(w["warnings"] for w in report["workouts"]))
        ws, _ = app.scan_workouts(data)
        with self.assertRaises(ValueError):
            app.export(data, ws, args, {})

    def test_bad_dates_and_no_matches(self):
        self.data(workout())
        with self.assertRaises(ValueError):
            app.run(self.args("--start", "2027-01-01"))
        target = self.root / "empty"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            app.run(self.args("--sport", "running", "--export", "--output", str(target)))
        self.assertFalse(target.exists())

    def test_invalid_record_reported(self):
        data = self.data(workout() + record(value="nan"))
        ws, _ = app.scan_workouts(data)
        rejected = app.collect_sensors(data, ws)
        self.assertEqual(sum(rejected.values()), 1)
        self.assertEqual(ws[0].sensors, {})

    def test_invalid_workout_reported_without_losing_valid_one(self):
        data = self.data(workout() + workout(start="invalid"))
        ws, errors = app.scan_workouts(data)
        self.assertEqual(len(ws), 1)
        self.assertEqual(len(errors), 1)

    def test_dateline_coordinate_is_tcx_compatible(self):
        data = self.data(workout())
        (self.root / "workout-routes/ride.gpx").write_text(GPX.replace('lon="121.4"', 'lon="180"'))
        ws, _ = app.scan_workouts(data)
        points = app.read_routes(data, ws[0])
        self.assertEqual(points[min(points)]["lon"], -180)

    def test_apple_leading_slash_routes(self):
        for zipped in (False, True):
            data = self.data(workout(route="/workout-routes/ride.gpx"), zipped=zipped)
            ws, _ = app.scan_workouts(data)
            self.assertEqual(len(app.read_routes(data, ws[0])), 2)
            for unsafe in ("/etc/passwd", "/workout-routes/../../secret.gpx"):
                with self.assertRaises(ValueError):
                    with data.open(unsafe):
                        pass

    def test_default_input_is_project_folder(self):
        self.assertEqual(Path(app.parser().parse_args([]).input), app.PROJECT_DIR / "apple_health_export")

    def export_pair(self):
        self.data(workout() + workout(start="2026-07-31 10:00:00 +0800", end="2026-07-31 10:10:00 +0800"))
        args = self.args("--export", "--output", str(self.root / "out"), "--batch-size", "1")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            out = app.run(args)
        return out, app.UploadHistory(args.history_file)

    def test_export_is_not_upload_and_partial_success_skips_only_one(self):
        out, history = self.export_pair()
        self.assertEqual(len(history.rows()), 2)
        self.assertEqual(history.uploaded_ids(), set())
        entries = app.manifest_selection(out / "batch_001")
        self.assertEqual(history.set_uploaded(entries, True), 1)
        self.assertEqual(history.set_uploaded(entries, True), 0)
        data = app.HealthInput(self.root / "export.xml")
        self.addCleanup(data.close)
        ws, _ = app.scan_workouts(data)
        selected, skipped = app.pending_workouts(ws, history)
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(selected[0].start.day, 31)
        selected, skipped = app.pending_workouts(ws, history, include_uploaded=True)
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(skipped), 0)

    def test_upload_history_survives_reopen_renaming_and_timezone_change(self):
        out, history = self.export_pair()
        entries = app.manifest_selection(out)
        history.set_uploaded(entries, True)
        history = app.UploadHistory(history.path)
        changed = dict(entries[0], start="2026-07-30T02:00:00Z", end="2026-07-30T02:10:00+00:00", file="renamed.tcx", source="Renamed Watch")
        self.assertIn(app.activity_id(changed), history.uploaded_ids())
        history.register_export([changed], self.root)
        self.assertEqual(len(history.uploaded_ids()), 2)

    def test_undo_upload_and_audit_events(self):
        out, history = self.export_pair()
        entries = app.manifest_selection(out)
        history.set_uploaded(entries, True)
        history.set_uploaded(entries[:1], False)
        self.assertEqual(len(history.uploaded_ids()), 1)
        with history.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE action='unmarked_uploaded'").fetchone()[0], 1)
        history.write_csv()
        text = history.path.with_suffix(".csv").read_text(encoding="utf-8-sig")
        self.assertIn("uploaded", text)
        self.assertIn("exported", text)

    def test_mark_unknown_entries_rolls_back_entire_change(self):
        out, history = self.export_pair()
        entries = app.manifest_selection(out)
        unknown = dict(entries[0], sport="Walking")
        with self.assertRaises(ValueError):
            history.set_uploaded([entries[0], unknown], True)
        self.assertEqual(history.uploaded_ids(), set())

    def test_manifest_scope_single_batch_all_and_selected_files(self):
        out, _ = self.export_pair()
        files = sorted(out.glob("batch_*/*.tcx"))
        self.assertEqual(len(app.manifest_selection(out)), 2)
        self.assertEqual(len(app.manifest_selection(out / "manifest.json")), 2)
        self.assertEqual(len(app.manifest_selection(files[0])), 1)
        self.assertEqual(len(app.manifest_selection(out, [files[1].name])), 1)
        with self.assertRaises(ValueError):
            app.manifest_selection(out, ["missing.tcx"])
        (out / "INCOMPLETE.txt").touch()
        with self.assertRaises(ValueError):
            app.manifest_selection(out)

    def test_tampered_manifest_rejected(self):
        out, _ = self.export_pair()
        manifest = out / "manifest.json"
        report = json.loads(manifest.read_text())
        report["workouts"][0]["file"] = "../secret.tcx"
        manifest.write_text(json.dumps(report))
        with self.assertRaises(ValueError):
            app.manifest_selection(out)
        report["workouts"][0]["file"] = "batch_001/test.tcx"
        report["workouts"][0]["sport"] = "Walking"
        manifest.write_text(json.dumps(report))
        with self.assertRaises(ValueError):
            app.manifest_selection(out)

    def test_no_input_scan_for_history_actions_and_no_implicit_upload(self):
        out, history = self.export_pair()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            app.run(self.args("--mark-uploaded", str(out / "batch_001")))
            (self.root / "export.xml").unlink()
            app.run(self.args("--history"))
            app.run(self.args("--unmark-uploaded", str(out / "batch_001")))
        self.assertEqual(history.uploaded_ids(), set())

    def test_all_uploaded_generates_no_new_output(self):
        out, history = self.export_pair()
        history.set_uploaded(app.manifest_selection(out), True)
        target = self.root / "second-output"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            app.run(self.args("--export", "--output", str(target)))
        self.assertFalse(target.exists())

    def test_corrupt_history_fails_instead_of_treating_everything_as_new(self):
        from sqlite3 import DatabaseError
        path = self.root / "history.sqlite3"
        path.write_text("corrupted")
        with self.assertRaises(DatabaseError):
            app.UploadHistory(path).uploaded_ids()


if __name__ == "__main__":
    unittest.main()
