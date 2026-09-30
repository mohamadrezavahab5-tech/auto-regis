from autoreview.duplicates import find_duplicates


def test_duplicate_by_site_ignores_itself():
    pending = [{"id": "SMR-1", "site": "https://www.a.ir/x"}, {"id": "SMR-2", "site": "b.ir"}, {"id": "SMR-3", "site": ""}]
    nbo = [{"id": "SMR-9", "site": "a.ir"}, {"id": "SMR-2", "site": "b.ir"}]
    crm = [{"id": "MRG-7", "site": "http://a.ir"}]
    r = {x["id"]: x for x in find_duplicates(pending, nbo, crm)}
    assert r["SMR-1"]["is_duplicate"] and r["SMR-1"]["related"] == ["SMR-9", "MRG-7"]
    assert not r["SMR-2"]["is_duplicate"]      # only matches itself
    assert not r["SMR-3"]["is_duplicate"]      # no site -> never a duplicate
