from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .db import Base, engine

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "./data/backups"))
BACKUP_LOCK = threading.RLock()
MAX_UPLOAD_BYTES = int(os.getenv("BACKUP_UPLOAD_MAX_BYTES", str(512 * 1024 * 1024)))


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


def _backup_row(path: Path) -> dict:
    return {
        "name": path.name,
        "path": path,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "created_at": datetime.utcfromtimestamp(path.stat().st_mtime),
    }


def _sqlite_ro(path: Path):
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def validate_backup_file(path: Path) -> dict:
    """Validate a SQLite backup before it is offered for restore.

    Checks file header, SQLite integrity, foreign keys, required XNAT tables,
    and required columns for the currently running Panel schema.
    """
    path = Path(path).resolve()
    if not path.exists() or not path.is_file():
        raise RuntimeError("备份文件不存在")
    if path.suffix.lower() != ".db":
        raise RuntimeError("只允许 .db SQLite 备份文件")
    size = path.stat().st_size
    if size <= 100:
        raise RuntimeError("数据库文件为空或过小")
    if size > MAX_UPLOAD_BYTES:
        raise RuntimeError(f"数据库文件超过允许大小：{MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    with path.open("rb") as fh:
        if fh.read(16) != b"SQLite format 3\x00":
            raise RuntimeError("文件不是有效的 SQLite 3 数据库")

    conn = _sqlite_ro(path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            raise RuntimeError(f"SQLite 完整性检查失败：{integrity[0] if integrity else 'unknown'}")

        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchmany(5)
        if fk_errors:
            raise RuntimeError("SQLite 外键检查失败，数据库存在引用不一致")

        existing_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

        # Base metadata is fully populated by the time this function is called from the Panel.
        required_tables = set(Base.metadata.tables.keys())
        missing_tables = sorted(required_tables - existing_tables)
        if missing_tables:
            raise RuntimeError("数据库结构不兼容，缺少数据表：" + ", ".join(missing_tables[:8]))

        missing_columns: list[str] = []
        for table_name, table in Base.metadata.tables.items():
            actual_columns = {
                str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            }
            required_columns = {column.name for column in table.columns}
            absent = sorted(required_columns - actual_columns)
            if absent:
                missing_columns.append(f"{table_name}({','.join(absent[:6])})")
        if missing_columns:
            raise RuntimeError("数据库结构不兼容，缺少字段：" + "；".join(missing_columns[:6]))

        def safe_count(table_name: str) -> int:
            if table_name not in existing_tables:
                return 0
            try:
                return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])
            except Exception:
                return 0

        return {
            "name": path.name,
            "path": path,
            "size": size,
            "sha256": sha256_file(path),
            "integrity": "ok",
            "users": safe_count("users"),
            "servers": safe_count("servers"),
            "orders": safe_count("orders"),
            "audit_logs": safe_count("audit_logs"),
            "created_at": datetime.utcfromtimestamp(path.stat().st_mtime),
        }
    finally:
        conn.close()


def create_backup(prefix: str = "manual") -> dict:
    with BACKUP_LOCK:
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

        return _backup_row(dst)


def list_backups() -> list[dict]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(BACKUP_DIR.glob("panel-*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rows.append(_backup_row(path))
        except FileNotFoundError:
            continue
    return rows


def prune_backups(keep_daily: int = 7, keep_weekly: int = 4) -> int:
    backups = list_backups()
    keep: set[str] = set()

    daily_days = []
    for row in backups:
        day = row["created_at"].date()
        if day not in daily_days:
            daily_days.append(day)
        if daily_days.index(day) < keep_daily:
            keep.add(row["name"])

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


def safe_backup_path(backup_name: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    candidate = (BACKUP_DIR / Path(backup_name).name).resolve()
    if candidate.parent != BACKUP_DIR.resolve():
        raise RuntimeError("非法备份路径")
    if not candidate.exists() or not candidate.name.startswith("panel-") or candidate.suffix.lower() != ".db":
        raise RuntimeError("备份不存在")
    return candidate


def store_uploaded_backup(fileobj, original_name: str) -> dict:
    """Store an uploaded .db into BACKUP_DIR and validate it before keeping it."""
    original = Path(original_name or "").name
    if not original.lower().endswith(".db"):
        raise RuntimeError("请选择 .db SQLite 备份文件")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    dst = BACKUP_DIR / f"panel-upload-{stamp}.db"
    suffix = 1
    while dst.exists():
        dst = BACKUP_DIR / f"panel-upload-{stamp}-{suffix}.db"
        suffix += 1

    total = 0
    try:
        with dst.open("wb") as out:
            while True:
                chunk = fileobj.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise RuntimeError(f"上传文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB 限制")
                out.write(chunk)
        info = validate_backup_file(dst)
        info["original_name"] = original
        return info
    except Exception:
        try:
            dst.unlink()
        except FileNotFoundError:
            pass
        raise


def _copy_database(source_path: Path, target_path: Path) -> None:
    """Use SQLite backup API instead of a raw file copy so WAL/locks are handled safely."""
    source = sqlite3.connect(str(source_path))
    target = sqlite3.connect(str(target_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def restore_backup(backup_name: str) -> dict:
    """Restore an existing validated backup and auto-rollback if post-restore validation fails."""
    with BACKUP_LOCK:
        current_db = sqlite_database_path()
        if not current_db:
            raise RuntimeError("当前数据库不是 SQLite")

        candidate = safe_backup_path(backup_name)
        candidate_info = validate_backup_file(candidate)

        # Always create a recoverable snapshot immediately before destructive restore.
        safety = create_backup("pre-restore")

        try:
            engine.dispose()
            _copy_database(candidate, current_db)
            engine.dispose()
            restored_info = validate_backup_file(current_db)
            return {
                "restored": candidate_info,
                "safety": safety,
                "current": restored_info,
                "rolled_back": False,
            }
        except Exception as restore_exc:
            rollback_error = None
            try:
                engine.dispose()
                _copy_database(Path(safety["path"]), current_db)
                engine.dispose()
                validate_backup_file(current_db)
            except Exception as exc:
                rollback_error = str(exc)
            if rollback_error:
                raise RuntimeError(
                    f"恢复失败且自动回滚也失败：{restore_exc}; rollback={rollback_error}"
                ) from restore_exc
            raise RuntimeError(f"恢复失败，已自动回滚到恢复前数据库：{restore_exc}") from restore_exc
