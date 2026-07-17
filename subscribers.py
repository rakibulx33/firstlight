"""Telegram subscriber CRUD -- multiple recipients, each on a manually-assigned tier.

No billing/payment processing: tiers are just a label + delay (see
CONFIG_DEFAULTS["subscriber_tiers"] in app.py) the operator assigns from the
dashboard. `ensure_legacy_migrated` preserves pre-upgrade behavior: if nobody has
been added as a subscriber yet but `.env` still has a TELEGRAM_CHAT_ID from the
single-recipient era, it's seeded as the first subscriber so upgrading doesn't
silently stop delivering alerts.
"""
import sqlite3
from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=3000")
    return c


def list_subscribers(db_path: str) -> list[dict]:
    with _conn(db_path) as db:
        return [dict(r) for r in db.execute("SELECT * FROM subscribers ORDER BY id")]


def add_subscriber(db_path: str, chat_id: str, name: str = "", tier: str = "free") -> dict:
    chat_id = str(chat_id).strip()
    if not chat_id:
        raise ValueError("chat_id is required")
    now = utcnow_iso()
    with _conn(db_path) as db:
        try:
            cur = db.execute(
                "INSERT INTO subscribers(chat_id, name, tier, enabled, created_at, updated_at)"
                " VALUES(?,?,?,1,?,?)",
                (chat_id, name.strip(), tier, now, now),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"chat_id {chat_id} is already subscribed") from None
        db.commit()
        row = db.execute("SELECT * FROM subscribers WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)


def update_subscriber(db_path: str, sub_id: int, **fields) -> dict | None:
    allowed = {"name", "tier", "enabled"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        with _conn(db_path) as db:
            row = db.execute("SELECT * FROM subscribers WHERE id=?", (sub_id,)).fetchone()
            return dict(row) if row else None
    updates["updated_at"] = utcnow_iso()
    set_sql = ", ".join(f"{k}=?" for k in updates)
    with _conn(db_path) as db:
        db.execute(f"UPDATE subscribers SET {set_sql} WHERE id=?", (*updates.values(), sub_id))
        db.commit()
        row = db.execute("SELECT * FROM subscribers WHERE id=?", (sub_id,)).fetchone()
        return dict(row) if row else None


def remove_subscriber(db_path: str, sub_id: int) -> bool:
    with _conn(db_path) as db:
        cur = db.execute("DELETE FROM subscribers WHERE id=?", (sub_id,))
        db.commit()
        return cur.rowcount > 0


def ensure_legacy_migrated(db_path: str, legacy_chat_id: str | None, tier: str = "instant") -> None:
    if not legacy_chat_id:
        return
    with _conn(db_path) as db:
        count = db.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
    if count == 0:
        add_subscriber(db_path, legacy_chat_id, name="Primary (migrated)", tier=tier)
