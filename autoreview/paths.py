"""Where things live. Source checkout: the project folder. Packaged exe: the folder next to the exe (config/ and data/ stay editable there)."""
import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    external = app_root() / "config"
    if external.is_dir():
        return external
    bundled = Path(getattr(sys, "_MEIPASS", app_root())) / "config"
    return bundled


def data_dir() -> Path:
    d = app_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d
