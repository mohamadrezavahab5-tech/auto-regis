import json
import pytest
from autoreview import workspace, sheets, crm_sync, settings, workspace_google


def test_workspace_never_uses_saved_profile_as_crm_auth(monkeypatch):
    monkeypatch.setattr(workspace_google, 'get_backend', lambda *a: pytest.fail('unauthenticated network call'))
    with pytest.raises(sheets.SheetError) as exc:
        workspace.call('read')
    assert exc.value.code == 'AUTH_ERROR'


def test_handshake_rejects_wrong_schema(monkeypatch):
    monkeypatch.setattr(workspace, 'call', lambda *a: {'ok': True, 'version': 4})
    with pytest.raises(sheets.SheetError) as exc: workspace.health()
    assert exc.value.code == 'SHEET_SCHEMA_ERROR'


def test_release_publish_uses_direct_workspace(monkeypatch):
    captured = {}
    def call(action, cfg=None, **payload):
        captured.update(action=action, **payload)
        return {'ok': True}
    monkeypatch.setattr(workspace, 'call', call)
    assert workspace.publish_release('1.3.22', 'https://example.test/setup.exe', 'a'*64, 'notes')['ok']
    assert captured['action'] == 'publish_release'
    assert len(captured['operation_id']) == 32


def test_settings_survive_reload_and_failed_atomic_write(monkeypatch):
    settings.save_user({'rules': {'runtime.concurrency': 3}})
    from autoreview import atomic_file
    monkeypatch.setattr(atomic_file.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError): settings.save_user({'rules': {'runtime.concurrency': 4}})
    assert settings.load_rules()['runtime']['concurrency'] == 3


def test_safe_audit_metadata_does_not_include_credentials():
    event = workspace.safe_event({'event_id': 'a'*32, 'kind': 'SOURCE', 'detail':
                                  {'password': 'private', 'cookie': 'private', 'status': 'PENDING'}})
    assert 'private' not in json.dumps(event)
    assert 'PENDING' in event['detail']
