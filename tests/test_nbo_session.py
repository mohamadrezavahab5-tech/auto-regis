import inspect

from autoreview import nbo_session as n


def test_export_params_match_nbo_filter_format():
    assert n.export_params(["PENDING", "COMMERCIAL_IN_PROGRESS"]) == {"statuses[0]": "PENDING", "statuses[1]": "COMMERCIAL_IN_PROGRESS"}


def test_only_read_requests_and_no_status_change_in_this_module():
    src = inspect.getsource(n)
    assert "ctx.request.get(" in src
    for bad in ("request.put", "request.post", "request.delete", "change-status", ".fill(", "password"):
        assert bad not in src.split('"""', 2)[2], bad


def test_xlsx_signature_check():
    assert n.looks_like_xlsx(b"PK\x03\x04...") and not n.looks_like_xlsx(b'{"error":1}')
