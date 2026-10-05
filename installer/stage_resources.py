"""Explicit public resource allowlist; developer backups and credentials never ship."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ('category_keywords.json', 'category_map.json', 'columns.json', 'crm_api.json',
          'execution.json', 'nbo_reasons.json', 'reasons.json', 'rules.json')
SCRIPTS = ('crm-get.ps1', 'crm-save-cred.ps1')


def validate_public(path, data):
    if b'-----BEGIN PRIVATE KEY-----' in data or b'-----BEGIN RSA PRIVATE KEY-----' in data:
        raise ValueError('Private key detected in build resources')
    if path.suffix == '.json':
        def check(value):
            if isinstance(value, dict):
                if any(k.lower() in {'private_key', 'app_key', 'access_token', 'refresh_token', 'password'}
                       and v for k, v in value.items()):
                    raise ValueError('Credential field detected in build resources')
                for v in value.values(): check(v)
            elif isinstance(value, list):
                for v in value: check(v)
        check(json.loads(data))


def stage():
    target = ROOT / 'build' / 'public-resources'
    expected = {f'{folder}/{name}' for folder, names in [('config', CONFIG), ('scripts', SCRIPTS)] for name in names}
    expected.add('config/workspace.json')
    if target.exists() and any(p.relative_to(target).as_posix() not in expected for p in target.rglob('*') if p.is_file()):
        raise ValueError('Unexpected file in public staging directory')
    for folder, names in [('config', CONFIG), ('scripts', SCRIPTS)]:
        (target / folder).mkdir(parents=True, exist_ok=True)
        for name in names:
            source = ROOT / folder / name
            data = source.read_bytes()
            validate_public(source, data)
            (target / folder / name).write_bytes(data)
    # Generate only non-secret routing metadata; never copy a developer's workspace config.
    from autoreview.workspace_google import EXPECTED_SHEET_ID
    (target / 'config' / 'workspace.json').write_text(json.dumps({
        'workspace_backend': 'google_sheets', 'own_sheet_id': EXPECTED_SHEET_ID}), encoding='utf-8')


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(ROOT))
    stage()
