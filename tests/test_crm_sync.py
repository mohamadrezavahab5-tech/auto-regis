import subprocess

import pytest

from autoreview import crm_sync as c

MAP = {"entity_set": "regs", "fields": {"smr": "code", "status": "st", "site": "web"}, "status_labels_are_formatted_values": True, "filter": None}


class Proc:
    def __init__(self, code=0, out=b"", err=b""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_fetch_rows_follows_paging_and_uses_formatted_status():
    pages = {
        "regs?$select=code,st,web": {"value": [{"code": "MRG-1", "st": 5, "st" + c.FORMATTED: "درخواست تایید شده است", "web": "a.ir"}], "@odata.nextLink": "NEXT"},
        "NEXT": {"value": [{"code": "MRG-2", "st": 1, "st" + c.FORMATTED: "پیشنویس قرارداد", "web": ""}, {"code": "", "web": "x"}]},
    }
    rows = c.fetch_rows(MAP, runner=lambda p: pages[p])
    assert rows == [{"smr": "MRG-1", "status": "درخواست تایید شده است", "site": "a.ir"}, {"smr": "MRG-2", "status": "پیشنویس قرارداد", "site": ""}]


def test_shipped_filter_is_url_encoded_and_reads_only_approved_statuses():
    seen = []
    c.fetch_rows(runner=lambda p: seen.append(p) or {"value": []})
    assert seen[0].startswith("new_merchantregistrations?$select=new_caseid,new_merchantstatus,new_urlsite&$filter=")
    assert " " not in seen[0] and "100000005" in seen[0]


def test_unfilled_mapping_refuses_instead_of_guessing():
    with pytest.raises(RuntimeError):
        c.fetch_rows({"entity_set": None, "fields": {"smr": None, "status": None, "site": None}}, runner=lambda p: {})


def test_only_get_requests_exist():
    import inspect
    src = inspect.getsource(c) + c._script("crm-get.ps1").read_text(encoding="utf-8")
    for verb in ("POST", "PATCH", "DELETE", "PUT"):
        assert f"-Method {verb}" not in src
    assert "-Method GET" in src


def test_username_gets_the_domain_unless_it_already_has_one():
    assert c.normalize_username("ali") == "SNAPP\\ali"
    assert c.normalize_username(" ali ") == "SNAPP\\ali"
    assert c.normalize_username("SNAPP\\ali") == "SNAPP\\ali"
    assert c.normalize_username("ali@snapp.ir") == "ali@snapp.ir"


def test_a_401_removes_the_stored_login_and_is_reported_clearly(monkeypatch):
    c.cred_file().write_text('<S N="UserName">SNAPP\\ali</S>', encoding="utf-8")
    monkeypatch.setattr(c, "_ps", lambda *a, **k: Proc(1, b"", b"HTTP 401 The remote server returned an error: (401) Unauthorized."))
    with pytest.raises(c.CrmAuthError):
        c.whoami()
    assert not c.cred_file().exists()                      # a refused password is never sent again


def test_a_login_saved_without_the_domain_is_dropped():
    c.cred_file().write_text('<Objs><S N="UserName">ali</S><SS N="Password">01000000d08c</SS></Objs>', encoding="utf-8")
    assert c.have_credentials() is False and not c.cred_file().exists()
    c.cred_file().write_text('<Objs><S N="UserName">SNAPP\\ali</S></Objs>', encoding="utf-8")
    assert c.have_credentials() is True and c.stored_username() == "SNAPP\\ali"


def test_login_reads_the_display_name_and_cleans_up_on_failure(monkeypatch):
    monkeypatch.setattr(c, "save_credentials", lambda u, p: c.cred_file().write_text(f'<S N="UserName">{c.normalize_username(u)}</S>', encoding="utf-8"))
    pages = {"WhoAmI": {"UserId": "u-1"}, "systemusers(u-1)?$select=fullname": {"fullname": "علی رضایی"}}
    prof = c.login("ali", "pw", runner=lambda p: pages[p])
    assert prof == {"username": "SNAPP\\ali", "display_name": "علی رضایی", "user_id": "u-1"}

    def refuse(p):
        raise c.CrmAuthError("no")
    with pytest.raises(c.CrmAuthError):
        c.login("ali", "bad", runner=refuse)
    assert not c.cred_file().exists()


def test_check_stops_at_the_first_failing_step():
    steps = c.check(reachable=False)
    assert len(steps) == 1 and steps[0][1] is False
    steps = c.check(reachable=True)
    assert [s[1] for s in steps] == [True, False]                      # reachable, but not signed in


def test_save_credentials_round_trip_through_powershell():
    """The real helper: the typed password must come back exactly (special characters, non-ASCII)."""
    pw = "P@ss w0rd!\"'#$%&x-رمز"
    c.save_credentials("test.user", pw)
    script = f"$c = Import-Clixml -Path '{c.cred_file()}'; $p = $c.GetNetworkCredential().Password; " \
             "[Console]::OpenStandardOutput().Write([Text.Encoding]::UTF8.GetBytes($c.UserName + '|' + $p), 0, [Text.Encoding]::UTF8.GetByteCount($c.UserName + '|' + $p))"
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       env=c.powershell_environment())
    assert r.stdout.decode("utf-8") == f"SNAPP\\test.user|{pw}"
    assert c.stored_username() == "SNAPP\\test.user"
