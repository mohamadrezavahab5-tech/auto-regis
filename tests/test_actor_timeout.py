import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from autoreview.app.nbo_actor import NboActor


def test_stalled_javascript_finishes_once_and_is_uncertain_after_detail():
    QApplication.instance() or QApplication([])
    class Page:
        def __init__(self): self.callbacks = []; self.urls = []
        def runJavaScript(self, js, world, cb): self.callbacks.append(cb)
        def setUrl(self, url): self.urls.append(url.toString())
    owner = QObject()
    owner.page = Page()
    for detail, expected in [(False, 'timeout'), (True, 'sent_unconfirmed')]:
        out, loop = [], QEventLoop()
        def done(result): out.append(result); loop.quit()
        NboActor._script(owner, 'ignored', done, 25, detail=detail)
        QTimer.singleShot(1000, loop.quit)
        loop.exec()
        assert len(out) == 1 and out[0]['error'] == expected
        assert owner.page.urls[-1] == 'about:blank'
        owner.page.callbacks[-1](True)
        QApplication.processEvents()
        assert len(out) == 1


def test_detail_script_error_before_final_click_remains_systemic():
    QApplication.instance() or QApplication([])

    class Page:
        def __init__(self):
            self.callbacks = []
            self.urls = []
        def runJavaScript(self, js, world, cb):
            self.callbacks.append(cb)
        def setUrl(self, url):
            self.urls.append(url.toString())

    owner = QObject()
    owner.page = Page()
    out, loop = [], QEventLoop()
    NboActor._script(owner, 'ignored', lambda result: (out.append(result), loop.quit()), 1000, detail=True)
    owner.page.callbacks[0](True)

    def deliver():
        if len(owner.page.callbacks) > 1:
            owner.page.callbacks[-1]('{"state":"error","error":"script"}')
        else:
            QTimer.singleShot(10, deliver)

    QTimer.singleShot(100, deliver)
    QTimer.singleShot(1500, loop.quit)
    loop.exec()
    assert len(out) == 1 and out[0]['error'] == 'script'
    assert not owner.page.urls
