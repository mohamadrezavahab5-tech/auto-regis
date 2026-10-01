"""Windows install / uninstall, per user (no administrator prompt) - used by AutoReview-Setup.exe and by
'AutoReview.exe --uninstall' (the entry Windows 'Installed apps' calls).

Program files:  %LOCALAPPDATA%\\Programs\\AutoReview          (replaced by every update)
Person's data:  %LOCALAPPDATA%\\AutoReview                    (never touched by install/update; removed on uninstall only if
                                                              the person ticks it)
Registration:   HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\SnappPay.AutoReview"""
import ctypes
import os
import shutil
import subprocess
import winreg
import zipfile
from datetime import date
from pathlib import Path

APP_NAME = "AutoReview"
EXE_NAME = "AutoReview.exe"
PUBLISHER = "SnappPay - Online Merchant Review"
REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SnappPay.AutoReview"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def default_install_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Programs" / APP_NAME


def user_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / APP_NAME


def _known_folder(csidl: int) -> Path:
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value)


def desktop_dir() -> Path:
    return _known_folder(0x10)                     # CSIDL_DESKTOPDIRECTORY (follows OneDrive redirection)


def programs_dir() -> Path:
    return _known_folder(0x02)                     # CSIDL_PROGRAMS = the person's Start menu\Programs


def shortcut_paths():
    return {"start": programs_dir() / f"{APP_NAME}.lnk", "desktop": desktop_dir() / f"{APP_NAME}.lnk"}


# ---- registration ("Installed apps") -------------------------------------------------------------------------------------
def read_registration(reg_path: str = REG_PATH):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as k:
            out = {}
            for name in ("DisplayVersion", "InstallLocation", "DisplayName", "UninstallString", "EstimatedSize"):
                try:
                    out[name] = winreg.QueryValueEx(k, name)[0]
                except OSError:
                    pass
            return out
    except OSError:
        return None


def write_registration(install_dir: Path, version: str, size_kb: int, reg_path: str = REG_PATH):
    exe = install_dir / EXE_NAME
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_path) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, version)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, f"{exe},0")
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, f'"{exe}" --uninstall')
        winreg.SetValueEx(k, "InstallDate", 0, winreg.REG_SZ, date.today().strftime("%Y%m%d"))
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, int(size_kb))
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def remove_registration(reg_path: str = REG_PATH):
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, reg_path)
    except OSError:
        pass


# ---- shortcuts -------------------------------------------------------------------------------------------------------------
def create_shortcut(lnk: Path, target: Path, description=APP_NAME):
    r"""A .lnk through Windows' own shell object (PowerShell, no window). The paths travel as environment variables - never
    pasted into the command, so a space ('C:\Users\Snapp Pay\...') or a quote in a path cannot break or change it."""
    lnk.parent.mkdir(parents=True, exist_ok=True)
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:AR_LNK); $s.TargetPath = $env:AR_TARGET; "
              "$s.WorkingDirectory = (Split-Path $env:AR_TARGET); $s.IconLocation = $env:AR_TARGET + ',0'; $s.Description = $env:AR_DESC; $s.Save()")
    env = dict(os.environ, AR_LNK=str(lnk), AR_TARGET=str(target), AR_DESC=description)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                       capture_output=True, text=True, creationflags=NO_WINDOW, timeout=60)
    if r.returncode != 0 or not lnk.exists():
        raise RuntimeError(f"shortcut not created: {r.stderr.strip()[:200]}")


def remove_shortcuts():
    for p in shortcut_paths().values():
        try:
            p.unlink()
        except (FileNotFoundError, OSError):
            pass


# ---- the running app -------------------------------------------------------------------------------------------------------
def app_running() -> bool:
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/NH"], capture_output=True, text=True, creationflags=NO_WINDOW)
    return EXE_NAME.lower() in r.stdout.lower()


def close_app():
    subprocess.run(["taskkill", "/IM", EXE_NAME, "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)


# ---- files -----------------------------------------------------------------------------------------------------------------
def looks_like_our_install(path: Path) -> bool:
    """Only a folder that is empty or holds AutoReview.exe may be emptied - never some other folder the person picked."""
    return path.exists() and ((path / EXE_NAME).exists() or not any(path.iterdir()))


def extract(payload_zip, target: Path, progress=None):
    """Replace the program files with the payload (old files first removed, so nothing stale survives an update)."""
    target = Path(target)
    if target.exists():
        if not looks_like_our_install(target):
            raise RuntimeError(f"پوشه‌ی «{target}» خالی نیست و برنامه‌ی AutoReview هم در آن نیست؛ پوشه‌ی دیگری انتخاب کن.")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(payload_zip) as z:
        members = z.infolist()
        total = sum(m.file_size for m in members) or 1
        done = 0
        for m in members:
            dest = (target / m.filename).resolve()
            if not str(dest).startswith(str(target.resolve())):              # never write outside the install folder
                raise RuntimeError(f"unsafe path in payload: {m.filename}")
            z.extract(m, target)
            done += m.file_size
            if progress:
                progress(done / total)
    return sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) // 1024


def schedule_removal(path: Path):
    """Delete the program folder a few seconds after this process exits (a running exe cannot delete itself)."""
    if not looks_like_our_install(path):
        return
    subprocess.Popen(["cmd", "/c", "ping", "127.0.0.1", "-n", "4", ">nul", "&", "rmdir", "/s", "/q", str(path)],
                     creationflags=NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
