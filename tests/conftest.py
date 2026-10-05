import pytest


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    """Every test gets its own empty profile folder: nothing ever reads or writes the real database, settings or logins."""
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("AUTOREVIEW_HOME", str(home))
    from autoreview import sheets, crm_sync
    monkeypatch.setattr(sheets, 'shared_config', lambda: {})
    monkeypatch.setattr(crm_sync, '_authenticated_identity', None)
    return home


@pytest.fixture(autouse=True)
def _no_google_backoff(monkeypatch):
    from autoreview import google_sheet
    monkeypatch.setattr(google_sheet, "RETRY_WAITS", ())
    google_sheet._CHECKED.clear()
