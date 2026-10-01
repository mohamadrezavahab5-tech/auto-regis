"""Read-only connectivity check; never returns credentials or record contents."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from autoreview.app.web import NboClient

app = QApplication([])
client = NboClient()


def done(result):
    print(json.dumps({'state': result.get('state'), 'total_available': result.get('total') is not None}))
    app.quit()


QTimer.singleShot(70_000, lambda: done({'state': 'timeout'}))
client.probe(done)
app.exec()
