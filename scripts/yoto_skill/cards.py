from __future__ import annotations

import copy
import secrets
from typing import Any

from .errors import YotoError, require
from .media import AudioInfo


def chapters(card: dict[str, Any]) -> list[dict[str, Any]]:
    content = card.setdefault("content", {})
    value = content.setdefault("chapters", [])
    require(isinstance(value, list), "invalid_card", "Card chapters are not a list")
    return value


def _track_list(chapter: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = chapter.get("tracks", [])
    return tracks if isinstance(tracks, list) else []


def song_rows(card: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for position, chapter in enumerate(chapters(card), 1):
        tracks = _track_list(chapter)
        rows.append(
            {
                "position": position,
                "song_key": chapter.get("key"),
                "title": chapter.get("title") or (tracks[0].get("title") if tracks else "Untitled"),
                "manageable": len(tracks) == 1,
                "track_count": len(tracks),
                "duration": chapter.get("duration"),
                "icon": (chapter.get("display") or {}).get("icon16x16"),
            }
        )
    return rows


def summarize_card(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "card_id": card.get("cardId") or card.get("id"),
        "title": card.get("title") or "Untitled",
        "description": (card.get("metadata") or {}).get("description"),
        "cover_url": ((card.get("metadata") or {}).get("cover") or {}).get("imageL"),
        "songs": song_rows(card),
    }


def mutation_signature(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Return fields that a song mutation promises to persist."""
    return [
        {
            "key": chapter.get("key"),
            "title": chapter.get("title"),
            "icon": (chapter.get("display") or {}).get("icon16x16"),
            "tracks": [
                {
                    "key": track.get("key"),
                    "title": track.get("title"),
                    "track_url": track.get("trackUrl"),
                    "icon": (track.get("display") or {}).get("icon16x16"),
                }
                for track in _track_list(chapter)
            ],
        }
        for chapter in chapters(card)
    ]


def new_card(title: str, description: str | None = None) -> dict[str, Any]:
    card: dict[str, Any] = {
        "title": title,
        "content": {"playbackType": "linear", "chapters": []},
        "metadata": {"media": {"duration": 0, "fileSize": 0, "readableFileSize": 0.0}},
    }
    if description:
        card["metadata"]["description"] = description
    return card


def set_cover(card: dict[str, Any], image_url: str) -> dict[str, Any]:
    updated = copy.deepcopy(card)
    metadata = updated.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        updated["metadata"] = metadata
    cover = metadata.get("cover")
    if not isinstance(cover, dict):
        cover = {}
        metadata["cover"] = cover
    cover["imageL"] = image_url
    return updated


def _unique_key(card: dict[str, Any]) -> str:
    existing = {str(item.get("key")) for item in chapters(card)}
    while True:
        key = f"song-{secrets.token_hex(5)}"
        if key not in existing:
            return key


def _selected(card: dict[str, Any], song_key: str) -> tuple[int, dict[str, Any], dict[str, Any]]:
    for index, chapter in enumerate(chapters(card)):
        if str(chapter.get("key")) == song_key:
            tracks = _track_list(chapter)
            if len(tracks) != 1:
                raise YotoError(
                    "complex_chapter",
                    "This chapter does not contain exactly one track and cannot be changed by the song commands",
                    {"song_key": song_key, "track_count": len(tracks)},
                )
            return index, chapter, tracks[0]
    raise YotoError("song_not_found", f"No song has key {song_key}")


def recompute(card: dict[str, Any]) -> None:
    total_duration = 0.0
    total_size = 0
    has_streams = False
    for position, chapter in enumerate(chapters(card), 1):
        duration = sum(float(track.get("duration") or 0) for track in _track_list(chapter))
        size = sum(int(track.get("fileSize") or 0) for track in _track_list(chapter))
        chapter["duration"] = round(duration, 3)
        chapter["fileSize"] = size
        chapter["overlayLabel"] = str(position)
        for track_position, track in enumerate(_track_list(chapter), 1):
            if len(_track_list(chapter)) == 1:
                track["overlayLabel"] = str(position)
            elif "overlayLabel" not in track:
                track["overlayLabel"] = str(track_position)
            url = str(track.get("trackUrl") or "")
            has_streams = has_streams or (bool(url) and not url.startswith("yoto:#"))
        total_duration += duration
        total_size += size
    metadata = card.setdefault("metadata", {})
    media = metadata.setdefault("media", {})
    media.update(
        {
            "duration": round(total_duration, 3),
            "fileSize": total_size,
            "readableFileSize": round(total_size / 1024 / 1024, 1),
            "hasStreams": has_streams,
        }
    )


def add_song(
    card: dict[str, Any],
    info: AudioInfo,
    title: str,
    track_url: str,
    icon_url: str,
    transcoded_info: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    updated = copy.deepcopy(card)
    key = _unique_key(updated)
    display = {"icon16x16": icon_url}
    authoritative = transcoded_info or {}
    track = {
        "key": "01",
        "title": title,
        "trackUrl": track_url,
        "duration": float(authoritative.get("duration") or info.duration),
        "fileSize": int(authoritative.get("fileSize") or info.size),
        "format": authoritative.get("format") or "mp3",
        "type": "audio",
        "display": copy.deepcopy(display),
    }
    if authoritative.get("channels") is not None:
        track["channels"] = authoritative["channels"]
    chapters(updated).append(
        {
            "key": key,
            "title": title,
            "tracks": [track],
            "display": copy.deepcopy(display),
        }
    )
    recompute(updated)
    return updated, key


def rename_song(card: dict[str, Any], song_key: str, title: str) -> dict[str, Any]:
    updated = copy.deepcopy(card)
    _, chapter, track = _selected(updated, song_key)
    chapter["title"] = title
    track["title"] = title
    recompute(updated)
    return updated


def remove_song(card: dict[str, Any], song_key: str) -> dict[str, Any]:
    updated = copy.deepcopy(card)
    index, _, _ = _selected(updated, song_key)
    chapters(updated).pop(index)
    recompute(updated)
    return updated


def move_song(card: dict[str, Any], song_key: str, position: int) -> dict[str, Any]:
    updated = copy.deepcopy(card)
    require(1 <= position <= len(chapters(updated)), "invalid_position", "Position is outside the card")
    index, chapter, _ = _selected(updated, song_key)
    chapters(updated).pop(index)
    chapters(updated).insert(position - 1, chapter)
    recompute(updated)
    return updated


def set_song_icon(card: dict[str, Any], song_key: str, icon_url: str) -> dict[str, Any]:
    updated = copy.deepcopy(card)
    _, chapter, track = _selected(updated, song_key)
    if not isinstance(chapter.get("display"), dict):
        chapter["display"] = {}
    if not isinstance(track.get("display"), dict):
        track["display"] = {}
    chapter["display"]["icon16x16"] = icon_url
    track["display"]["icon16x16"] = icon_url
    recompute(updated)
    return updated
