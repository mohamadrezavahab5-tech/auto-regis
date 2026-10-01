"""Where things live.

Program files (code, shipped config, helper scripts) sit in the install folder and are replaced by every update.
Everything that belongs to the person - database, saved logins, personal settings, logs, the embedded browser's session -
lives in their own Windows profile, so an update or reinstall never touches it and two people on one PC never share a login:
    frozen app:       %LOCALAPPDATA%\\AutoReview\\
    source checkout:  <project>\\data\\            (or AUTOREVIEW_HOME, which the tests point at a temp folder)"""
import os
import sys
from pathlib import Path

APP_NAME = "AutoReview"
APP_ID = "SnappPay.AutoReview"


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """Files packed inside the build (PyInstaller unpacks them under _internal / _MEIPASS)."""
    return Path(getattr(sys, "_MEIPASS", app_root()))


def _shipped(name: str) -> Path:
    for base in (app_root(), bundle_dir()):
        if (base / name).is_dir():
            return base / name
    return app_root() / name


def config_dir() -> Path:
    return _shipped("config")


def scripts_dir() -> Path:
    return _shipped("scripts")


def user_dir() -> Path:
    env = os.environ.get("AUTOREVIEW_HOME")
    if env:
        d = Path(env)
    elif getattr(sys, "frozen", False):
        d = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / APP_NAME
    else:
        d = app_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sub(name: str) -> Path:
    d = user_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    """Database, saved logins and downloaded exports."""
    return user_dir()


def logs_dir() -> Path:
    return _sub("logs")


def exports_dir() -> Path:
    return _sub("exports")


def web_dir() -> Path:
    """Storage of the embedded browser profile (NBO session)."""
    return _sub("web")
