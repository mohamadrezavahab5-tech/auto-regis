"""The NBO screen flow, run in a real browser engine against a local fake of NBO's registration pages (same buttons, dialog
and reason picker as the team's old script expected). Real NBO is never touched by tests."""
import functools
import http.server
import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
if "--disable-background-timer-throttling" not in _flags.split():
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (_flags + " --disable-background-timer-throttling").strip()
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineProfile  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autoreview.app.nbo_actor import NboActor  # noqa: E402
from autoreview.app.web import _chrome_compatible_user_agent  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_nbo"
KEEP = []          # Qt objects must die on the window thread, never in a garbage-collection run on the server's thread


def test_nbo_user_agent_keeps_chromium_version_without_qt_marker():
    user_agent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 QtWebEngine/6.11.0")
    normalized = _chrome_compatible_user_agent(user_agent)
    assert "Chrome/134.0.0.0" in normalized
    assert "QtWebEngine" not in normalized


@pytest.fixture(scope="module")
def server():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(FAKE)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def actor(server):
    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile(app)                    # off the record: every test starts unassigned (kept alive by app)
    a = NboActor(list_url=server + "/list.html", profile=profile)
    KEEP.append((a, profile))
    yield a, profile


def run(actor, *args, **kw):
    out, loop = {}, QEventLoop()
    actor.run(*args, lambda r: (out.update(r), loop.quit()), **kw)
    QTimer.singleShot(180_000, loop.quit)
    loop.exec()
    return out


def stored(actor, key):
    out, loop = {}, QEventLoop()
    actor.page.runJavaScript(f"localStorage.getItem({key!r})", 0, lambda v: (out.update(v=v), loop.quit()))
    loop.exec()
    return out["v"]


def test_approve_goes_through_assign_and_change_status(actor):
    a, _ = actor
    res = run(a, "SMR-1001", "APPROVE", "")
    assert res["ok"] and res["sent"], res
    assert stored(a, "result") == "APPROVE|"


def test_edit_picks_the_exact_nbo_reason(actor):
    a, _ = actor
    res = run(a, "SMR-1001", "EDIT", "سایت‌مپ وجود ندارد")
    assert res["ok"] and res["sent"], res
    assert stored(a, "result") == "EDIT NEEDED|سایت‌مپ وجود ندارد"


def test_reason_readiness_is_polled_without_fixed_waits(actor):
    a, _ = actor
    a.list_url = a.list_url + "?preassigned=1"
    started = time.monotonic()
    res = run(a, "SMR-1001", "EDIT", "سایت‌مپ وجود ندارد")
    elapsed = time.monotonic() - started
    assert res["ok"] and res["sent"], res
    assert stored(a, "result") == "EDIT NEEDED|سایت‌مپ وجود ندارد"
    assert elapsed < 6, f"delayed fake NBO readiness took {elapsed:.2f}s"


def test_a_reason_nbo_does_not_offer_stops_before_anything_is_sent(actor):
    a, _ = actor
    res = run(a, "SMR-1001", "CANCEL", "وجود ندارد")
    assert not res["ok"] and res["error"] == "reason_not_selected", res
    assert not stored(a, "result")


def test_rehearsal_never_assigns_or_submits(actor):
    a, _ = actor
    res = run(a, "SMR-1001", "APPROVE", "", rehearsal=True)
    # Change Status stays disabled without Assign to me: the rehearsal stops there, reports the Assign button is ready
    assert res["ok"] and res["rehearsed"] and res["error"] == "needs_assign" and "Assign to me" in res["message"], res
    assert not stored(a, "assigned") and not stored(a, "result")


def test_unknown_request_is_never_acted_on(actor):
    a, _ = actor
    res = run(a, "SMR-9999", "APPROVE", "")
    assert not res["ok"] and res["error"] == "not_found", res
