#!/usr/bin/env python3
"""Restore a Shanhai Wander backup after STOPPING the application.

An additional pre-restore backup is saved when the destination has a database.
The command intentionally requires --yes to prevent accidental replacements.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tarfile
import tempfile

try:
    from .backup import create_backup
except ImportError:  # Direct command-line invocation.
    from backup import create_backup


def restore_backup(archive_path: Path, data_dir: Path) -> Path | None:
    archive_path = archive_path.resolve()
    data_dir = data_dir.resolve()
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Parse and validate the entire restore in isolation before modifying data.
    with tempfile.TemporaryDirectory(prefix="shanhai-wander-restore-") as temporary:
        stage = Path(temporary)
        seen = set()
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive:
                relative = PurePosixPath(member.name)
                if (relative.is_absolute() or ".." in relative.parts or "\\" in member.name
                        or not relative.parts or member.name in seen):
                    raise ValueError("Backup contains an unsafe or duplicate path")
                seen.add(member.name)
                if relative.parts[0] not in {"data", "manifest.json"}:
                    raise ValueError("Backup contains an unknown top-level path")
                if relative.parts[0] == "manifest.json" and len(relative.parts) != 1:
                    raise ValueError("Invalid manifest path")
                if any(part in {"backups", ".secret_key.lock", ".secret_key.tmp"} for part in relative.parts):
                    raise ValueError("Backup contains reserved runtime paths")
                if not member.isdir() and not member.isfile():
                    raise ValueError("Backup links and special files are not supported")
                destination = stage.joinpath(*relative.parts)
                if member.isdir():
                    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, destination.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    destination.chmod(0o600)
        manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != 1 or manifest.get("database") != "data/db.sqlite3":
            raise ValueError("Unsupported backup format")
        snapshot = stage / "data"
        database = snapshot / "db.sqlite3"
        if not database.is_file():
            raise ValueError("Backup does not contain a database")
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup database integrity check failed")
        if manifest.get("contains_persistent_secret") and not (snapshot / ".secret_key").is_file():
            raise ValueError("Backup is missing its persistent application secret")
        previous = create_backup(data_dir, data_dir / "backups") if (data_dir / "db.sqlite3").exists() else None
        for existing in data_dir.iterdir():
            if existing.name == "backups":
                continue
            if existing.is_dir() and not existing.is_symlink():
                shutil.rmtree(existing)
            else:
                existing.unlink()
        for source in snapshot.iterdir():
            destination = data_dir / source.name
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copyfile(source, destination)
                destination.chmod(0o600)
        data_dir.chmod(0o700)
        return previous


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    parser.add_argument("--yes", action="store_true", help="Confirm the app is stopped and replace data")
    args = parser.parse_args()
    if not args.yes:
        parser.error("Stop the application first, then pass --yes to confirm replacing its data.")
    previous = restore_backup(args.archive, args.data_dir)
    if previous:
        print(f"Previous data preserved in: {previous}")
    print(f"Restore complete: {args.data_dir.resolve()}. Preserve external .env secrets and restart the app.")


if __name__ == "__main__":
    main()
