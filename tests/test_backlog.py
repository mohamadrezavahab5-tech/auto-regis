from autoreview.backlog import batches, jalali_key, select

CFG = {"statuses": ["PENDING"], "optional_statuses": ["COMMERCIAL_IN_PROGRESS"], "ownership": "INDIVIDUAL",
       "online_only": True, "order": "newest_first"}


def row(smr, status="PENDING", date="۱۴۰۵/۰۷/۰۸", instore="false", own="INDIVIDUAL", online="true"):
    return dict(smr=smr, status=status, created_at=date, has_instore=instore, ownership=own, has_online=online)


def test_persian_dates_sort_numerically():
    assert jalali_key("۱۴۰۵/۰۷/۰۸") == 14050708
    assert jalali_key("1405/6/2") == 14050602
    assert jalali_key("") == 0


def test_select_keeps_only_pending_online_individual_and_orders_newest_first():
    rows = [row("A", date="۱۴۰۵/۰۶/۰۲"), row("B", date="۱۴۰۵/۰۷/۰۸"), row("C", status="CANCELLED"), row("D", instore="true"),
            row("E", own="LEGAL"), row("F", date="۱۴۰۵/۰۷/۰۸")]
    picked, why = select(rows, CFG)
    assert [r["smr"] for r in picked] == ["B", "F", "A"]      # stable inside the same day
    assert why == {"status": 1, "also instore (Online-Insta flow)": 1, "ownership": 1}


def test_commercial_in_progress_only_when_asked():
    rows = [row("A"), row("B", status="COMMERCIAL_IN_PROGRESS")]
    assert [r["smr"] for r in select(rows, CFG)[0]] == ["A"]
    assert {r["smr"] for r in select(rows, CFG, include_optional=True)[0]} == {"A", "B"}


def test_batches_split_evenly():
    assert [len(b) for b in batches(list(range(10)), 4)] == [4, 4, 2]
