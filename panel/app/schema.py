from __future__ import annotations

from sqlalchemy import inspect, text

from .db import engine


# SQL fragments intentionally use conservative types that work with SQLite and
# common SQL databases. Fresh installs get these columns from SQLAlchemy
# metadata; this helper only fills missing columns on an existing v1.0.x DB.
SCHEMA_EXTENSIONS: dict[str, dict[str, str]] = {
    "users": {
        "announcement_seen_key": "VARCHAR(64)",
    },
    "host_nodes": {
        "maintenance_mode": "BOOLEAN NOT NULL DEFAULT 0",
        "maintenance_reason": "VARCHAR(255)",
        "schedule_cpu_max_percent": "INTEGER NOT NULL DEFAULT 90",
        "schedule_memory_max_percent": "INTEGER NOT NULL DEFAULT 90",
        "schedule_storage_max_percent": "INTEGER NOT NULL DEFAULT 90",
    },
    "servers": {
        "traffic_cycle_mode": "VARCHAR(24) NOT NULL DEFAULT 'rolling30'",
        "traffic_cycle_day": "INTEGER NOT NULL DEFAULT 1",
        "expiry_suspended_at": "DATETIME",
        "expiry_delete_queued_at": "DATETIME",
    },
}


def ensure_schema_extensions() -> list[str]:
    """Apply additive, backwards-compatible v1.1 schema changes.

    XNAT still uses SQLite by default. ALTER TABLE ADD COLUMN is deliberately
    used instead of destructive table rebuilds so a failed upgrade can be
    rolled back with the pre-upgrade DB snapshot created by upgrade-panel.sh.
    """
    changed: list[str] = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table_name, columns in SCHEMA_EXTENSIONS.items():
            if table_name not in existing_tables:
                continue
            existing = {row["name"] for row in inspect(conn).get_columns(table_name)}
            for column_name, ddl in columns.items():
                if column_name in existing:
                    continue
                conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {ddl}'))
                changed.append(f"{table_name}.{column_name}")
                existing.add(column_name)
    return changed
