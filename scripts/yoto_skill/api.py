from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .auth import AuthManager
from .covers import download_image
from .errors import YotoError

DEFAULT_API_BASE = "https://api.yotoplay.com"


class YotoAPI:
    def __init__(self, auth: AuthManager, http: httpx.Client | None = None, base_url: str | None = None) -> None:
        self.auth = auth
        self.http = http or httpx.Client(timeout=60)
        self.base_url = (base_url or os.environ.get("YOTO_API_BASE_URL") or DEFAULT_API_BASE).rstrip("/")

    def request(self, method: str, path: str, *, retry: bool = True, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.auth.access_token()}"
        try:
            response = self.http.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        except httpx.RequestError as exc:
            raise YotoError(
                "network_error", "Could not reach the Yoto API", {"exception": type(exc).__name__, "path": path}
            ) from exc
        if response.status_code == 401 and retry:
            headers["Authorization"] = f"Bearer {self.auth.access_token(force_refresh=True)}"
            try:
                response = self.http.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
            except httpx.RequestError as exc:
                raise YotoError(
                    "network_error", "Could not reach the Yoto API", {"exception": type(exc).__name__, "path": path}
                ) from exc
        if not response.is_success:
            try:
                details = response.json()
            except (ValueError, json.JSONDecodeError):
                details = response.text
            raise YotoError(
                "api_error",
                f"Yoto API returned HTTP {response.status_code}",
                {"status": response.status_code, "body": details, "path": path},
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def list_cards(self) -> list[dict[str, Any]]:
        payload = self.request("GET", "/content/mine")
        if isinstance(payload, list):
            return payload
        return payload.get("cards") or payload.get("content") or []

    def get_card(self, card_id: str) -> dict[str, Any]:
        payload = self.request("GET", f"/content/{card_id}")
        return payload.get("card", payload)

    def save_card(self, card: dict[str, Any]) -> dict[str, Any]:
        payload = self.request("POST", "/content", json=card)
        return payload.get("card", payload)

    def audio_upload_url(self, sha256: str, filename: str) -> dict[str, Any]:
        payload = self.request(
            "GET", "/media/transcode/audio/uploadUrl", params={"sha256": sha256, "filename": filename}
        )
        return payload.get("upload", payload)

    def upload_signed(self, url: str, payload: bytes, content_type: str) -> None:
        try:
            response = self.http.put(url, content=payload, headers={"Content-Type": content_type})
        except httpx.RequestError as exc:
            raise YotoError(
                "network_error", "Could not upload media to Yoto's storage service", {"exception": type(exc).__name__}
            ) from exc
        if not response.is_success:
            raise YotoError("upload_failed", f"Signed upload returned HTTP {response.status_code}", response.text)

    def transcoded_upload(self, upload_id: str) -> dict[str, Any]:
        payload = self.request("GET", f"/media/upload/{upload_id}/transcoded", params={"loudnorm": "false"})
        return payload.get("transcode", payload)

    def upload_icon(self, filename: str, payload: bytes) -> dict[str, Any]:
        return self.request(
            "POST",
            "/media/displayIcons/user/me/upload",
            params={"autoConvert": "false", "filename": filename},
            content=payload,
            headers={"Content-Type": "image/png"},
        )

    def upload_cover(self, payload: bytes) -> dict[str, Any]:
        return self.request(
            "POST", "/media/coverImage/user/me/upload",
            params={"autoconvert": "true", "coverType": "default"},
            content=payload, headers={"Content-Type": "image/png"},
        )

    def download_cover(self, url: str) -> bytes:
        return download_image(url, self.http)
