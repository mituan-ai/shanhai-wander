"""Operations tests use isolated temporary data, never the running site's database."""
import io
import json
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from django.test import SimpleTestCase
from scripts.backup import create_backup
from scripts.restore import restore_backup


class BackupTests(SimpleTestCase):
    def test_live_wal_backup_restores_committed_data_and_persistent_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            (data / ".secret_key").write_text("test-persistent-key")
            with sqlite3.connect(data / "db.sqlite3") as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE saved (title TEXT)")
                connection.execute("INSERT INTO saved VALUES (?)", ("我的私密旅行",))
                connection.commit()
                archive = create_backup(data, root / "archives")
                restored = root / "restored"
                restore_backup(archive, restored)
                with sqlite3.connect(restored / "db.sqlite3") as reader:
                    self.assertEqual(reader.execute("SELECT title FROM saved").fetchone()[0], "我的私密旅行")
                self.assertEqual((restored / ".secret_key").read_text(), "test-persistent-key")
                self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
                # Replacing an existing destination first saves a recovery copy.
                previous = restore_backup(archive, restored)
                self.assertTrue(previous.is_file())

    def test_unsafe_archive_is_rejected_before_destination_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            marker = data / "keep.txt"
            marker.write_text("keep me")
            for name in ("../escape", "/absolute", "data/../../escape"):
                archive = root / "bad.tar.gz"
                with tarfile.open(archive, "w:gz") as writer:
                    info = tarfile.TarInfo(name)
                    info.size = 3
                    writer.addfile(info, io.BytesIO(b"bad"))
                with self.assertRaises(ValueError):
                    restore_backup(archive, data)
                self.assertEqual(marker.read_text(), "keep me")

    def test_catalog_is_complete_and_all_stops_validate(self):
        from django.conf import settings
        from planner.services import validate_stops
        catalog = json.loads((settings.BASE_DIR / "planner/data/routes.json").read_text())
        self.assertEqual(len(catalog), 30)
        self.assertEqual(len({route["slug"] for route in catalog}), 30)
        for route in catalog:
            with self.subTest(route=route["slug"]):
                self.assertGreaterEqual(len(validate_stops(route["stops"], route["days"])), 2)
                self.assertTrue((settings.BASE_DIR / "static/images" / (route["cover_style"] + ".svg")).is_file())
                self.assertTrue(route["source_notes"])


    def test_rebranded_gpx_still_imports_legacy_day_and_kind_fields(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from planner.services import export_gpx, parse_upload
        from planner.services.files import APP_NS, LEGACY_APP_NS
        original = {"title": "山海漫游", "days": 2, "stops": [
            {"id": "a", "name": "杭州", "lng": 120.155, "lat": 30.274, "kind": "town", "day": 1, "stay_minutes": 20, "note": "", "address": ""},
            {"id": "b", "name": "住宿待定", "lng": 120.14, "lat": 30.25, "kind": "hotel", "day": 2, "stay_minutes": 0, "note": "", "address": ""},
        ]}
        xml = export_gpx(original)
        if isinstance(xml, bytes):
            xml = xml.decode()
        legacy = xml.replace(APP_NS, LEGACY_APP_NS)
        imported = parse_upload(SimpleUploadedFile("old-trip.gpx", legacy.encode()))
        self.assertEqual(imported["days"], 2)
        self.assertEqual(imported["stops"][1]["kind"], "hotel")
        self.assertEqual(imported["stops"][1]["day"], 2)
