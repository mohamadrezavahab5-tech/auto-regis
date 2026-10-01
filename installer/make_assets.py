"""Build assets: build/autoreview.ico (the app mark at 16-256 px, PNG frames) and Windows version-info files for the exe
properties of the app and of the setup."""
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from autoreview.version import __version__  # noqa: E402

BUILD = ROOT / "build"


def write_ico(path: Path):
    from autoreview.app.icons import logo_image
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    frames = []
    for s in sizes:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        logo_image(s).save(buf, "PNG")
        frames.append(bytes(buf.data()))
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries, blobs = b"", b""
    for s, data in zip(sizes, frames):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    path.write_bytes(header + entries + blobs)


def version_file(path: Path, name: str, description: str, original: str):
    v = tuple(int(x) for x in __version__.split(".")) + (0,)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'SnappPay'),
      StringStruct('FileDescription', '{description}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('OriginalFilename', '{original}'),
      StringStruct('ProductName', 'AutoReview'),
      StringStruct('ProductVersion', '{__version__}'),
      StringStruct('LegalCopyright', 'SnappPay - Online Merchant Review')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


if __name__ == "__main__":
    BUILD.mkdir(exist_ok=True)
    app = QGuiApplication(sys.argv[:1])
    write_ico(BUILD / "autoreview.ico")
    version_file(BUILD / "version_app.txt", "AutoReview", "AutoReview - online merchant registration review", "AutoReview.exe")
    version_file(BUILD / "version_setup.txt", "AutoReview-Setup", "AutoReview installer", f"AutoReview-Setup-{__version__}.exe")
    print("assets ok", __version__)
