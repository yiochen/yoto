from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from yoto_skill.covers import COVER_SIZE, PREVIEW_SIZE, download_image, inspect_published_cover, prepare_cover
from yoto_skill.errors import YotoError


def source_with_edge_art() -> bytes:
    image = Image.new("RGB", (300, 300), "white")
    for x in range(24):
        for y in range(300):
            image.putpixel((x, y), (240, 0, 0))
            image.putpixel((299 - x, y), (0, 0, 240))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_square_source_keeps_both_edges_at_card_size(tmp_path) -> None:
    prepared = prepare_cover(source_with_edge_art(), root=tmp_path)
    with Image.open(prepared.cover) as cover:
        assert cover.size == COVER_SIZE
        assert cover.getpixel((40, 505))[0] > 200
        assert cover.getpixel((598, 505))[2] > 200
    with Image.open(prepared.preview) as preview:
        assert preview.size == PREVIEW_SIZE
        assert preview.getpixel((11, 127))[0] > 150
        assert preview.getpixel((149, 127))[2] > 150


def test_parts_get_distinct_corner_badges_without_cropping_source(tmp_path) -> None:
    source = source_with_edge_art()
    first = prepare_cover(source, part_number=1, part_total=3, header="Book 1",
                          title="Volume title", subtitle="Tracks 1–8", root=tmp_path)
    second = prepare_cover(source, part_number=2, part_total=3, header="Book 1",
                           title="Volume title", subtitle="Tracks 9–16", root=tmp_path)
    assert first.part_label == "1/3"
    assert second.part_label == "2/3"
    with Image.open(first.preview) as one, Image.open(second.preview) as two:
        assert one.tobytes() != two.tobytes()
        assert one.getpixel((139, 17))[0] > 180  # yellow corner badge
    with Image.open(first.cover) as cover:
        assert cover.getpixel((30, 440))[0] > 200
        assert cover.getpixel((607, 440))[2] > 200


def test_published_image_is_saved_and_compared(tmp_path) -> None:
    prepared = prepare_cover(source_with_edge_art(), root=tmp_path / "prepared")
    matched = inspect_published_cover(Path(prepared.cover).read_bytes(), prepared.cover, tmp_path / "published")
    assert Path(matched["preview"]).is_file()
    assert (matched["width"], matched["height"]) == COVER_SIZE
    assert matched["mean_difference"] == 0

    wrong = io.BytesIO()
    Image.new("RGB", COVER_SIZE, "black").save(wrong, format="PNG")
    with pytest.raises(YotoError) as error:
        inspect_published_cover(wrong.getvalue(), prepared.cover, tmp_path / "published")
    assert error.value.code == "cover_publication_unverified"
    assert Path(error.value.details["preview"]).is_file()


@pytest.mark.parametrize("url", ["http://publisher.example/cover.jpg", "https://127.0.0.1/cover.jpg"])
def test_source_url_must_be_public_https(url) -> None:
    with pytest.raises(YotoError) as error:
        download_image(url)
    assert error.value.code == "invalid_cover_url"


def test_source_download_rejects_redirects() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(302, headers={"Location": "https://other.example/image.png"})),
        follow_redirects=True,
    )
    with pytest.raises(YotoError) as error:
        download_image("https://publisher.example/cover.png", client)
    assert error.value.code == "cover_download_failed"
