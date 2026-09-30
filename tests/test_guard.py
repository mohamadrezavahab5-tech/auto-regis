import json

import pytest

from autoreview import guard


def cfg(tmp_path, **kw):
    base = {"enabled": False, "approved_by": None, "approved_at": None, "targets": {"nbo_status_changes": False}}
    base.update(kw)
    p = tmp_path / "e.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


def test_shipped_config_is_closed():
    with pytest.raises(guard.ExecutionBlocked):
        guard.require("nbo_status_changes")


def test_needs_switch_approval_and_the_specific_target(tmp_path):
    with pytest.raises(guard.ExecutionBlocked, match="disabled"):
        guard.require("nbo_status_changes", cfg(tmp_path))
    with pytest.raises(guard.ExecutionBlocked, match="not approved"):
        guard.require("nbo_status_changes", cfg(tmp_path, enabled=True))
    with pytest.raises(guard.ExecutionBlocked, match="not enabled"):
        guard.require("nbo_status_changes", cfg(tmp_path, enabled=True, approved_by="owner", approved_at="2026-10-01"))
    guard.require("nbo_status_changes", cfg(tmp_path, enabled=True, approved_by="owner", approved_at="2026-10-01",
                                            targets={"nbo_status_changes": True}))
