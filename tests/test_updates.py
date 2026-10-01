import hashlib

import httpx
import pytest

from autoreview import updates

BODY = b"MZ fake setup " * 1000
SHA = hashlib.sha256(BODY).hexdigest()


def release(**kw):
    return dict(dict(version="9.9.9", url="https://drive.google.com/file/d/" + "a" * 33 + "/view", sha256=SHA), **kw)


def test_versions_compare_numerically():
    assert updates.is_newer("1.10.0", "1.9.9") and not updates.is_newer("1.1.0", "1.1.0")
    assert not updates.is_newer("garbage", "1.0.0")


def test_only_complete_releases_count_and_the_highest_wins():
    rows = [release(version="1.2.0"), release(version="1.10.0"), release(version="2.0.0", sha256="short"),
            release(version="3.0.0", url="http://plain"), release(version="x")]
    assert updates.pick_latest(rows)["version"] == "1.10.0"
    assert updates.pick_latest([]) is None


def test_drive_share_links_become_direct_downloads():
    fid = "1AbC_dEf-" + "x" * 25
    for link in (f"https://drive.google.com/file/d/{fid}/view?usp=sharing", f"https://drive.google.com/open?id={fid}",
                 f"https://drive.google.com/uc?export=download&id={fid}"):
        assert updates.direct_url(link) == f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
    with pytest.raises(updates.UpdateError):
        updates.direct_url("http://example.com/x.exe")


def serve(body, ctype="application/octet-stream"):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body, headers={"content-type": ctype})))


def test_a_verified_download_is_kept(tmp_path):
    seen = []
    path = updates.download(release(), seen.append, client=serve(BODY), folder=tmp_path)
    assert path.read_bytes() == BODY and seen[-1][0] == len(BODY)


def test_a_wrong_file_is_never_kept(tmp_path):
    folder = tmp_path / "dl"
    folder.mkdir()
    with pytest.raises(updates.UpdateError):
        updates.download(release(), client=serve(BODY + b"x"), folder=folder)
    assert list(folder.iterdir()) == []


def test_a_web_page_instead_of_the_file_is_refused(tmp_path):
    with pytest.raises(updates.UpdateError, match="Anyone"):
        updates.download(release(), client=serve(b"<html>scan warning</html>", "text/html; charset=utf-8"), folder=tmp_path)


def test_source_runs_never_self_install(tmp_path):
    with pytest.raises(updates.UpdateError):
        updates.install(tmp_path / "x.exe", tmp_path, 1)
