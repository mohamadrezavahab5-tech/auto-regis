import json
import pytest
from autoreview import sheets, workspace


def test_legacy_configuration_migrates_without_touching_personal_data():
    from autoreview.paths import user_dir
    files = ['autoreview.db', 'crm.dpapi', 'personal.json']
    for name in files: (user_dir() / name).write_bytes(b'preserved')
    legacy = dict(auth_mode='workspace', workspace_url='https://legacy.invalid/exec',
                  app_key='obsolete', workflow_sync=False, sent_runs=['run1'])
    sheets.config_file().write_text(json.dumps(legacy), encoding='utf-8')
    cfg = sheets.load()
    assert cfg['workspace_backend'] == 'google_sheets' and cfg['workflow_sync']
    assert cfg['workspace_url'] == legacy['workspace_url']
    assert cfg['sent_runs'] == ['run1']
    assert 'app_key' not in json.loads(sheets.config_file().read_text())
    assert all((user_dir()/name).read_bytes() == b'preserved' for name in files)


def test_ping_uses_direct_health(monkeypatch):
    monkeypatch.setattr(workspace, 'health', lambda cfg: {'ok':True, 'backend':'google_sheets'})
    assert sheets.ping()['backend'] == 'google_sheets'


def test_no_legacy_transport_or_script_generation_remains():
    assert not any(hasattr(sheets, n) for n in ('_post', 'script_code', 'save_workspace_url'))


def test_the_teams_shared_sheets_are_unreachable_from_the_app():
    """Owner rule 2026-10-01: only the sheet he made. No code path opens another spreadsheet."""
    import pathlib
    root = pathlib.Path(sheets.__file__).resolve().parent.parent
    sources = [p.read_text(encoding="utf-8") for p in (root / "autoreview").rglob("*.py")]
    sources += [p.read_text(encoding="utf-8") for p in (root / "scripts").glob("*.gs")]
    for text in sources:
        assert "1FCt7WfmuQ5zy_jwafsLe2a7xkbKouS28wep1d94lF3s" not in text          # shared Online-Instore
        assert "1i5c0fSKf1bTzM4hikIS9ZN1buTUDnjtrnMNvNi43S2Y" not in text          # Main-Data
        for argument in __import__('re').findall(r'openById\(([^)]*)\)', text):
            assert argument == 'EXPECTED_SHEET_ID'
    assert not any(hasattr(sheets, n) for n in ("oi_write", "oi_describe", "oi_rows"))
    assert "oi" not in sheets.load()


def test_saving_one_settings_page_keeps_what_other_pages_saved():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from autoreview import settings
    from autoreview.app.pages.settings_page import SettingsPage
    QApplication.instance() or QApplication([])
    settings.save_user({"rules": {"appearance.theme": "dark", "execution.auto_actions": ["APPROVE", "EDIT"]}})
    page = SettingsPage(type("S", (), {"refresh_workflow": lambda self: None,
                                        "data_changed": type("Sig", (), {"emit": lambda self: None})()})(), None)
    page.on_show()
    page.save()
    rules = settings.load_user()["rules"]
    assert rules["appearance.theme"] == "dark" and rules["execution.auto_actions"] == ["APPROVE", "EDIT"]
