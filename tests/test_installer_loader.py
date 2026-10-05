import os
from pathlib import Path
import sys

from installer.freeze import clean_path


def test_build_sanitizes_path_inside_python(monkeypatch):
    monkeypatch.setenv('PATH', r'C:\unrelated\poppler\bin;C:\other-tools')
    monkeypatch.setenv('SystemRoot', r'C:\Windows')
    clean_path()
    parts = os.environ['PATH'].split(os.pathsep)
    assert parts == [str(Path(sys.executable).parent), str(Path(sys.executable).parent / 'Scripts'),
                     r'C:\Windows\System32', r'C:\Windows']
    assert not any('poppler' in part.lower() for part in parts)


def test_installer_self_test_renders_without_installing(monkeypatch, tmp_path):
    import json
    from installer import setup_app
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    proof = tmp_path / 'proof.json'
    payload = tmp_path / 'payload.zip'
    payload.write_bytes(b'test')
    monkeypatch.setattr(sys, 'argv', ['setup', '--self-test', str(proof)])
    monkeypatch.setattr(setup_app, 'payload_path', lambda: payload)
    monkeypatch.setattr(setup_app, 'install', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not install')))
    monkeypatch.setattr(setup_app.winsetup, 'close_app', lambda: (_ for _ in ()).throw(AssertionError('must not close app')))
    assert setup_app.main() == 0
    assert json.loads(proof.read_text())['qt_rendered'] is True
