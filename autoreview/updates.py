"""In-app updates without a server: the owner uploads a release to Drive and publishes its link in the 'Updates' tab of
his own sheet (version, URL, SHA-256, notes). Every app checks it (the owner directly, colleagues through the
Google-hosted service), downloads the setup, refuses it unless the SHA-256 matches exactly, and runs it silently over
the current install - the person's data and settings live elsewhere and are never touched.

The SHA-256 is the trust anchor: a link that was swapped, a Drive warning page or a broken download can never be run."""
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from . import logs, sheets, workspace
from .version import __version__

log = logs.get("update")
MAX_BYTES = 800 * 1024 * 1024
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class UpdateError(RuntimeError):
    pass


def parse(version) -> tuple:
    v = str(version or "").strip()
    if not _VERSION.match(v):
        raise ValueError(f"نسخه‌ی نامعتبر: {v!r}")
    return tuple(int(x) for x in v.split("."))


def is_newer(candidate, current=__version__) -> bool:
    try:
        return parse(candidate) > parse(current)
    except ValueError:
        return False


def pick_latest(rows):
    """rows: [{version, url, sha256, notes, published_at}] -> the highest valid release, or None."""
    good = []
    for r in rows:
        try:
            parse(r.get("version"))
        except ValueError:
            continue
        if _SHA.match(str(r.get("sha256") or "").strip().lower()) and str(r.get("url") or "").startswith("https://"):
            good.append(dict(r, sha256=str(r["sha256"]).strip().lower(), version=str(r["version"]).strip()))
    return max(good, key=lambda r: parse(r["version"]), default=None)


def latest(cfg=None):
    """The newest published release (or None) - read from the owner's sheet, never from anywhere else."""
    cfg = cfg or sheets.load()
    if cfg.get("auth_mode") == "workspace":
        return pick_latest(workspace.call("release", cfg).get("releases") or [])
    if cfg.get("auth_mode") == "service_account":
        from .google_sheet import Client
        with Client(cfg["own_sheet_id"]) as google:
            return pick_latest(google.releases())
    return None


def direct_url(link: str) -> str:
    """A Google Drive share link -> its direct download address (large files skip Drive's 'cannot scan' page)."""
    link = str(link or "").strip()
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:[^#]*&)?id=)([A-Za-z0-9_-]{20,})", link)
    if m:
        return f"https://drive.usercontent.google.com/download?id={m.group(1)}&export=download&confirm=t"
    if not link.startswith("https://"):
        raise UpdateError("لینک دانلود باید https باشد")
    return link


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(release, progress=None, client=None, folder=None) -> Path:
    """Streams the setup to a temp file; -> its path only when the SHA-256 matches the published one."""
    url = direct_url(release["url"])
    folder = Path(folder or tempfile.gettempdir())
    target = folder / f"AutoReview-Setup-{release['version']}.exe"
    partial = target.with_suffix(".part")
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0), follow_redirects=True)
    h, done = hashlib.sha256(), 0
    try:
        with client.stream("GET", url) as r:
            if r.status_code != 200:
                raise UpdateError(f"دانلود نشد (HTTP {r.status_code})")
            if "text/html" in r.headers.get("content-type", ""):
                raise UpdateError("لینک به صفحه‌ی وب می‌رسد نه خود فایل؛ در Drive اشتراک را «Anyone with the link» بگذار")
            total = int(r.headers.get("content-length") or 0)
            with open(partial, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    done += len(chunk)
                    if done > MAX_BYTES:
                        raise UpdateError("فایل از اندازه‌ی مجاز بزرگ‌تر است")
                    h.update(chunk)
                    f.write(chunk)
                    if progress:
                        progress((done, total))
    except httpx.HTTPError as e:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"اتصال دانلود قطع شد ({type(e).__name__})") from e
    except Exception:
        partial.unlink(missing_ok=True)                 # a half file is never left behind to be mistaken for a setup
        raise
    finally:
        if own:
            client.close()
    if h.hexdigest() != release["sha256"]:
        partial.unlink(missing_ok=True)
        raise UpdateError("فایل دانلودشده با امضای (SHA-256) منتشرشده یکی نیست؛ اجرا نشد")
    partial.replace(target)
    log.info("update %s downloaded and verified (%d bytes)", release["version"], done)
    return target


def can_self_update() -> bool:
    return bool(getattr(sys, "frozen", False))


def install(setup_path, install_dir, pid):
    """Runs the verified setup silently over this install; it waits for this app (pid) to exit, then reopens it."""
    if not can_self_update():
        raise UpdateError("نسخه‌ی در حال اجرا از روی کد است، نه نصب‌شده")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(setup_path), "--silent", "--dir", str(install_dir), "--wait-pid", str(pid)],
                     creationflags=flags, close_fds=True)
    log.info("update installer started for %s", install_dir)
