"""Schema init + migration for state.db: exchange-aware seen/listings/snapshots + subscribers.

Call `init_schema(db_path)` once at startup, before any DetectorEngine is
constructed. A pre-existing single-exchange state.db (`seen`/`listings` keyed on
`market` alone) is migrated to the multi-exchange schema (composite
`(exchange, market)` key) exactly once, tagging all pre-existing rows
`exchange='upbit'` -- the only exchange that existed before this schema.
DetectorEngine._init_db() creates the same tables with CREATE TABLE IF NOT
EXISTS, so a fresh install (no prior state.db) needs no migration step at all.
"""
import sqlite3


def _conn(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=3000")
    return c


def _has_column(db: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in db.execute(f"PRAGMA table_info({table})"))


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return bool(
        db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    )


def _migrate_legacy_table(db: sqlite3.Connection, table: str, columns_sql: str, select_cols: str) -> None:
    """If `table` exists without an `exchange` column, rename it aside, recreate
    with the new schema, and backfill old rows as exchange='upbit'."""
    if not _table_exists(db, table) or _has_column(db, table, "exchange"):
        return
    db.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
    db.execute(f"CREATE TABLE {table}({columns_sql})")
    db.execute(f"INSERT INTO {table}(exchange, {select_cols}) SELECT 'upbit', {select_cols} FROM {table}_old")
    db.execute(f"DROP TABLE {table}_old")


def _migrate_legacy_listings(db: sqlite3.Connection) -> None:
    """`listings` needs a computed `base` column on migration (old rows only ever
    stored the Upbit-native `market` id, e.g. "KRW-XYZ" -> base "XYZ"), so it can't
    reuse the plain-column-copy `_migrate_legacy_table` helper."""
    if not _table_exists(db, "listings") or _has_column(db, "listings", "exchange"):
        return
    db.execute("ALTER TABLE listings RENAME TO listings_old")
    db.execute(
        "CREATE TABLE listings(exchange TEXT NOT NULL, market TEXT NOT NULL, base TEXT,"
        " english TEXT, korean TEXT, detected_at TEXT, PRIMARY KEY(exchange, market))"
    )
    db.execute(
        "INSERT INTO listings(exchange, market, base, english, korean, detected_at) "
        "SELECT 'upbit', market, substr(market, instr(market, '-') + 1), english, korean, detected_at "
        "FROM listings_old"
    )
    db.execute("DROP TABLE listings_old")


def init_schema(db_path: str) -> None:
    with _conn(db_path) as db:
        db.execute("PRAGMA journal_mode=WAL")

        _migrate_legacy_table(
            db, "seen",
            "exchange TEXT NOT NULL, market TEXT NOT NULL, ts TEXT, PRIMARY KEY(exchange, market)",
            "market, ts",
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS seen("
            "exchange TEXT NOT NULL, market TEXT NOT NULL, ts TEXT,"
            " PRIMARY KEY(exchange, market))"
        )

        _migrate_legacy_listings(db)
        db.execute(
            "CREATE TABLE IF NOT EXISTS listings("
            "exchange TEXT NOT NULL, market TEXT NOT NULL, base TEXT, english TEXT, korean TEXT,"
            " detected_at TEXT, PRIMARY KEY(exchange, market))"
        )

        db.execute(
            "CREATE TABLE IF NOT EXISTS snapshots("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, exchange TEXT NOT NULL DEFAULT 'upbit',"
            " market TEXT, source TEXT, t_offset INTEGER, price REAL, ts TEXT)"
        )
        if not _has_column(db, "snapshots", "exchange"):
            db.execute("ALTER TABLE snapshots ADD COLUMN exchange TEXT DEFAULT 'upbit'")

        db.execute(
            "CREATE TABLE IF NOT EXISTS subscribers("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL UNIQUE,"
            " name TEXT, tier TEXT NOT NULL DEFAULT 'free', enabled INTEGER NOT NULL DEFAULT 1,"
            " created_at TEXT, updated_at TEXT)"
        )
        db.commit()
