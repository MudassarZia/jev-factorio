"""Store a TypeSafe key with Windows DPAPI protection for the current user."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


class KeyStoreError(RuntimeError):
    pass


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(data, decrypt=False):
    if os.name != "nt":
        raise KeyStoreError("Saving API keys requires Windows. You can still enter a key for this session.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.POINTER(DataBlob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = DataBlob(len(data), buffer)
    result = DataBlob()
    try:
        # UI_FORBIDDEN=1; deliberately omit LOCAL_MACHINE so protection is per user.
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            message = "unlock the saved key" if decrypt else "protect the API key"
            raise KeyStoreError(f"Windows could not {message}. Enter your key and try saving it again.")
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(buffer, 0, len(data))
        if result.data:
            ctypes.memset(result.data, 0, result.size)
            kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def _validate(key):
    if not isinstance(key, str) or not 1 <= len(key) <= 8192 or any(c.isspace() or ord(c) < 32 for c in key):
        raise KeyStoreError("Enter a TypeSafe API key without spaces or line breaks before saving.")


class KeyStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return None
        try:
            encrypted = self.path.read_bytes()
            if not 1 <= len(encrypted) <= 65536:
                raise KeyStoreError("Saved API key is invalid. Enter your key and save it again.")
            key = _protect(encrypted, decrypt=True).decode("utf-8")
            _validate(key)
            return key
        except (OSError, UnicodeError):
            raise KeyStoreError("Could not read the saved API key. Enter your key and save it again.") from None

    def save(self, key):
        _validate(key)
        encrypted = _protect(key.encode("utf-8"))
        temporary = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(encrypted)
            temporary.replace(self.path)
        except OSError:
            raise KeyStoreError("Could not write the encrypted API key. Check that the application folder is writable.") from None
        finally:
            temporary.unlink(missing_ok=True)

    def forget(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise KeyStoreError("Could not remove the saved key. Check that the application folder is writable.") from None
