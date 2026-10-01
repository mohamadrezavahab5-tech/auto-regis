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
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            z.write(p, p.relative_to(SRC).as_posix())
    print(f"payload: {len(files)} files, {OUT.stat().st_size // (1024 * 1024)} MB")
