"""Alert gating: per-event flags + quiet hours (server-local time)."""
from datetime import datetime

_DEFAULT_FLAG = {"listing": True, "notice": True, "error": False}


def _in_quiet(qh: dict, now: datetime) -> bool:
    if not qh or not qh.get("enabled"):
        return False
    try:
        sh, sm = (int(x) for x in str(qh["start"]).split(":"))
        eh, em = (int(x) for x in str(qh["end"]).split(":"))
    except (KeyError, ValueError, TypeError):
        return False
    cur = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end          # overnight wrap


def alert_allowed(config: dict, kind: str, now: datetime | None = None) -> bool:
    if not config.get(f"alert_on_{kind}", _DEFAULT_FLAG.get(kind, False)):
        return False
    return not _in_quiet(config.get("quiet_hours") or {}, now or datetime.now())
