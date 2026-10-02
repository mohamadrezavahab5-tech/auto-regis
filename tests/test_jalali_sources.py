from datetime import datetime, timedelta, timezone

from autoreview import jalali, workboard


def test_gregorian_to_jalali_known_dates():
    assert jalali.g2j(2026, 10, 1) == (1405, 7, 9)
    assert jalali.g2j(2026, 3, 21) == (1405, 1, 1)
    assert jalali.g2j(2026, 3, 20) == (1404, 12, 29)
    assert jalali.g2j(2025, 3, 21) == (1404, 1, 1)
    assert jalali.g2j(2024, 3, 20) == (1403, 1, 1)                    # leap-year Nowruz
    assert jalali.jdate(datetime(2026, 10, 1, 12), persian_digits=False) == "1405/07/09"
    assert jalali.long_date(datetime(2026, 10, 1, 12)) == "پنجشنبه 9 مهر 1405"           # English digits (owner 2026-10-02)


def test_relative_age_in_persian():
    now = datetime(2026, 10, 1, 15, 0).astimezone()
    assert jalali.ago(now - timedelta(seconds=20), now) == "همین حالا"
    assert jalali.ago(now - timedelta(minutes=5), now) == "5 دقیقه پیش"
    assert jalali.ago(now - timedelta(hours=3), now) == "3 ساعت پیش"
    assert jalali.ago_en(now - timedelta(minutes=5), now) == "5m ago"
    assert jalali.ago(now - timedelta(days=1), now) == "دیروز"
    assert jalali.ago(None) == "—"
    utc = datetime(2026, 10, 1, 11, 25, tzinfo=timezone.utc)          # stored UTC is shown in local time
    assert jalali.jdatetime(utc, persian_digits=False).startswith("1405/07/09")


def test_workboard_counts_done_and_left():
    backlog = [{"smr": s} for s in ("A", "B", "C", "D", "E", "F")]
    latest = {"A": ("APPROVE", [], "t"), "B": ("MANUAL", [], "t"), "C": ("MANUAL", [], "t"), "D": ("CANCEL", ["WEBSITE_IS_INACTIVE"], "t"),
              "E": ("CANCEL", ["DUPLICATE_REQUEST"], "t")}
    s = workboard.summarize(backlog, latest, manual_done={"C"})
    assert s["total"] == 6 and s["reviewed"] == 5 and s["left"] == 1 and s["percent"] == 83.3
    assert s["counts"]["MANUAL_OPEN"] == 1 and s["counts"]["MANUAL_DONE"] == 1 and s["counts"]["DUPLICATE"] == 1 and s["counts"]["CANCEL"] == 1


def test_latest_decision_per_request_and_manual_done(isolated_profile):
    from autoreview import store
    from autoreview.rules import Decision
    db = store.connect(isolated_profile / "w.db")
    store.save_result(db, "r1", {"smr": "A"}, Decision("MANUAL"), {})
    store.save_result(db, "r1", {"smr": "B"}, Decision("EDIT", ["X"], ["CODE"]), {})
    db.execute("UPDATE results SET decided_at = '2026-01-01T00:00:00+00:00' WHERE run_id = 'r1'")
    db.commit()
    store.save_result(db, "r2", {"smr": "A"}, Decision("APPROVE"), {})                 # a later run changed A
    assert store.latest_decisions(db) == {"A": "APPROVE", "B": "EDIT"}
    store.set_manual_done(db, "Z", True, "علی")
    assert store.manual_done_set(db) == {"Z"}
    store.set_manual_done(db, "Z", False)
    assert store.manual_done_set(db) == set()


def test_latest_states_and_history(isolated_profile):
    from autoreview import store
    from autoreview.rules import Decision
    db = store.connect(isolated_profile / "h.db")
    store.save_result(db, "r1", {"smr": "A"}, Decision("MANUAL", notes=["could not determine: add to cart"]), {})
    db.execute("UPDATE results SET decided_at = '2026-01-01T00:00:00+00:00'")
    db.commit()
    store.set_manual_done(db, "A", True, "علی")
    store.save_result(db, "r2", {"smr": "A"}, Decision("CANCEL", ["DUPLICATE_REQUEST"], ["DUPLICATE_REQUEST"]), {})
    assert store.latest_states(db)["A"][:2] == ("CANCEL", ["DUPLICATE_REQUEST"])
    kinds = [e["kind"] for e in store.history(db, "A")]
    assert kinds[0] == "DECISION" and "MANUAL_DONE" in kinds and kinds.count("DECISION") == 2


def test_persian_digits_are_one_switch_away(monkeypatch):
    monkeypatch.setattr(jalali, "DIGITS", "persian")
    assert jalali.long_date(datetime(2026, 10, 1, 12)) == "پنجشنبه ۹ مهر ۱۴۰۵"
    monkeypatch.setattr(jalali, "DIGITS", "latin")
    assert jalali.fa_digits("۱۴۰۵") == "1405"
