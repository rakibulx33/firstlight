from datetime import datetime
from alerts import alert_allowed


def test_flags():
    assert alert_allowed({"alert_on_listing": True}, "listing") is True
    assert alert_allowed({"alert_on_listing": False}, "listing") is False
    assert alert_allowed({}, "error") is False          # error defaults off
    assert alert_allowed({"alert_on_notice": True}, "notice") is True


def test_quiet_hours_same_day():
    cfg = {"alert_on_listing": True, "quiet_hours": {"enabled": True, "start": "09:00", "end": "17:00"}}
    assert alert_allowed(cfg, "listing", now=datetime(2026, 6, 21, 12, 0)) is False
    assert alert_allowed(cfg, "listing", now=datetime(2026, 6, 21, 20, 0)) is True


def test_quiet_hours_overnight_wrap():
    cfg = {"alert_on_listing": True, "quiet_hours": {"enabled": True, "start": "23:00", "end": "07:00"}}
    assert alert_allowed(cfg, "listing", now=datetime(2026, 6, 21, 2, 0)) is False
    assert alert_allowed(cfg, "listing", now=datetime(2026, 6, 21, 23, 30)) is False
    assert alert_allowed(cfg, "listing", now=datetime(2026, 6, 21, 12, 0)) is True
