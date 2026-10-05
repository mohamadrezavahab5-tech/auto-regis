"""Verify the built app includes current Workspace code and no private profile files."""
import hashlib
import json
import zipfile
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent.parent


def verify():
    from autoreview.version import __version__
    app = ROOT / 'build/app/AutoReview/AutoReview.exe'
    # Qt resolves unversioned ICU imports against Windows; a bundled Poppler ICU
    # shadows that system DLL and has different exported procedure names.
    import ast
    for kind, name in [('app', 'AutoReview'), ('setup', f'AutoReview-Setup-{__version__}')]:
        toc = ast.literal_eval((ROOT / f'build/work-{kind}/{name}/Analysis-00.toc').read_text(encoding='utf-8'))
        def audit(value):
            if isinstance(value, (list, tuple)):
                if len(value) == 3 and all(isinstance(v, str) for v in value) and value[2] == 'BINARY':
                    if 'poppler' in value[1].lower(): raise ValueError('Foreign Poppler DLL in build: ' + value[0])
                else:
                    for item in value: audit(item)
        audit(toc)
    archive = CArchiveReader(str(app)).open_embedded_archive('PYZ.pyz')
    names = ['autoreview.workspace_google', 'autoreview.workspace', 'autoreview.workflow_sync',
             'autoreview.google_credentials', 'autoreview.sheets', 'autoreview.app.pages.connections',
             'autoreview.app.session', 'autoreview.app.execution_control', 'autoreview.version']
    for name in names:
        code = archive.extract(name)
        expected = compile((ROOT / (name.replace('.', '/') + '.py')).read_text(encoding='utf-8'),
                           code.co_filename, 'exec', dont_inherit=True)
        if code != expected: raise ValueError('Built module differs from current source: ' + name)
    for name in ('google.oauth2.service_account', 'google.auth.transport.requests', 'google.auth.crypt.rsa', 'httpx', 'requests'):
        if name not in archive.toc: raise ValueError('Missing bundled dependency: ' + name)
    payload = ROOT / 'build/payload.zip'
    with zipfile.ZipFile(payload) as bundle:
        for name in bundle.namelist():
            if Path(name).suffix.lower() in {'.dpapi', '.db', '.sqlite', '.bak', '.log', '.gs'}:
                raise ValueError('Forbidden private or legacy payload file')
            if name.startswith('_internal/config/'):
                from installer.stage_resources import validate_public
                validate_public(Path(name), bundle.read(name))
        if bundle.read('AutoReview.exe') != app.read_bytes(): raise ValueError('Payload app is stale')
        config = json.loads(bundle.read('_internal/config/workspace.json'))
        if set(config) != {'workspace_backend', 'own_sheet_id'}: raise ValueError('Unexpected workspace configuration')
        count = len(bundle.namelist())
    setup = ROOT / 'dist' / f'AutoReview-Setup-{__version__}.exe'
    setup_archive = CArchiveReader(str(setup))
    if hashlib.sha256(setup_archive.extract('payload.zip')).digest() != hashlib.sha256(payload.read_bytes()).digest():
        raise ValueError('Installer carries a stale payload')
    import subprocess
    proof = ROOT / 'build' / 'setup-self-test.json'
    proof.unlink(missing_ok=True)
    process = subprocess.Popen([str(setup), '--self-test', str(proof)], creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        if process.wait(timeout=90) != 0: raise ValueError('Installer startup self-test failed')
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        raise ValueError('Installer startup did not complete; possible loader error') from None
    if not proof.is_file() or not json.loads(proof.read_text()).get('qt_rendered'):
        raise ValueError('Installer did not confirm Qt rendering')
    result = dict(version=__version__, files=count, module_sources_match=True, google_dependencies_present=True,
                  private_profile_files=False, legacy_scripts=False, installer_qt_self_test=True, installer_bytes=setup.stat().st_size,
                  sha256=hashlib.sha256(setup.read_bytes()).hexdigest())
    (ROOT / 'build' / 'verification-1.3.22.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(ROOT))
    verify()
