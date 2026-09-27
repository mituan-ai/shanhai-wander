#!/usr/bin/env python3
"""Create a private, consistent SQLite snapshot plus persistent application data.

Run inside the app container or with the same DATA_DIR as Django. Output archives
contain credentials and personal itineraries: keep them private.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile


def create_backup(data_dir: Path, output_dir: Path) -> Path:
    data_dir = data_dir.resolve()
    output_dir = output_dir.resolve()
    database = data_dir / "db.sqlite3"
    if not database.is_file():
        raise FileNotFoundError(f"Database not found: {database}")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    destination = output_dir / f"shanhai-wander-{timestamp}.tar.gz"
    temporary_archive = destination.with_suffix(".tmp")
    try:
        with tempfile.TemporaryDirectory(prefix="shanhai-wander-backup-") as temporary:
            snapshot = Path(temporary) / "data"
            snapshot.mkdir(mode=0o700)
            # Never copy a live SQLite file directly: include committed writes in
            # WAL using SQLite's own online backup mechanism.
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(snapshot / "db.sqlite3") as target:
                    source.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("SQLite backup failed its integrity check")
            for path in data_dir.rglob("*"):
                relative = path.relative_to(data_dir)
                if path.is_symlink() or not path.is_file():
                    continue
                if output_dir == path or output_dir in path.parents:
                    continue
                if any(part in {"backups", ".git", "__pycache__"} for part in relative.parts):
                    continue
                if path == database or path.name.endswith(("-wal", "-shm", "-journal", ".lock", ".tmp")):
                    continue
                copied = snapshot / relative
                copied.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(path, copied)
                copied.chmod(0o600)
            (Path(temporary) / "manifest.json").write_text(json.dumps({
                "format": 1,
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "database": "data/db.sqlite3",
                "contains_persistent_secret": (snapshot / ".secret_key").is_file(),
                "note": "Restore while the application is stopped; preserve external .env secrets separately.",
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with tarfile.open(temporary_archive, "w:gz") as archive:
                archive.add(snapshot, arcname="data")
                archive.add(Path(temporary) / "manifest.json", arcname="manifest.json")
            temporary_archive.chmod(0o600)
            temporary_archive.replace(destination)
    except BaseException:
        temporary_archive.unlink(missing_ok=True)
        raise
    return destination


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    print(create_backup(args.data_dir, args.output_dir or args.data_dir / "backups"))


if __name__ == "__main__":
    main()
