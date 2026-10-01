import pytest

from autoreview import crm_sync as c

MAP = {"entity_set": "regs", "fields": {"smr": "code", "status": "st", "site": "web"}, "status_labels_are_formatted_values": True, "filter": None}


def test_fetch_rows_follows_paging_and_uses_formatted_status():
    pages = {
        "regs?$select=code,st,web": {"value": [{"code": "MRG-1", "st": 5, "st" + c.FORMATTED: "تایید قرارداد", "web": "a.ir"}], "@odata.nextLink": "NEXT"},
        "NEXT": {"value": [{"code": "MRG-2", "st": 1, "st" + c.FORMATTED: "پیشنویس قرارداد", "web": ""}, {"code": "", "web": "x"}]},
    }
    rows = c.fetch_rows(MAP, runner=lambda p: pages[p])
    assert rows == [{"smr": "MRG-1", "status": "تایید قرارداد", "site": "a.ir"}, {"smr": "MRG-2", "status": "پیشنویس قرارداد", "site": ""}]


def test_unfilled_mapping_refuses_instead_of_guessing():
    with pytest.raises(RuntimeError):
        c.fetch_rows({"entity_set": None, "fields": {"smr": None, "status": None, "site": None}}, runner=lambda p: {})


def test_only_get_requests_exist():
    import inspect
    src = inspect.getsource(c) + open(str(c._script("crm-get.ps1")), encoding="utf-8").read()
    for verb in ("POST", "PATCH", "DELETE", "PUT"):
        assert f"-Method {verb}" not in src


def test_username_gets_the_domain_unless_it_already_has_one():
    assert c.normalize_username("ali") == "SNAPP\\ali"
    assert c.normalize_username(" ali ") == "SNAPP\\ali"
    assert c.normalize_username("SNAPP\\ali") == "SNAPP\\ali"
    assert c.normalize_username("ali@snapp.ir") == "ali@snapp.ir"


def test_a_401_removes_the_stored_login_and_is_reported_clearly(tmp_path, monkeypatch):
    class R:
        returncode, stdout, stderr = 1, "", "The remote server returned an error: (401) Unauthorized."
    cred = tmp_path / "cred.xml"; cred.write_text("x")
    monkeypatch.setattr(c, "cred_file", lambda: cred)
    monkeypatch.setattr(c, "_ps", lambda *a, **k: R())
    with pytest.raises(c.CrmAuthError):
        c.whoami()
    assert not cred.exists()                      # a refused password is never sent again
