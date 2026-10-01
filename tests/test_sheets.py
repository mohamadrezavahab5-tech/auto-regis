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
