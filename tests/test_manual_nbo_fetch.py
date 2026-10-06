from autoreview.app.pages.review import export_nbo_statuses


class FakeClient:
    def __init__(self, answers):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.calls = []
        self.reloads = 0

    def export(self, statuses, callback):
        status = statuses[0]
        self.calls.append(status)
        data, err = self.answers[status].pop(0)
        callback(data, err)

    def reload(self):
        self.reloads += 1


def immediate(_ms, callback):
    callback()


def run(client, statuses):
    out = []
    export_nbo_statuses(client, statuses, out.append, schedule=immediate, gap_ms=0)
    assert len(out) == 1
    return out[0]


def test_manual_nbo_export_retries_504_once_then_continues():
    c = FakeClient({
        "PENDING": [(None, "http_504"), (b"pending", None)],
        "COMMERCIAL_IN_PROGRESS": [(b"inprogress", None)],
    })
    r = run(c, ["PENDING", "COMMERCIAL_IN_PROGRESS"])
    assert r["fatal"] is None
    assert r["refused"] == []
    assert r["done"] == ["PENDING", "COMMERCIAL_IN_PROGRESS"]
    assert r["parts"] == [b"pending", b"inprogress"]
    assert c.calls == ["PENDING", "PENDING", "COMMERCIAL_IN_PROGRESS"]


def test_manual_nbo_export_keeps_successful_statuses_when_one_status_fails():
    c = FakeClient({
        "PENDING": [(b"pending", None)],
        "COMMERCIAL_IN_PROGRESS": [(None, "http_403")],
        "COMMERCIAL_APPROVED": [(b"approved", None)],
    })
    r = run(c, ["PENDING", "COMMERCIAL_IN_PROGRESS", "COMMERCIAL_APPROVED"])
    assert r["fatal"] is None
    assert r["done"] == ["PENDING", "COMMERCIAL_APPROVED"]
    assert r["parts"] == [b"pending", b"approved"]
    assert r["refused"] == ["COMMERCIAL_IN_PROGRESS (http_403)"]


def test_manual_nbo_export_stops_cleanly_on_rate_limit():
    c = FakeClient({
        "PENDING": [(b"pending", None)],
        "COMMERCIAL_IN_PROGRESS": [(None, "http_429")],
        "COMMERCIAL_APPROVED": [(b"approved", None)],
    })
    r = run(c, ["PENDING", "COMMERCIAL_IN_PROGRESS", "COMMERCIAL_APPROVED"])
    assert r["fatal"] == "limited"
    assert r["done"] == ["PENDING"]
    assert r["parts"] == [b"pending"]
    assert "COMMERCIAL_IN_PROGRESS (http_429)" in r["refused"]
    assert c.calls == ["PENDING", "COMMERCIAL_IN_PROGRESS"]


def test_manual_nbo_export_stops_for_login():
    c = FakeClient({
        "PENDING": [(None, "login")],
        "COMMERCIAL_IN_PROGRESS": [(b"x", None)],
    })
    r = run(c, ["PENDING", "COMMERCIAL_IN_PROGRESS"])
    assert r["fatal"] == "login"
    assert r["parts"] == []
    assert c.calls == ["PENDING"]
