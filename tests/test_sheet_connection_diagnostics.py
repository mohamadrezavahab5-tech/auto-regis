import httpx
import pytest

from autoreview import google_sheet as gs


def test_google_api_error_includes_safe_actionable_response_detail():
    def handle(_request):
        return httpx.Response(400, json={'error': {'message': 'Invalid requests[0].addSheet: title already exists'}})

    with gs.Client('x' * 30, transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(gs.GoogleSheetError, match=r'Google Sheets \(400\): Invalid requests\[0\]'):
            client.ping()


def test_optional_sheet_protection_and_layout_failures_do_not_block_sync(monkeypatch):
    sheet_id = 'z' * 30
    gs._CHECKED.pop(sheet_id, None)
    try:
        with gs.Client(sheet_id, transport=httpx.MockTransport(lambda _request: httpx.Response(200))) as client:
            monkeypatch.setattr(client, 'ensure_tabs', lambda _labels: ['Results'])

            def fail(*_args):
                raise gs.GoogleSheetError('optional setup failed')

            monkeypatch.setattr(client, 'lock', fail)
            monkeypatch.setattr(client, 'tidy', fail)
            monkeypatch.setattr(client, 'style', fail)
            assert client.ensure_tabs_once() == ['Results']
    finally:
        gs._CHECKED.pop(sheet_id, None)
