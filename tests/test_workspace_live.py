"""Opt-in: AUTOREVIEW_LIVE_WORKSPACE=1, AUTOREVIEW_LIVE_HOME=<local profile>.

Uses this PC's DPAPI credentials. Health may add the v6 tabs atomically and writes
the unchanged schema cell to prove Editor access. No cases/claims are submitted.
"""
import os
import pytest


@pytest.mark.skipif(os.environ.get('AUTOREVIEW_LIVE_WORKSPACE') != '1', reason='live Workspace is explicitly opt-in')
def test_real_workspace_health_and_read(monkeypatch):
    home = os.environ.get('AUTOREVIEW_LIVE_HOME')
    assert home, 'Set AUTOREVIEW_LIVE_HOME to the local DPAPI profile directory'
    monkeypatch.setenv('AUTOREVIEW_HOME', home)
    from autoreview.workspace_google import Backend
    backend = Backend()
    try:
        result = backend.health(force=True)
        assert result['write_ok'] and result['schema_version'] == 6
        snapshot = backend.call('read', 'integration-check', 'manual', 'manual')
        assert isinstance(snapshot['cases'], list)
    finally:
        backend.client.close()
