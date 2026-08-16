from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .db import engine

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "./data/backups"))


def sqlite_database_path() -> Path | None:
    url = str(engine.url)
    if not url.startswith("sqlite:///"):
        return None
    path = url[len("sqlite:///"):]
    return Path(path).resolve()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(prefix: str = "manual") -> dict:
    src = sqlite_database_path()
    if not src:
        raise RuntimeError("当前数据库不是 SQLite，内置文件备份不可用")
    if not src.exists():
        raise RuntimeError("数据库文件不存在")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    dst = BACKUP_DIR / f"panel-{prefix}-{stamp}.db"

    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(dst))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    return {
        "name": dst.name,
        "path": dst,
        "size": dst.stat().st_size,
        "sha256": sha256_file(dst),
        "created_at": datetime.utcfromtimestamp(dst.stat().st_mtime),
    }


def list_backups() -> list[dict]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(BACKUP_DIR.glob("panel-*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        rows.append({
            "name": path.name,
            "path": path,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "created_at": datetime.utcfromtimestamp(path.stat().st_mtime),
        })
    return rows


def prune_backups(keep_daily: int = 7, keep_weekly: int = 4) -> int:
    backups = list_backups()
    keep: set[str] = set()

    # Keep latest per UTC day for N days.
    daily_days = []
    for row in backups:
        day = row["created_at"].date()
        if day not in daily_days:
            daily_days.append(day)
        if daily_days.index(day) < keep_daily:
            keep.add(row["name"])

    # Keep one oldest/latest snapshot for each ISO week among recent backups.
    weeks = []
    for row in backups:
        iso = row["created_at"].isocalendar()[:2]
        if iso not in weeks:
            weeks.append(iso)
            if len(weeks) <= keep_weekly:
                keep.add(row["name"])

    removed = 0
    for row in backups:
        if row["name"] not in keep:
            try:
                row["path"].unlink()
                removed += 1
            except FileNotFoundError:
                pass
    return removed


def create_scheduled_backup_if_due() -> bool:
    backups = list_backups()
    now = datetime.utcnow()
    if backups and (now - backups[0]["created_at"]) < timedelta(hours=20):
        return False
    create_backup("daily")
    prune_backups()
    return True


def restore_backup(backup_name: str) -> Path:
    src_db = sqlite_database_path()
    if not src_db:
        raise RuntimeError("当前数据库不是 SQLite")
    candidate = (BACKUP_DIR / Path(backup_name).name).resolve()
    if candidate.parent != BACKUP_DIR.resolve() or not candidate.exists():
        raise RuntimeError("备份不存在")

    safety = src_db.with_suffix(src_db.suffix + ".pre-restore")
    if src_db.exists():
        shutil.copy2(src_db, safety)
    shutil.copy2(candidate, src_db)
    return safety
