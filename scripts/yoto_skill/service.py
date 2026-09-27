from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import cards
from .api import YotoAPI
from .errors import YotoError, require
from .icons import prepare_icon, upload_icon
from .media import inspect_mp3, upload_audio
from .storage import app_data_dir

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class YotoService:
    def __init__(self, api: YotoAPI) -> None:
        self.api = api

    @contextmanager
    def _card_lock(self, card_id: str) -> Iterator[None]:
        directory = app_data_dir() / "locks"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{card_id}.lock"
        with path.open("a+b") as stream:
            if sys.platform == "win32":
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                for attempt in range(600):
                    stream.seek(0)
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if attempt == 599:
                            raise YotoError("card_lock_timeout", "Could not lock the card for editing")
                        time.sleep(0.1)
            else:
                fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if sys.platform == "win32":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def list_cards(self) -> list[dict[str, Any]]:
        return [cards.summarize_card(card) for card in self.api.list_cards()]

    def show_card(self, card_id: str) -> dict[str, Any]:
        return cards.summarize_card(self.api.get_card(card_id))

    def create_card(self, title: str, description: str | None = None) -> dict[str, Any]:
        require(bool(title.strip()), "invalid_title", "Card title cannot be empty")
        response = self.api.save_card(cards.new_card(title.strip(), description))
        return cards.summarize_card(response) if isinstance(response, dict) else {"created": True, "title": title}

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
