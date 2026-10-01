from datetime import datetime, timedelta, timezone

from autoreview import jalali, sources, workboard


def test_gregorian_to_jalali_known_dates():
    assert jalali.g2j(2026, 10, 1) == (1405, 7, 9)
    assert jalali.g2j(2026, 3, 21) == (1405, 1, 1)
    assert jalali.g2j(2026, 3, 20) == (1404, 12, 29)
    assert jalali.g2j(2025, 3, 21) == (1404, 1, 1)
    assert jalali.g2j(2024, 3, 20) == (1403, 1, 1)                    # leap-year Nowruz
    assert jalali.jdate(datetime(2026, 10, 1, 12), persian_digits=False) == "1405/07/09"
    assert jalali.long_date(datetime(2026, 10, 1, 12)) == "پنجشنبه ۹ مهر ۱۴۰۵"


def test_relative_age_in_persian():
    now = datetime(2026, 10, 1, 15, 0).astimezone()
    assert jalali.ago(now - timedelta(seconds=20), now) == "همین حالا"
    assert jalali.ago(now - timedelta(minutes=5), now) == "۵ دقیقه پیش"
    assert jalali.ago(now - timedelta(hours=3), now) == "۳ ساعت پیش"
    assert jalali.ago(now - timedelta(days=1), now) == "دیروز"
    assert jalali.ago(None) == "—"
    utc = datetime(2026, 10, 1, 11, 25, tzinfo=timezone.utc)          # stored UTC is shown in local time
    assert jalali.jdatetime(utc, persian_digits=False).startswith("1405/07/09")


def test_snapshot_round_trip_and_staleness():
    rules = {"backlog": {"statuses": ["PENDING"], "optional_statuses": ["COMMERCIAL_IN_PROGRESS"], "ownership": "INDIVIDUAL",
                         "online_only": True, "order": "newest_first"},
             "approved_statuses": {"nbo": ["COMPLETED"]}}
    rows = [{"smr": "A", "status": "PENDING", "ownership": "INDIVIDUAL", "has_online": "true", "has_instore": "false", "created_at": "1405/07/08", "site": "a.ir"},
            {"smr": "B", "status": "PENDING", "ownership": "INDIVIDUAL", "has_online": "true", "has_instore": "true", "created_at": "1405/07/08", "site": "b.ir"},
            {"smr": "C", "status": "COMPLETED", "ownership": "INDIVIDUAL", "has_online": "true", "has_instore": "false", "created_at": "1405/01/01", "site": "c.ir"}]
    snap = sources.build_nbo(rows, rules, "file:x.xlsx")
    assert [r["smr"] for r in snap["backlog"]] == ["A"] and [r["smr"] for r in snap["both_channels"]] == ["B"]
    assert snap["approved"] == [{"id": "C", "site": "c.ir"}] and snap["status_counts"] == {"PENDING": 2, "COMPLETED": 1}
    sources.save_nbo(snap)
    assert sources.load_nbo()["backlog"][0]["smr"] == "A" and not sources.is_stale(sources.load_nbo())
    old = dict(snap, loaded_at=(datetime.now(timezone.utc) - timedelta(hours=13)).isoformat())
    assert sources.is_stale(old) and sources.is_stale(None)
    crm = sources.save_crm([{"smr": "MRG-1", "status": "x", "site": "d.ir"}], "auto", total=1)
    assert sources.load_crm()["approved"] == [{"id": "MRG-1", "site": "d.ir"}] and crm["rows"] == 1


def test_workboard_counts_done_and_left():
    backlog = [{"smr": s} for s in ("A", "B", "C", "D", "E")]
    latest = {"A": "APPROVE", "B": "MANUAL", "C": "MANUAL", "D": "CANCEL"}
    s = workboard.summarize(backlog, latest, manual_done={"C"})
    assert s["total"] == 5 and s["reviewed"] == 4 and s["left"] == 1 and s["percent"] == 80.0
    assert s["counts"]["MANUAL_OPEN"] == 1 and s["counts"]["MANUAL_DONE"] == 1 and s["needs_person"] == 1


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
