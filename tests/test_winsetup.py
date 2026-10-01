import zipfile

import pytest

from autoreview import winsetup

TEST_KEY = r"Software\AutoReviewTests\Uninstall\SnappPay.AutoReview"


def payload(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def test_extract_installs_and_an_update_replaces_old_files(tmp_path):
    target = tmp_path / "Programs" / "AutoReview"
    p1 = payload(tmp_path / "p1.zip", {"AutoReview.exe": "v1", "_internal/old.txt": "x"})
    seen = []
    winsetup.extract(p1, target, seen.append)
    assert (target / "AutoReview.exe").read_text() == "v1" and seen[-1] == 1.0
    p2 = payload(tmp_path / "p2.zip", {"AutoReview.exe": "v2"})
    winsetup.extract(p2, target)
    assert (target / "AutoReview.exe").read_text() == "v2" and not (target / "_internal" / "old.txt").exists()


def test_extract_never_empties_a_foreign_folder_or_writes_outside(tmp_path):
    foreign = tmp_path / "Documents"
    foreign.mkdir()
    (foreign / "report.xlsx").write_text("important")
    with pytest.raises(RuntimeError):
        winsetup.extract(payload(tmp_path / "p.zip", {"AutoReview.exe": "v"}), foreign)
    assert (foreign / "report.xlsx").exists()
    evil = payload(tmp_path / "evil.zip", {"../escape.txt": "x", "AutoReview.exe": "v"})
    with pytest.raises(RuntimeError):
        winsetup.extract(evil, tmp_path / "Programs" / "AR")
    assert not (tmp_path / "Programs" / "escape.txt").exists()


def test_shortcut_is_created_with_the_windows_shell(tmp_path):
    (tmp_path / "Snapp Pay's folder").mkdir()                          # a space and a quote, like real Windows user folders
    exe = tmp_path / "Snapp Pay's folder" / "AutoReview.exe"
    exe.write_bytes(b"MZ")
    lnk = tmp_path / "menu" / "AutoReview.lnk"
    winsetup.create_shortcut(lnk, exe)
    assert lnk.exists() and lnk.stat().st_size > 100


def test_installed_apps_entry_round_trip_on_a_test_key(tmp_path):
    try:
        winsetup.write_registration(tmp_path, "9.9.9", 1234, reg_path=TEST_KEY)
        reg = winsetup.read_registration(TEST_KEY)
        assert reg["DisplayVersion"] == "9.9.9" and reg["InstallLocation"] == str(tmp_path) and reg["EstimatedSize"] == 1234
        assert reg["UninstallString"] == f'"{tmp_path / "AutoReview.exe"}" --uninstall'
    finally:
        winsetup.remove_registration(TEST_KEY)
    assert winsetup.read_registration(TEST_KEY) is None


def test_only_our_folders_count_as_an_install(tmp_path):
    d = tmp_path / "Programs" / "AutoReview"
    d.mkdir(parents=True)
    assert winsetup.looks_like_our_install(d)                             # empty
    (d / "x.txt").write_text("x")
    assert not winsetup.looks_like_our_install(d)
    (d / "AutoReview.exe").write_text("x")
    assert winsetup.looks_like_our_install(d)
