import json
import pytest
from google.oauth2 import service_account
from autoreview import google_credentials as gc


def test_import_encrypts_locally_and_load_reuses_dpapi(monkeypatch, tmp_path):
    info = dict(type='service_account', private_key='unit-test-placeholder',
                client_email='test@example.iam.gserviceaccount.com', token_uri='https://oauth2.googleapis.com/token')
    source = tmp_path / 'incoming.json'
    source.write_text(json.dumps(info), encoding='utf-8')
    validated = []
    monkeypatch.setattr(service_account.Credentials, 'from_service_account_info', lambda value: validated.append(value))
    assert gc.import_file(source) == info['client_email']
    assert validated == [info]
    assert b'unit-test-placeholder' not in gc.key_path().read_bytes()
    source.unlink()
    assert gc.available() and gc.load() == info


def test_invalid_pem_never_replaces_existing_credentials(tmp_path):
    gc.key_path().write_bytes(b'existing-protected-key')
    source = tmp_path / 'invalid.json'
    source.write_text(json.dumps(dict(type='service_account', private_key='bad-private-value',
        client_email='test@example.com', token_uri='https://oauth2.googleapis.com/token')), encoding='utf-8')
    with pytest.raises(ValueError) as error: gc.import_file(source)
    assert gc.key_path().read_bytes() == b'existing-protected-key'
    assert 'bad-private-value' not in str(error.value)


def test_foreign_token_endpoint_is_rejected():
    with pytest.raises(ValueError):
        gc.validate(dict(type='service_account', private_key='placeholder', client_email='test@example.com',
                         token_uri='https://example.com/token'))
