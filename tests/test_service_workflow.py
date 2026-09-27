from __future__ import annotations

import copy
import io
from pathlib import Path

import pytest
from PIL import Image

from yoto_skill.covers import prepare_cover
from yoto_skill.errors import YotoError
from yoto_skill.icons import PreparedIcon
from yoto_skill.media import AudioInfo
from yoto_skill.service import YotoService


class MemoryAPI:
    def __init__(self, card) -> None:
        self.cards = {card["cardId"]: copy.deepcopy(card)}
        self.fail_save = False
        self.cover_bytes = b""

    def list_cards(self):
        return [copy.deepcopy(card) for card in self.cards.values()]

    def get_card(self, card_id):
        return copy.deepcopy(self.cards[card_id])

    def save_card(self, card):
        if self.fail_save:
            raise YotoError("api_error", "mock update failed")
        saved = copy.deepcopy(card)
        card_id = saved.get("cardId") or "created-1"
        saved["cardId"] = card_id
        self.cards[card_id] = saved
        return copy.deepcopy(saved)

    def upload_cover(self, payload):
        self.cover_bytes = payload
        return {"coverImage": {"mediaUrl": "https://yoto.example/cover.jpg"}}

    def download_cover(self, _url):
        return self.cover_bytes


def prepared_cover(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (300, 300), "blue").save(source)
    return prepare_cover(source, root=tmp_path / "prepared")


def test_set_cover_preserves_existing_tracks(tmp_path, monkeypatch, simple_card) -> None:
    monkeypatch.setenv("YOTO_DATA_DIR", str(tmp_path / "data"))
    api = MemoryAPI(simple_card)
    before = copy.deepcopy(api.cards["card-1"]["content"])
    result = YotoService(api).set_cover("card-1", prepared_cover(tmp_path).cover)
    assert result["cover_url"] == "https://yoto.example/cover.jpg"
    assert Path(result["published"]["preview"]).is_file()
    assert api.cards["card-1"]["content"] == before


def test_set_cover_rejects_unprepared_square(tmp_path, monkeypatch, simple_card) -> None:
    monkeypatch.setenv("YOTO_DATA_DIR", str(tmp_path / "data"))
    square = tmp_path / "square.png"
    Image.new("RGB", (300, 300), "blue").save(square)
    with pytest.raises(YotoError) as error:
        YotoService(MemoryAPI(simple_card)).set_cover("card-1", str(square))
    assert error.value.code == "cover_not_prepared"


def test_set_cover_retries_after_concurrent_change(tmp_path, monkeypatch, simple_card) -> None:
    monkeypatch.setenv("YOTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("yoto_skill.service.time.sleep", lambda _: None)

    class ConcurrentAPI(MemoryAPI):
        saves = 0

        def save_card(self, card):
            self.saves += 1
            if self.saves == 1:
                self.cards["card-1"]["title"] = "Concurrent edit"
                return copy.deepcopy(card)
            return super().save_card(card)

    api = ConcurrentAPI(simple_card)
    result = YotoService(api).set_cover("card-1", prepared_cover(tmp_path).cover)
    assert api.saves == 2
    assert result["title"] == "Concurrent edit"
    assert result["cover_url"] == "https://yoto.example/cover.jpg"


def test_set_cover_never_reports_unverified_update(tmp_path, monkeypatch, simple_card) -> None:
    monkeypatch.setenv("YOTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("yoto_skill.service.time.sleep", lambda _: None)

    class IgnoringAPI(MemoryAPI):
        saves = 0

        def save_card(self, card):
            self.saves += 1
            return copy.deepcopy(card)

    api = IgnoringAPI(simple_card)
    with pytest.raises(YotoError) as error:
        YotoService(api).set_cover("card-1", prepared_cover(tmp_path).cover)
    assert error.value.code == "update_not_verified"
    assert api.saves == 2


def test_set_cover_does_not_save_wrong_yoto_conversion(tmp_path, monkeypatch, simple_card) -> None:
    monkeypatch.setenv("YOTO_DATA_DIR", str(tmp_path / "data"))

    class CroppingAPI(MemoryAPI):
        saves = 0

        def download_cover(self, _url):
            stream = io.BytesIO()
            Image.new("RGB", (638, 1011), "black").save(stream, format="PNG")
            return stream.getvalue()

        def save_card(self, card):
            self.saves += 1
            return super().save_card(card)

    api = CroppingAPI(simple_card)
    with pytest.raises(YotoError) as error:
        YotoService(api).set_cover("card-1", prepared_cover(tmp_path).cover)
    assert error.value.code == "cover_publication_unverified"
    assert api.saves == 0


def test_mocked_complete_song_workflow(monkeypatch, tmp_path, simple_card) -> None:
    monkeypatch.setenv("YOTO_DATA_DIR", str(tmp_path / "data"))
    audio = AudioInfo("/tmp/song.mp3", "song.mp3", "hash", 1234, 9.5, "Metadata Title", "Artist", "Album")
    prepared = PreparedIcon("/tmp/source.png", "/tmp/icon.png", "/tmp/preview.png")
    monkeypatch.setattr("yoto_skill.service.inspect_mp3", lambda _: audio)
    monkeypatch.setattr("yoto_skill.service.prepare_icon", lambda *_: prepared)
    monkeypatch.setattr("yoto_skill.service.upload_icon", lambda *_: {"media_id": "icon-new", "icon_url": "yoto:#icon-new"})
    monkeypatch.setattr(
        "yoto_skill.service.upload_audio",
        lambda *_: {"upload_id": "up", "transcoded_sha256": "audio-new", "track_url": "yoto:#audio-new", "deduplicated": True, "transcoded_info": {}},
    )
    api = MemoryAPI(simple_card)
    service = YotoService(api)

    assert len(service.list_cards()) == 1
    added = service.add_song("card-1", "/tmp/song.mp3", "/tmp/source.png", "Third")
    key = added["song_key"]
    assert added["audio"]["deduplicated"] is True
    assert [song["title"] for song in added["card"]["songs"]] == ["First", "Second", "Third"]

    renamed = service.rename_song("card-1", key, "Third Renamed")
    assert renamed["songs"][-1]["title"] == "Third Renamed"
    moved = service.move_song("card-1", key, 1)
    assert moved["songs"][0]["song_key"] == key
    iconed = service.set_icon("card-1", key, "/tmp/new.png")
    assert iconed["card"]["songs"][0]["icon"] == "yoto:#icon-new"
    removed = service.remove_song("card-1", key, True)
    assert [song["song_key"] for song in removed["songs"]] == ["song-a", "song-b"]


def test_create_and_update_failure_do_not_corrupt_existing(monkeypatch, tmp_path, simple_card) -> None:
    monkeypatch.setenv("YOTO_DATA_DIR", str(tmp_path / "data"))
    api = MemoryAPI(simple_card)
    service = YotoService(api)
    created = service.create_card("Bedtime", "Quiet songs")
    assert created["title"] == "Bedtime"
    before = copy.deepcopy(api.cards["card-1"])
    api.fail_save = True
    with pytest.raises(YotoError, match="mock update failed"):
        service.rename_song("card-1", "song-a", "Unsaved")
    assert api.cards["card-1"] == before


def test_remove_requires_explicit_confirmation(monkeypatch, tmp_path, simple_card) -> None:
    monkeypatch.setenv("YOTO_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(YotoError) as error:
        YotoService(MemoryAPI(simple_card)).remove_song("card-1", "song-a", False)
    assert error.value.code == "confirmation_required"


def test_save_must_be_observable_on_refetch(monkeypatch, tmp_path, simple_card) -> None:
    monkeypatch.setenv("YOTO_DATA_DIR", str(tmp_path / "data"))

    class IgnoringAPI(MemoryAPI):
        def save_card(self, card):
            return copy.deepcopy(card)

    with pytest.raises(YotoError) as error:
        YotoService(IgnoringAPI(simple_card)).rename_song("card-1", "song-a", "Ignored")
    assert error.value.code == "update_not_verified"
