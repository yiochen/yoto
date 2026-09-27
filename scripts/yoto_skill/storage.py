from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

from .errors import YotoError

KEYCHAIN_SERVICE = "com.openai.codex.skill.yoto"
ERR_SEC_SUCCESS = 0
ERR_SEC_DUPLICATE_ITEM = -25299
ERR_SEC_ITEM_NOT_FOUND = -25300


def app_data_dir() -> Path:
    override = os.environ.get("YOTO_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Yoto Skill"
    return Path.home() / "Library/Application Support/Yoto Skill"


class ConfigStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or app_data_dir()
        self.path = self.root / "config.json"

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise YotoError("config_invalid", f"Could not read {self.path}", str(exc)) from exc

    def write(self, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)


class TokenStore(Protocol):
    def load(self, account: str) -> dict[str, Any] | None: ...
    def save(self, account: str, value: dict[str, Any]) -> None: ...
    def delete(self, account: str) -> None: ...


def default_token_store() -> TokenStore:
    if sys.platform == "win32":
        return WindowsTokenStore()
    if sys.platform == "darwin":
        return KeychainTokenStore()
    raise YotoError("unsupported_platform", "Yoto supports Windows and macOS")


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class WindowsTokenStore:
    """Encrypt OAuth data with current-user Windows DPAPI before writing it locally."""

    def __init__(self, root: Path | None = None) -> None:
        if sys.platform != "win32":
            raise YotoError("windows_security_unavailable", "Windows data protection is unavailable")
        self.root = (root or app_data_dir()) / "credentials"
        self.crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        pointer = ctypes.POINTER(DataBlob)
        self.crypt32.CryptProtectData.argtypes = [pointer, ctypes.c_wchar_p, pointer, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, pointer]
        self.crypt32.CryptProtectData.restype = ctypes.c_int
        self.crypt32.CryptUnprotectData.argtypes = [pointer, ctypes.c_void_p, pointer, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, pointer]
        self.crypt32.CryptUnprotectData.restype = ctypes.c_int
        self.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel32.LocalFree.restype = ctypes.c_void_p

    def _path(self, account: str) -> Path:
        digest = hashlib.sha256(account.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.bin"

    def _transform(self, raw: bytes, protect: bool) -> bytes:
        buffer = ctypes.create_string_buffer(raw)
        source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        result = DataBlob()
        operation = self.crypt32.CryptProtectData if protect else self.crypt32.CryptUnprotectData
        if not operation(ctypes.byref(source), "Yoto OAuth credentials" if protect else None, None, None, None, 0x01, ctypes.byref(result)):
            error = ctypes.get_last_error()
            raise YotoError("windows_security_error", f"Windows could not protect Yoto credentials (error {error})", {"win_error": error})
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            self.kernel32.LocalFree(result.pbData)

    def load(self, account: str) -> dict[str, Any] | None:
        path = self._path(account)
        if not path.exists():
            return None
        try:
            raw = self._transform(path.read_bytes(), protect=False)
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise YotoError("windows_token_invalid", "Stored Yoto credentials could not be read") from exc
        if not isinstance(value, dict):
            raise YotoError("windows_token_invalid", "Stored Yoto credentials are invalid")
        return value

    def save(self, account: str, value: dict[str, Any]) -> None:
        encrypted = self._transform(json.dumps(value, separators=(",", ":")).encode("utf-8"), protect=True)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(account)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        temporary.replace(path)

    def delete(self, account: str) -> None:
        self._path(account).unlink(missing_ok=True)


class KeychainTokenStore:
    """Store an OAuth token document in macOS Keychain, never on the command line."""

    def __init__(self, service: str = KEYCHAIN_SERVICE) -> None:
        self.service = service
        try:
            self.security = ctypes.CDLL(
                "/System/Library/Frameworks/Security.framework/Security"
            )
            self.core_foundation = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
        except OSError as exc:
            raise YotoError("keychain_unavailable", "The macOS Keychain API is unavailable") from exc
        self._configure_native_signatures()

    def _configure_native_signatures(self) -> None:
        pointer = ctypes.c_void_p
        uint32 = ctypes.c_uint32
        self.security.SecKeychainFindGenericPassword.argtypes = [
            pointer,
            uint32,
            ctypes.c_char_p,
            uint32,
            ctypes.c_char_p,
            ctypes.POINTER(uint32),
            ctypes.POINTER(pointer),
            ctypes.POINTER(pointer),
        ]
        self.security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainAddGenericPassword.argtypes = [
            pointer,
            uint32,
            ctypes.c_char_p,
            uint32,
            ctypes.c_char_p,
            uint32,
            pointer,
            ctypes.POINTER(pointer),
        ]
        self.security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainItemModifyAttributesAndData.argtypes = [
            pointer,
            pointer,
            uint32,
            pointer,
        ]
        self.security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self.security.SecKeychainItemDelete.argtypes = [pointer]
        self.security.SecKeychainItemDelete.restype = ctypes.c_int32
        self.security.SecKeychainItemFreeContent.argtypes = [pointer, pointer]
        self.security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self.core_foundation.CFRelease.argtypes = [pointer]
        self.core_foundation.CFRelease.restype = None

    def _identifiers(self, account: str) -> tuple[bytes, bytes]:
        return self.service.encode(), account.encode()

    def _raise_status(self, action: str, status: int) -> None:
        raise YotoError(
            "keychain_error",
            f"macOS Keychain could not {action}",
            {"os_status": status},
        )

    def _find_item(self, account: str, include_data: bool) -> tuple[int, ctypes.c_void_p, bytes | None]:
        service, encoded_account = self._identifiers(account)
        item = ctypes.c_void_p()
        length = ctypes.c_uint32()
        data = ctypes.c_void_p()
        status = self.security.SecKeychainFindGenericPassword(
            None,
            len(service),
            service,
            len(encoded_account),
            encoded_account,
            ctypes.byref(length) if include_data else None,
            ctypes.byref(data) if include_data else None,
            ctypes.byref(item),
        )
        if status != ERR_SEC_SUCCESS:
            return status, item, None
        if not include_data:
            return status, item, None
        try:
            raw = ctypes.string_at(data, length.value)
        finally:
            self.security.SecKeychainItemFreeContent(None, data)
        return status, item, raw

    def _load_raw(self, account: str) -> bytes | None:
        status, item, raw = self._find_item(account, include_data=True)
        if status == ERR_SEC_ITEM_NOT_FOUND:
            return None
        if status != ERR_SEC_SUCCESS:
            self._raise_status("read credentials", status)
        try:
            return raw
        finally:
            if item:
                self.core_foundation.CFRelease(item)

    def _save_raw(self, account: str, raw: bytes) -> None:
        service, encoded_account = self._identifiers(account)
        buffer = ctypes.create_string_buffer(raw)
        item = ctypes.c_void_p()
        status = self.security.SecKeychainAddGenericPassword(
            None,
            len(service),
            service,
            len(encoded_account),
            encoded_account,
            len(raw),
            ctypes.cast(buffer, ctypes.c_void_p),
            ctypes.byref(item),
        )
        if status == ERR_SEC_SUCCESS:
            if item:
                self.core_foundation.CFRelease(item)
            return
        if status != ERR_SEC_DUPLICATE_ITEM:
            self._raise_status("save credentials", status)

        find_status, existing_item, _ = self._find_item(account, include_data=False)
        if find_status != ERR_SEC_SUCCESS:
            self._raise_status("find credentials for update", find_status)
        try:
            update_status = self.security.SecKeychainItemModifyAttributesAndData(
                existing_item,
                None,
                len(raw),
                ctypes.cast(buffer, ctypes.c_void_p),
            )
        finally:
            if existing_item:
                self.core_foundation.CFRelease(existing_item)
        if update_status != ERR_SEC_SUCCESS:
            self._raise_status("update credentials", update_status)

    def load(self, account: str) -> dict[str, Any] | None:
        raw = self._load_raw(account)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise YotoError("keychain_token_invalid", "Stored Yoto credentials are invalid JSON") from exc

    def save(self, account: str, value: dict[str, Any]) -> None:
        self._save_raw(account, json.dumps(value, separators=(",", ":")).encode())

    def delete(self, account: str) -> None:
        status, item, _ = self._find_item(account, include_data=False)
        if status == ERR_SEC_ITEM_NOT_FOUND:
            return
        if status != ERR_SEC_SUCCESS:
            self._raise_status("find credentials for deletion", status)
        try:
            delete_status = self.security.SecKeychainItemDelete(item)
        finally:
            if item:
                self.core_foundation.CFRelease(item)
        if delete_status != ERR_SEC_SUCCESS:
            self._raise_status("delete credentials", delete_status)
