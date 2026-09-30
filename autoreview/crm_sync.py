"""Automatic CRM export - READ-ONLY. Replaces the manual 'Export to Excel' step.

Auth: the person's own CRM login, typed once in the app and stored DPAPI-encrypted (only that Windows user can decrypt it).
Transport: PowerShell Invoke-RestMethod (NTLM), the same way the team's other CRM tool does it - no extra dependency.
Nothing is ever written to CRM: the only verb used is GET."""
import json
import subprocess
from pathlib import Path
from urllib.parse import quote

from .paths import app_root, config_dir, data_dir

FORMATTED = "@OData.Community.Display.V1.FormattedValue"


def _script(name: str) -> Path:
    for base in (app_root() / "scripts", Path(__file__).resolve().parent.parent / "scripts"):
        if (base / name).is_file():
            return base / name
    raise FileNotFoundError(name)


def cred_file() -> Path:
    return data_dir() / "crm_credential.xml"


def have_credentials() -> bool:
    return cred_file().is_file()


def _ps(args, stdin=None, timeout=300):
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", *args]
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def save_credentials(username: str, password: str) -> None:
    r = _ps([str(_script("crm-save-cred.ps1")), "-OutFile", str(cred_file())], stdin=f"{username}\n{password}\n", timeout=60)
    if r.returncode != 0:
        raise RuntimeError("could not store the CRM login: " + r.stderr.strip()[:200])


def forget_credentials() -> None:
    cred_file().unlink(missing_ok=True)


def get(path: str, runner=None) -> dict:
    """One GET against the CRM Web API. `runner` is injectable for tests."""
    if runner:
        return runner(path)
    r = _ps([str(_script("crm-get.ps1")), "-Path", path, "-CredFile", str(cred_file())])
    if r.returncode != 0:
        raise RuntimeError("CRM read failed: " + (r.stderr.strip() or r.stdout.strip())[:300])
    return json.loads(r.stdout)


def get_all(path: str, runner=None, max_pages=200):
    """Follow @odata.nextLink until exhausted."""
    rows, page = [], 0
    while path and page < max_pages:
        data = get(path, runner)
        rows.extend(data.get("value", []))
        path, page = data.get("@odata.nextLink"), page + 1
    return rows


def discover(runner=None) -> dict:
    """List entities that look like merchant registrations, with their attributes, so the mapping can be filled in."""
    flt = quote("contains(LogicalName,'regist') or contains(LogicalName,'merchant')", safe="(),' ")
    ents = get(f"EntityDefinitions?$select=LogicalName,EntitySetName&$filter={flt}", runner).get("value", [])
    return {e["LogicalName"]: e["EntitySetName"] for e in ents}


def attributes(logical_name: str, runner=None) -> list:
    data = get(f"EntityDefinitions(LogicalName='{logical_name}')/Attributes?$select=LogicalName,AttributeType", runner)
    return [(a["LogicalName"], a.get("AttributeType")) for a in data.get("value", [])]


def load_mapping():
    return json.loads((config_dir() / "crm_api.json").read_text(encoding="utf-8"))


def fetch_rows(mapping=None, runner=None):
    """-> [{'smr','status','site'}] in the same shape imports.read_export gives for a CRM file."""
    m = mapping or load_mapping()
    f = m["fields"]
    if not m.get("entity_set") or not all(f.get(k) for k in ("smr", "status", "site")):
        raise RuntimeError("CRM mapping is not filled in yet (config/crm_api.json) - run 'Discover CRM structure' first")
    sel = ",".join(sorted({f["smr"], f["status"], f["site"]}))
    path = f"{m['entity_set']}?$select={sel}" + (f"&$filter={quote(m['filter'], safe='(),' + chr(39))}" if m.get("filter") else "")
    rows = []
    for r in get_all(path, runner):
        status = r.get(f["status"] + FORMATTED) if m.get("status_labels_are_formatted_values", True) else r.get(f["status"])
        smr = str(r.get(f["smr"]) or "").strip()
        if smr:
            rows.append({"smr": smr, "status": str(status if status is not None else r.get(f["status"]) or "").strip(),
                         "site": str(r.get(f["site"]) or "").strip()})
    return rows
