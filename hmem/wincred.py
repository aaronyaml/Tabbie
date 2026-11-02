"""Windows Credential Manager through the Win32 API (ctypes), no packages needed. Same three calls as
the `keyring` library: set_password / get_password / delete_password. Each secret is a generic
credential named "<service>/<account>", stored for this Windows user only."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2  # this user, this computer (not roamed)
_ERROR_NOT_FOUND = 1168


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def _api():
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    adv.CredFree.argtypes = [ctypes.c_void_p]
    return adv


def set_password(service: str, account: str, secret: str) -> None:
    blob = secret.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    c = _CREDENTIAL(Type=_CRED_TYPE_GENERIC, TargetName=f"{service}/{account}", UserName=account,
                    CredentialBlobSize=len(blob), CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
                    Persist=_CRED_PERSIST_LOCAL_MACHINE)
    if not _api().CredWriteW(ctypes.byref(c), 0):
        raise OSError(ctypes.get_last_error(), "couldn't save to Windows Credential Manager")


def get_password(service: str, account: str) -> str | None:
    adv = _api()
    p = ctypes.POINTER(_CREDENTIAL)()
    if not adv.CredReadW(f"{service}/{account}", _CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
        return None
    try:
        n = p.contents.CredentialBlobSize
        return bytes(p.contents.CredentialBlob[:n]).decode("utf-16-le") if n else ""
    finally:
        adv.CredFree(p)


def delete_password(service: str, account: str) -> None:
    if not _api().CredDeleteW(f"{service}/{account}", _CRED_TYPE_GENERIC, 0):
        if ctypes.get_last_error() != _ERROR_NOT_FOUND:
            raise OSError(ctypes.get_last_error(), "couldn't delete from Windows Credential Manager")
