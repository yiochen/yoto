from __future__ import annotations

import json
import os
import sys
import urllib.parse
from copy import deepcopy
from typing import Any

import httpx
import pytest

from conftest import MemoryTokenStore
from yoto_skill.api import YotoAPI
from yoto_skill.auth import (
    DEFAULT_REMOTE_CALLBACK_URL,
    AuthManager,
    authorization_url,
    pkce_pair,
)
from yoto_skill.errors import YotoError
from yoto_skill.storage import ConfigStore, KeychainTokenStore, WindowsTokenStore, default_token_store


def test_pkce_and_authorization_parameters() -> None:
    verifier, challenge = pkce_pair()
    assert len(verifier) >= 43
    assert "=" not in challenge
    parsed = urllib.parse.urlparse(authorization_url("client", challenge, "state"))
    query = urllib.parse.parse_qs(parsed.query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == [
        "family:library:view user:content:view user:content:manage "
        "user:icons:manage offline_access"
    ]
    assert query["state"] == ["state"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8787/callback"]


class KeyedMemoryTokenStore:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}

    def load(self, account: str) -> dict[str, Any] | None:
        return deepcopy(self.values.get(account))

    def save(self, account: str, value: dict[str, Any]) -> None:
        self.values[account] = deepcopy(value)

    def delete(self, account: str) -> None:
        self.values.pop(account, None)


def test_phone_auth_begin_and_complete(tmp_path) -> None:
    config = ConfigStore(tmp_path)
    config.write({"client_id": "client"})
    tokens = KeyedMemoryTokenStore()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": "access", "refresh_token": "refresh", "expires_in": 120},
        )

    auth = AuthManager(
        config,
        tokens,
        httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: 100,
    )
    started = auth.begin()
    parsed = urllib.parse.urlparse(started["authorization_url"])
    query = urllib.parse.parse_qs(parsed.query)
    pending = tokens.values["client:pending-login"]

    assert started["callback_url"] == DEFAULT_REMOTE_CALLBACK_URL
    assert query["redirect_uri"] == [DEFAULT_REMOTE_CALLBACK_URL]
    assert query["state"] == [pending["state"]]
    assert "verifier" not in started

    completed = auth.complete({"code": "one-time-code", "state": pending["state"]})
    assert completed == {"authenticated": True, "expires_at": 220}
    assert "client:pending-login" not in tokens.values
    assert tokens.values["client"]["refresh_token"] == "refresh"
    form = urllib.parse.parse_qs(requests[0].content.decode())
    assert form["code"] == ["one-time-code"]
    assert form["code_verifier"] == [pending["verifier"]]
    assert form["redirect_uri"] == [DEFAULT_REMOTE_CALLBACK_URL]


def test_phone_auth_rejects_wrong_state_without_discarding_attempt(tmp_path) -> None:
    config = ConfigStore(tmp_path)
    config.write({"client_id": "client"})
    tokens = KeyedMemoryTokenStore()
    auth = AuthManager(config, tokens, clock=lambda: 100)
    auth.begin()

    with pytest.raises(YotoError) as error:
        auth.complete({"code": "code", "state": "wrong"})

    assert error.value.code == "oauth_state_mismatch"
    assert "client:pending-login" in tokens.values


def test_phone_auth_discards_expired_attempt(tmp_path) -> None:
    config = ConfigStore(tmp_path)
    config.write({"client_id": "client"})
    tokens = KeyedMemoryTokenStore()
    now = [100]
    auth = AuthManager(config, tokens, clock=lambda: now[0])
    auth.begin()
    pending = tokens.values["client:pending-login"]
    now[0] = pending["expires_at"]

    with pytest.raises(YotoError) as error:
        auth.complete({"code": "code", "state": pending["state"]})

    assert error.value.code == "auth_result_expired"
    assert "client:pending-login" not in tokens.values


@pytest.mark.skipif(sys.platform == "win32", reason="Windows uses account ACLs rather than POSIX mode bits")
def test_config_is_private(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.write({"client_id": "abc"})
    assert store.read() == {"client_id": "abc"}
    assert os.stat(store.path).st_mode & 0o777 == 0o600


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Keychain only")
def test_keychain_serializes_secret_for_native_api(monkeypatch) -> None:
    call = {}
    store = KeychainTokenStore()

    def fake_save_raw(account, raw):
        call.update(account=account, raw=raw)

    monkeypatch.setattr(store, "_save_raw", fake_save_raw)
    store.save("client", {"access_token": "secret"})
    assert call["account"] == "client"
    assert json.loads(call["raw"]) == {"access_token": "secret"}


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Keychain only")
def test_keychain_missing_item_is_not_an_error(monkeypatch) -> None:
    store = KeychainTokenStore()
    monkeypatch.setattr(store, "_load_raw", lambda _account: None)
    assert store.load("client") is None


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Keychain only")
def test_keychain_invalid_document_is_reported(monkeypatch) -> None:
    store = KeychainTokenStore()
    monkeypatch.setattr(store, "_load_raw", lambda _account: b"not-json")
    with pytest.raises(YotoError) as error:
        store.load("client")
    assert error.value.code == "keychain_token_invalid"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI only")
def test_windows_tokens_round_trip_encrypted(tmp_path) -> None:
    store = WindowsTokenStore(tmp_path)
    assert isinstance(default_token_store(), WindowsTokenStore)
    assert store.load("client") is None
    store.save("client", {"access_token": "test-secret", "refresh_token": "test-refresh"})
    path = store._path("client")
    assert b"test-secret" not in path.read_bytes()
    assert store.load("client") == {"access_token": "test-secret", "refresh_token": "test-refresh"}
    store.save("client", {"access_token": "rotated"})
    assert store.load("client") == {"access_token": "rotated"}
    store.delete("client")
    assert store.load("client") is None


def test_phone_auth_requires_https_callback(tmp_path) -> None:
    config = ConfigStore(tmp_path)
    config.write({"client_id": "client"})
    auth = AuthManager(config, KeyedMemoryTokenStore())

    with pytest.raises(YotoError) as error:
        auth.begin("http://example.test/callback")

    assert error.value.code == "invalid_callback_url"


def test_auth_status_reports_granted_and_missing_scopes(tmp_path) -> None:
    config = ConfigStore(tmp_path)
    config.write({"client_id": "client"})
    tokens = KeyedMemoryTokenStore()
    tokens.save(
        "client",
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "scope": "user:content:view user:content:manage offline_access",
            "expires_at": 200,
        },
    )

    status = AuthManager(config, tokens, clock=lambda: 100).status()

    assert status["authenticated"] is True
    assert status["scopes"] == ["offline_access", "user:content:manage", "user:content:view"]
    assert status["missing_scopes"] == ["family:library:view", "user:icons:manage"]


def test_refresh_rotates_and_persists_token(tmp_path) -> None:
    config = ConfigStore(tmp_path)
    config.write({"client_id": "client"})
    tokens = MemoryTokenStore({"access_token": "old", "refresh_token": "refresh", "expires_at": 99})

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/token"
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "rotated", "expires_in": 100})

    auth = AuthManager(config, tokens, httpx.Client(transport=httpx.MockTransport(handler)), clock=lambda: 100)
    assert auth.access_token() == "new"
    assert tokens.value["refresh_token"] == "rotated"
    assert tokens.value["expires_at"] == 200
    assert len(tokens.saved) == 1


def test_api_retries_one_401_with_refreshed_token() -> None:
    class FakeAuth:
        def __init__(self) -> None:
            self.calls = []

        def access_token(self, force_refresh: bool = False) -> str:
            self.calls.append(force_refresh)
            return "new" if force_refresh else "old"

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json={"cards": []})

    auth = FakeAuth()
    api = YotoAPI(auth, httpx.Client(transport=httpx.MockTransport(handler)), "https://example.test")
    assert api.list_cards() == []
    assert seen == ["Bearer old", "Bearer new"]
    assert auth.calls == [False, True]


def test_api_unwraps_official_response_shapes() -> None:
    class FakeAuth:
        def access_token(self, force_refresh: bool = False) -> str:
            return "token"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/content/abc12":
            return httpx.Response(200, json={"card": {"cardId": "abc12"}})
        if request.url.path == "/content":
            return httpx.Response(200, json={"card": {"cardId": "new12"}})
        if request.url.path.endswith("/uploadUrl"):
            return httpx.Response(200, json={"upload": {"uploadId": "up", "uploadUrl": None}})
        return httpx.Response(200, json={"transcode": {"transcodedSha256": "hash"}})

    api = YotoAPI(FakeAuth(), httpx.Client(transport=httpx.MockTransport(handler)), "https://example.test")
    assert api.get_card("abc12")["cardId"] == "abc12"
    assert api.save_card({"title": "New"})["cardId"] == "new12"
    assert api.audio_upload_url("hash", "song.mp3")["uploadId"] == "up"
    assert api.transcoded_upload("up")["transcodedSha256"] == "hash"


def test_network_failure_becomes_yoto_error() -> None:
    class FakeAuth:
        def access_token(self, force_refresh: bool = False) -> str:
            return "token"

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    api = YotoAPI(FakeAuth(), httpx.Client(transport=httpx.MockTransport(offline)), "https://example.test")
    with pytest.raises(YotoError) as error:
        api.list_cards()
    assert error.value.code == "network_error"


def test_icon_upload_uses_raw_rgba_png_contract() -> None:
    class FakeAuth:
        def access_token(self, force_refresh: bool = False) -> str:
            return "token"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/media/displayIcons/user/me/upload"
        assert request.url.params["autoConvert"] == "false"
        assert request.url.params["filename"] == "icon.png"
        assert request.headers["Content-Type"] == "image/png"
        assert request.content == b"png-bytes"
        return httpx.Response(200, json={"displayIcon": {"mediaId": "icon-hash"}})

    api = YotoAPI(FakeAuth(), httpx.Client(transport=httpx.MockTransport(handler)), "https://example.test")
    assert api.upload_icon("icon.png", b"png-bytes")["displayIcon"]["mediaId"] == "icon-hash"
