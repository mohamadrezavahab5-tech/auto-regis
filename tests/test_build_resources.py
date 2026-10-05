from pathlib import Path
import pytest
from installer.stage_resources import validate_public, CONFIG, SCRIPTS


@pytest.mark.parametrize('data', [b'{"private_key":"placeholder"}', b'{"nested":{"password":"test"}}',
                                   b'{"app_key":"old-key"}', b'{"key":"-----BEGIN PRIVATE KEY-----"}'])
def test_credentials_are_rejected_before_packaging(data):
    with pytest.raises(ValueError): validate_public(Path('test.json'), data)


def test_only_public_resources_are_allowlisted():
    assert all(name.endswith('.json') for name in CONFIG)
    assert 'workspace.json.bak' not in CONFIG
    assert not any(name.endswith('.gs') for name in SCRIPTS)
    validate_public(Path('public.json'), b'{"workspace_backend":"google_sheets"}')
