"""Service-account key stored with Windows DPAPI, never shipped in the installer."""
import ctypes
import json
import os
from pathlib import Path
from ctypes import wintypes

from .paths import user_dir


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('ذخیره امن کلید فقط روی ویندوز پشتیبانی می‌شود')
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.restype = wintypes.BOOL
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    # CRYPTPROTECT_UI_FORBIDDEN; user-scope encryption (never machine scope).
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError('ویندوز نتوانست کلید اتصال را رمزگذاری/بازخوانی کند')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(result.data, 0, result.size)
        kernel.LocalFree(result.data)


def key_path():
    return user_dir() / 'google-service-account.dpapi'


def validate(info):
    if info.get('type') != 'service_account' or not info.get('private_key') or not info.get('client_email'):
        raise ValueError('فایل Service Account معتبر نیست')
    if info.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise ValueError('نشانی احراز هویت فایل با Google مطابقت ندارد')
    if info.get('universe_domain', 'googleapis.com') != 'googleapis.com':
        raise ValueError('این دامنه گوگل پشتیبانی نمی‌شود')
    return info


def import_file(path):
    info = validate(json.loads(Path(path).read_text(encoding='utf-8-sig')))
    protected = _dpapi(json.dumps(info).encode())
    target = key_path()
    temporary = target.with_suffix('.tmp')
    temporary.write_bytes(protected)
    os.replace(temporary, target)
    return info['client_email']


def load():
    return validate(json.loads(_dpapi(key_path().read_bytes(), decrypt=True)))


def available():
    return key_path().is_file()
