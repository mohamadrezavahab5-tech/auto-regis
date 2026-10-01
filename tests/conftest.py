import pytest


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    """Every test gets its own empty profile folder: nothing ever reads or writes the real database, settings or logins."""
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("AUTOREVIEW_HOME", str(home))
    return home
