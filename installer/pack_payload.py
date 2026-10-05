"""build/app/AutoReview -> build/payload.zip (what the setup unpacks into the install folder)."""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "build" / "app" / "AutoReview"
OUT = ROOT / "build" / "payload.zip"

if __name__ == "__main__":
    if not (SRC / "AutoReview.exe").exists():
        raise SystemExit(f"app build missing: {SRC}")
    files = sorted(p for p in SRC.rglob("*") if p.is_file())
    from stage_resources import validate_public
    for p in files:
        if p.suffix.lower() in {'.dpapi', '.db', '.sqlite', '.bak', '.log'}:
            raise SystemExit('Personal data or developer backup detected in payload')
        if p.suffix.lower() in {'.json', '.ps1', '.gs', '.pem', '.key'}:
            validate_public(p, p.read_bytes())
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            z.write(p, p.relative_to(SRC).as_posix())
    print(f"payload: {len(files)} files, {OUT.stat().st_size // (1024 * 1024)} MB")
