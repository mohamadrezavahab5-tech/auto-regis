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
