from __future__ import annotations

import copy

import pytest

from yoto_skill.cards import add_song, move_song, remove_song, rename_song, set_cover, set_song_icon, song_rows
from yoto_skill.errors import YotoError
from yoto_skill.media import AudioInfo


def audio() -> AudioInfo:
    return AudioInfo("/tmp/song.mp3", "song.mp3", "abc", 4096, 12.5, "Song", "Artist", "Album")


def test_add_preserves_fields_and_recomputes_aggregates(simple_card) -> None:
    original = copy.deepcopy(simple_card)
    updated, key = add_song(simple_card, audio(), "New Song", "yoto:#audio-c", "yoto:#icon-c")
    assert simple_card == original
    assert updated["unknownServerField"] == {"keep": True}
    assert updated["metadata"]["custom"] == "preserved"
    assert updated["metadata"]["media"]["serverHint"] == 7
    assert updated["metadata"]["media"]["duration"] == 42.75
    assert updated["metadata"]["media"]["fileSize"] == 7096
    added = updated["content"]["chapters"][-1]
    assert added["key"] == key
    assert added["title"] == added["tracks"][0]["title"] == "New Song"
    assert added["display"] == added["tracks"][0]["display"]


def test_set_cover_preserves_unknown_metadata_and_chapters(simple_card) -> None:
    original = copy.deepcopy(simple_card)
    updated = set_cover(simple_card, "https://yoto.example/cover.jpg")
    assert simple_card == original
    assert updated["metadata"]["cover"]["imageL"] == "https://yoto.example/cover.jpg"
    assert updated["metadata"]["custom"] == original["metadata"]["custom"]
    assert updated["content"] == original["content"]


def test_rename_move_remove_and_icon(simple_card) -> None:
    renamed = rename_song(simple_card, "song-a", "Renamed")
    assert renamed["content"]["chapters"][0]["title"] == "Renamed"
    assert renamed["content"]["chapters"][0]["tracks"][0]["title"] == "Renamed"
    moved = move_song(renamed, "song-a", 2)
    assert [row["song_key"] for row in song_rows(moved)] == ["song-b", "song-a"]
    assert [c["overlayLabel"] for c in moved["content"]["chapters"]] == ["1", "2"]
    iconed = set_song_icon(moved, "song-a", "yoto:#new-icon")
    chapter = iconed["content"]["chapters"][1]
    assert chapter["display"]["icon16x16"] == "yoto:#new-icon"
    assert chapter["tracks"][0]["display"]["icon16x16"] == "yoto:#new-icon"
    removed = remove_song(iconed, "song-a")
    assert [row["song_key"] for row in song_rows(removed)] == ["song-b"]


@pytest.mark.parametrize("operation", [rename_song, remove_song, move_song, set_song_icon])
def test_complex_chapter_refuses_mutation(simple_card, operation) -> None:
    simple_card["content"]["chapters"][0]["tracks"].append({"key": "02", "title": "Part 2"})
    args = {
        rename_song: ("title",),
        remove_song: (),
        move_song: (2,),
        set_song_icon: ("yoto:#icon",),
    }[operation]
    with pytest.raises(YotoError, match="exactly one track") as error:
        operation(simple_card, "song-a", *args)
    assert error.value.code == "complex_chapter"


def test_complex_chapter_is_listed_as_unmanageable(simple_card) -> None:
    simple_card["content"]["chapters"][0]["tracks"].append({"key": "02"})
    row = song_rows(simple_card)[0]
    assert row["manageable"] is False
    assert row["track_count"] == 2


def test_zero_track_chapter_refuses_with_accurate_error(simple_card) -> None:
    simple_card["content"]["chapters"][0]["tracks"] = []
    with pytest.raises(YotoError, match="exactly one track"):
        rename_song(simple_card, "song-a", "Nope")


def test_icon_can_replace_null_display_objects(simple_card) -> None:
    chapter = simple_card["content"]["chapters"][0]
    chapter["display"] = None
    chapter["tracks"][0]["display"] = None
    updated = set_song_icon(simple_card, "song-a", "yoto:#fixed")
    assert updated["content"]["chapters"][0]["display"]["icon16x16"] == "yoto:#fixed"
