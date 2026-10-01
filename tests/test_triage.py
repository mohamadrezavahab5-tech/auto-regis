import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from autoreview import workflow  # noqa: E402
from autoreview.app.pages.triage import TriagePage  # noqa: E402
from autoreview.app.session import Session  # noqa: E402

KEEP = []


def make():
    QApplication.instance() or QApplication([])
    s = Session({"username": "SNAPP\tester", "display_name": "tester"})
    db = s.db()
    try:
        rows = [dict(smr=f"SMR-{i}", site=f"s{i}.example", status="PENDING", has_online="true", has_instore="false") for i in range(3)]
        workflow.refresh(db, rows, {r["smr"] for r in rows})
        for r in rows:
            workflow.suggest(db, r["smr"], dict(action="MANUAL", reason_codes=[], notes=["could not determine: add to cart"]))
    finally:
        db.close()
    page = TriagePage(s, None)
    KEEP.append((s, page))
    page.on_show()
    return s, page


def test_one_key_decides_and_the_next_request_comes_up():
    s, page = make()
    assert len(page.items) == 3
    first = page.items[0]["smr"]
    page._key("APPROVE")
    db = s.db()
    try:
        assert workflow.get(db, first)["state"] == "READY"
    finally:
        db.close()
    assert len(page.items) == 2 and page.items[page.index]["smr"] != first


def test_edit_needs_an_nbo_reason_from_the_list():
    s, page = make()
    smr = page.items[0]["smr"]
    page.ask_reason("EDIT")
    page.reason.hidePopup()
    code_index = next(i for i in range(page.reason.count()) if page.reason.itemData(i))
    code = page.reason.itemData(code_index)
    page._reason_picked(code_index)
    db = s.db()
    try:
        case = workflow.get(db, smr)
    finally:
        db.close()
    assert case["state"] == "EDIT" and case["online"]["action"] == "EDIT" and case["online"]["reason"] == code


def test_skip_and_back_move_without_deciding():
    _s, page = make()
    page._key("SKIP")
    assert page.index == 1
    page._key("BACK")
    assert page.index == 0 and len(page.items) == 3
