import json

import httpx
import pytest

from autoreview import sheets

URL = "https://script.google.com/macros/s/AKfycbx" + "a" * 40 + "/exec"


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_each_install_gets_its_own_secret_and_it_is_written_into_the_script():
    cfg = sheets.load()
    assert len(cfg["secret"]) >= 30 and sheets.load()["secret"] == cfg["secret"]        # stable once created
    code = sheets.script_code(cfg)
    assert f"const SECRET = '{cfg['secret']}';" in code and "__SECRET__" not in code


def test_only_real_web_app_links_are_accepted():
    assert sheets.valid_webapp_url(URL)
    for bad in ("", "https://docs.google.com/spreadsheets/d/x/edit", "http://script.google.com/macros/s/abc/exec", URL + "?x=1"):
        assert not sheets.valid_webapp_url(bad)


def test_ping_and_send_follow_google_redirect_and_parse_json():
    sent = []

    def handler(request):
        if request.url.host == "script.google.com":
            sent.append(json.loads(request.content))
            return httpx.Response(302, headers={"location": "https://script.googleusercontent.com/macros/echo?user_content_key=x"})
        body = sent[-1]
        if body["action"] == "ping":
            return httpx.Response(200, json={"ok": True, "sheet": "AutoReview - Results"})
        return httpx.Response(200, json={"ok": True, "appended": len(body["rows"])})
    cfg = sheets.load()
    cfg["webapp_url"] = URL
    assert sheets.ping(cfg, client(handler))["sheet"] == "AutoReview - Results"
    res = [{"smr": "SMR-1", "site": "a.ir", "category": "c", "created_at": "1405/07/08", "action": "EDIT", "reason_codes": ["MISSING_LICENSE"],
            "notes": [], "duration_ms": 1500}]
    assert sheets.send_run("run1", res, "علی", cfg, client(handler))["appended"] == 1
    row = sent[-1]["rows"][0]
    assert row["action_fa"] == "نیاز به اصلاح" and row["reasons_fa"].startswith("نداشتن") and row["seconds"] == 1.5
    assert sent[-1]["secret"] == cfg["secret"] and "run1" in sheets.load()["sent_runs"]


def test_a_web_app_that_asks_for_google_sign_in_gets_a_clear_message():
    def handler(request):
        return httpx.Response(200, text="<!DOCTYPE html><html><body>Sign in</body></html>")
    cfg = sheets.load()
    cfg["webapp_url"] = URL
    with pytest.raises(sheets.SheetError, match="Anyone"):
        sheets.ping(cfg, client(handler))


def test_a_wrong_secret_is_reported():
    cfg = sheets.load()
    cfg["webapp_url"] = URL
    with pytest.raises(sheets.SheetError, match="کلید"):
        sheets.ping(cfg, client(lambda r: httpx.Response(200, json={"ok": False, "error": "forbidden"})))


def test_script_carries_only_the_owners_sheet():
    cfg = sheets.load()
    code = sheets.script_code(cfg)
    assert cfg['own_sheet_id'] in code
    assert 'ONLINE_INSTORE_ID' not in code
    cfg['own_sheet_id'] = "x'); DriveApp.getRootFolder(); ('"
    with pytest.raises((ValueError, sheets.SheetError)):
        sheets.script_code(cfg)


def test_the_teams_shared_sheets_are_unreachable_from_the_app():
    """Owner rule 2026-10-01: only the sheet he made. No code path opens another spreadsheet."""
    import pathlib
    root = pathlib.Path(sheets.__file__).resolve().parent.parent
    sources = [p.read_text(encoding="utf-8") for p in (root / "autoreview").rglob("*.py")]
    sources += [p.read_text(encoding="utf-8") for p in (root / "scripts").glob("*.gs")]
    for text in sources:
        assert "1FCt7WfmuQ5zy_jwafsLe2a7xkbKouS28wep1d94lF3s" not in text          # shared Online-Instore
        assert "1i5c0fSKf1bTzM4hikIS9ZN1buTUDnjtrnMNvNi43S2Y" not in text          # Main-Data
        assert "openById" not in text
    assert not any(hasattr(sheets, n) for n in ("oi_write", "oi_describe", "oi_rows"))
    assert "oi" not in sheets.load()


def test_saving_one_settings_page_keeps_what_other_pages_saved():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from autoreview import settings
    from autoreview.app.pages.settings_page import SettingsPage
    QApplication.instance() or QApplication([])
    settings.save_user({"rules": {"appearance.theme": "dark", "execution.auto_actions": ["APPROVE", "EDIT"]}})
    page = SettingsPage(type("S", (), {"refresh_workflow": lambda self: None,
                                        "data_changed": type("Sig", (), {"emit": lambda self: None})()})(), None)
    page.on_show()
    page.save()
    rules = settings.load_user()["rules"]
    assert rules["appearance.theme"] == "dark" and rules["execution.auto_actions"] == ["APPROVE", "EDIT"]
