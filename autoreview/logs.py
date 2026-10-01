"""Application log: a rotating file in the person's profile + an in-memory ring the Logs page reads live.

Never log a password, a token or a cookie. Log what happened and the numbers (rows, seconds, HTTP status)."""
import collections
import logging
import logging.handlers
import threading
from datetime import datetime

from .paths import logs_dir

ROOT = "autoreview"
RING = collections.deque(maxlen=5000)
_listeners = []
_lock = threading.Lock()
_ready = False


class _RingHandler(logging.Handler):
    def emit(self, record):
        try:
            item = {
                "ts": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "source": record.name.split(".", 1)[1] if "." in record.name else record.name,
                "message": record.getMessage() + (("\n" + self.formatter.formatException(record.exc_info)) if record.exc_info and self.formatter else ""),
            }
        except Exception:                      # a broken log call must never break the app
            return
        RING.append(item)
        for fn in list(_listeners):
            try:
                fn(item)
            except Exception:
                pass


def setup(level=logging.INFO) -> None:
    global _ready
    with _lock:
        if _ready:
            return
        root = logging.getLogger(ROOT)
        root.setLevel(level)
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        fh = logging.handlers.RotatingFileHandler(logs_dir() / "autoreview.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        ring = _RingHandler()
        ring.setFormatter(fmt)
        root.addHandler(fh)
        root.addHandler(ring)
        root.propagate = False
        _ready = True


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT}.{name}")


def subscribe(fn) -> None:
    _listeners.append(fn)


def unsubscribe(fn) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def log_file():
    return logs_dir() / "autoreview.log"
