from __future__ import annotations

from sqlalchemy import inspect, text

from .db import engine


# SQL fragments intentionally use conservative types that work with SQLite and
# common SQL databases. Fresh installs get these columns from SQLAlchemy
# metadata; this helper only fills missing columns on an existing v1.0.x DB.
SCHEMA_EXTENSIONS: dict[str, dict[str, str]] = {
    "plans": {
        "traffic_reset_price_cents": "INTEGER NOT NULL DEFAULT 0",
        "virtualization_type": "VARCHAR(16) NOT NULL DEFAULT 'lxc'",
    },
    "users": {
        "announcement_seen_key": "VARCHAR(64)",
    },
    "host_nodes": {
        "maintenance_mode": "BOOLEAN NOT NULL DEFAULT 0",
        "maintenance_reason": "VARCHAR(255)",
        "schedule_cpu_max_percent": "INTEGER NOT NULL DEFAULT 90",
        "schedule_memory_max_percent": "INTEGER NOT NULL DEFAULT 90",
        "schedule_storage_max_percent": "INTEGER NOT NULL DEFAULT 90",
        "virtualization_modes": "VARCHAR(32) NOT NULL DEFAULT 'lxc'",
        "kvm_available": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "servers": {
        "traffic_cycle_mode": "VARCHAR(24) NOT NULL DEFAULT 'rolling30'",
        "traffic_cycle_day": "INTEGER NOT NULL DEFAULT 1",
        "expiry_suspended_at": "DATETIME",
        "expiry_delete_queued_at": "DATETIME",
        "virtualization_type": "VARCHAR(16) NOT NULL DEFAULT 'lxc'",
    },
}


def ensure_schema_extensions() -> list[str]:
    """Apply additive, backwards-compatible XNAT schema changes.

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

        # Paid traffic reset was introduced after v1.1.1. For existing plans,
        # use the monthly plan price as a safe non-zero default so upgrading
        # never accidentally exposes a free traffic reset. Admins can change
        # the dedicated reset price per plan afterwards.
        if "plans" in existing_tables:
            plan_columns = {row["name"] for row in inspect(conn).get_columns("plans")}
            if "traffic_reset_price_cents" in plan_columns:
                result = conn.execute(text(
                    'UPDATE "plans" SET "traffic_reset_price_cents" = "monthly_price_cents" '
                    'WHERE COALESCE("traffic_reset_price_cents", 0) <= 0'
                ))
                if (result.rowcount or 0) > 0:
                    changed.append(f"plans.traffic_reset_price_cents.backfill={result.rowcount}")
    return changed
