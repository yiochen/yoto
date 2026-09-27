from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from PIL import Image, UnidentifiedImageError

from . import cards
from .api import YotoAPI
from .covers import COVER_SIZE, inspect_published_cover
from .errors import YotoError, require
from .icons import prepare_icon, upload_icon
from .media import inspect_mp3, upload_audio
from .storage import app_data_dir


class YotoService:
    def __init__(self, api: YotoAPI) -> None:
        self.api = api

    @contextmanager
    def _card_lock(self, card_id: str) -> Iterator[None]:
        directory = app_data_dir() / "locks"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{card_id}.lock"
        with path.open("w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def list_cards(self) -> list[dict[str, Any]]:
        return [cards.summarize_card(card) for card in self.api.list_cards()]

    def show_card(self, card_id: str) -> dict[str, Any]:
        return cards.summarize_card(self.api.get_card(card_id))

    def create_card(self, title: str, description: str | None = None) -> dict[str, Any]:
        require(bool(title.strip()), "invalid_title", "Card title cannot be empty")
        response = self.api.save_card(cards.new_card(title.strip(), description))
        return cards.summarize_card(response) if isinstance(response, dict) else {"created": True, "title": title}

    def set_cover(self, card_id: str, image_file: str) -> dict[str, Any]:
        path = Path(image_file).expanduser().resolve()
        require(path.is_file(), "cover_not_found", f"Prepared cover does not exist: {path}")
        try:
            with Image.open(path) as opened:
                image_format, image_size = opened.format, opened.size
                opened.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise YotoError("invalid_cover", "Prepared cover is not a readable image") from exc
        require(image_format == "PNG" and image_size == COVER_SIZE, "cover_not_prepared",
                "Run media prepare-cover and inspect its 160x254 preview before uploading")
        upload = self.api.upload_cover(path.read_bytes())
        cover = upload.get("coverImage", upload) if isinstance(upload, dict) else {}
        media_url = cover.get("mediaUrl") if isinstance(cover, dict) else None
        require(isinstance(media_url, str) and media_url.startswith("https://"),
                "cover_upload_invalid_response", "Yoto cover upload response has no HTTPS media URL")
        # Inspect the converted asset before referencing it from a playlist.
        inspect_published_cover(self.api.download_cover(media_url), path)
        with self._card_lock(card_id):
            latest = self.api.get_card(card_id)
            for attempt in range(2):
                updated = cards.set_cover(latest, media_url)
                updated["cardId"] = updated.get("cardId") or card_id
                self.api.save_card(updated)
                verified = self.api.get_card(card_id)
                actual_url = ((verified.get("metadata") or {}).get("cover") or {}).get("imageL")
                if actual_url == media_url:
                    published = inspect_published_cover(self.api.download_cover(actual_url), path)
                    final_card = self.api.get_card(card_id)
                    final_url = ((final_card.get("metadata") or {}).get("cover") or {}).get("imageL")
                    if final_url == media_url:
                        return {**cards.summarize_card(final_card), "published": published}
                    verified = final_card
                if attempt == 1:
                    raise YotoError("update_not_verified", "Yoto did not retain the playlist cover after one retry")
                # A remote editor can race with this local lock. Wait until two reads agree,
                # then rebuild the update from that latest complete card document.
                for _ in range(10):
                    time.sleep(0.5)
                    latest = self.api.get_card(card_id)
                    if latest == verified:
                        break
                    verified = latest
                else:
                    raise YotoError("update_not_verified", "Playlist is still changing; cover update was not verified")
        raise YotoError("update_not_verified", "Playlist cover could not be verified")

    def _save_and_verify(self, card_id: str, updated: dict[str, Any]) -> dict[str, Any]:
        updated["cardId"] = updated.get("cardId") or card_id
        self.api.save_card(updated)
        verified = self.api.get_card(card_id)
        expected_signature = cards.mutation_signature(updated)
        actual_signature = cards.mutation_signature(verified)
        if actual_signature != expected_signature:
            raise YotoError(
                "update_not_verified",
                "Yoto accepted the update but the refetched card did not contain the requested changes",
                {"expected": expected_signature, "actual": actual_signature},
            )
        return cards.summarize_card(verified)

    def add_song(self, card_id: str, audio_file: str, icon_file: str, title: str | None = None) -> dict[str, Any]:
        info = inspect_mp3(audio_file)
        song_title = (title or info.title).strip()
        require(bool(song_title), "invalid_title", "Song title cannot be empty")
        prepared = prepare_icon(icon_file, song_title)
        # Complete both media operations before changing the card document.
        icon = upload_icon(self.api, prepared)
        audio = upload_audio(self.api, info)
        with self._card_lock(card_id):
            latest = self.api.get_card(card_id)
            updated, key = cards.add_song(
                latest,
                info,
                song_title,
                audio["track_url"],
                icon["icon_url"],
                audio.get("transcoded_info"),
            )
            result = self._save_and_verify(card_id, updated)
        return {
            "song_key": key,
            "title": song_title,
            "audio": audio,
            "icon": {**icon, **prepared.json()},
            "card": result,
        }

    def rename_song(self, card_id: str, song_key: str, title: str) -> dict[str, Any]:
        require(bool(title.strip()), "invalid_title", "Song title cannot be empty")
        with self._card_lock(card_id):
            updated = cards.rename_song(self.api.get_card(card_id), song_key, title.strip())
            return self._save_and_verify(card_id, updated)

    def remove_song(self, card_id: str, song_key: str, confirmed: bool) -> dict[str, Any]:
        require(confirmed, "confirmation_required", "Pass --yes to remove the song")
        with self._card_lock(card_id):
            updated = cards.remove_song(self.api.get_card(card_id), song_key)
            return self._save_and_verify(card_id, updated)

    def move_song(self, card_id: str, song_key: str, position: int) -> dict[str, Any]:
        with self._card_lock(card_id):
            updated = cards.move_song(self.api.get_card(card_id), song_key, position)
            return self._save_and_verify(card_id, updated)

    def set_icon(self, card_id: str, song_key: str, icon_file: str) -> dict[str, Any]:
        with self._card_lock(card_id):
            latest = self.api.get_card(card_id)
            row = next((row for row in cards.song_rows(latest) if row["song_key"] == song_key), None)
            if not row:
                raise YotoError("song_not_found", f"No song has key {song_key}")
            require(
                bool(row["manageable"]),
                "complex_chapter",
                "This chapter does not contain exactly one track and cannot be changed by the song commands",
                {"song_key": song_key, "track_count": row["track_count"]},
            )
            prepared = prepare_icon(icon_file, str(row["title"]))
            icon = upload_icon(self.api, prepared)
            # Fetch again so a concurrent remote edit made during upload is retained.
            latest = self.api.get_card(card_id)
            updated = cards.set_song_icon(latest, song_key, icon["icon_url"])
            card = self._save_and_verify(card_id, updated)
        return {"song_key": song_key, "icon": {**icon, **prepared.json()}, "card": card}
