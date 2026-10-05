"""Run PyInstaller with dependency search paths sanitized inside its own process.

Some launchers reconstruct PATH when starting Python, undoing a shell's changes.
In particular, Poppler's icuuc.dll cannot substitute for the Windows ICU ABI used
by Qt. Clean PATH here, before importing PyInstaller or running its analysis.
"""
import os
from pathlib import Path
import sys


def clean_path():
    python = Path(sys.executable).parent
    windows = Path(os.environ['SystemRoot'])
    os.environ['PATH'] = os.pathsep.join(map(str, (python, python / 'Scripts', windows / 'System32', windows)))


if __name__ == '__main__':
    clean_path()
    from PyInstaller.__main__ import run
    run(sys.argv[1:])
