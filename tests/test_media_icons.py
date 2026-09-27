from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from yoto_skill.errors import YotoError
from yoto_skill.icons import numbered_icon, prepare_icon
from yoto_skill.media import AudioInfo, upload_audio


def info(tmp_path: Path) -> AudioInfo:
    path = tmp_path / "song.mp3"
    path.write_bytes(b"mp3")
    return AudioInfo(str(path), path.name, "hash", 3, 1.5, "Song", None, None)


def test_icon_conversion_is_exact_rgba_and_pixel_preview(tmp_path) -> None:
    source = tmp_path / "wide.png"
    Image.new("RGB", (120, 60), "red").save(source)
    prepared = prepare_icon(source, "A Song!", tmp_path / "out")
    with Image.open(prepared.icon) as icon:
        assert icon.size == (16, 16)
        assert icon.mode == "RGBA"
    with Image.open(prepared.preview) as preview:
        assert preview.size == (256, 256)
        assert preview.getpixel((8, 8)) == preview.getpixel((15, 15))
    assert Path(prepared.source).exists()


def test_numbered_icon_has_distinct_readable_digits(tmp_path) -> None:
    first = numbered_icon(7, tmp_path)
    second = numbered_icon(38, tmp_path)
    with Image.open(first.icon) as one, Image.open(second.icon) as two:
        assert one.size == two.size == (16, 16)
        assert one.mode == two.mode == "RGBA"
        assert one.tobytes() != two.tobytes()
        assert sum(one.getpixel((x, y))[0] > 180 for y in range(6, 16) for x in range(16)) > 20
    assert Path(first.preview).exists()


@pytest.mark.parametrize("number", [0, 1000])
def test_numbered_icon_rejects_out_of_range(number, tmp_path) -> None:
    with pytest.raises(YotoError, match="between 1 and 999"):
        numbered_icon(number, tmp_path)


class FakeMediaAPI:
    def __init__(self, statuses, upload_url="https://upload.test/file") -> None:
        self.statuses = iter(statuses)
        self.upload_url = upload_url
        self.uploaded = False

    def audio_upload_url(self, _sha, _name):
        return {"uploadId": "up-1", "uploadUrl": self.upload_url}

    def upload_signed(self, _url, payload, content_type):
        assert payload == b"mp3"
        assert content_type == "audio/mpeg"
        self.uploaded = True

    def transcoded_upload(self, _upload_id):
        return next(self.statuses)


def test_audio_upload_polling(tmp_path) -> None:
    api = FakeMediaAPI([{"status": "processing"}, {"transcodedSha256": "done"}])
    ticks = iter([0, 0, 1, 2])
    result = upload_audio(api, info(tmp_path), timeout=10, poll_interval=0, sleeper=lambda _: None, clock=lambda: next(ticks))
    assert result["track_url"] == "yoto:#done"
    assert result["deduplicated"] is False
    assert api.uploaded


def test_deduplicated_audio_skips_signed_put(tmp_path) -> None:
    api = FakeMediaAPI([{"transcodedSha256": "done"}], upload_url=None)
    result = upload_audio(api, info(tmp_path), timeout=10, sleeper=lambda _: None, clock=lambda: 0)
    assert result["deduplicated"] is True
    assert not api.uploaded


def test_transcode_failure_and_timeout(tmp_path) -> None:
    with pytest.raises(YotoError) as failed:
        upload_audio(FakeMediaAPI([{"status": "failed"}], None), info(tmp_path), clock=lambda: 0)
    assert failed.value.code == "transcode_failed"

    ticks = iter([0, 0, 2])
    with pytest.raises(YotoError) as timeout:
        upload_audio(
            FakeMediaAPI([{"status": "processing"}], None),
            info(tmp_path),
            timeout=1,
            poll_interval=0,
            sleeper=lambda _: None,
            clock=lambda: next(ticks),
        )
    assert timeout.value.code == "transcode_timeout"


def test_transcode_not_ready_http_status_is_polled(tmp_path) -> None:
    class PendingAPI(FakeMediaAPI):
        def __init__(self):
            super().__init__([], None)
            self.calls = 0

        def transcoded_upload(self, _upload_id):
            self.calls += 1
            if self.calls == 1:
                raise YotoError("api_error", "not ready", {"status": 404})
            return {"transcodedSha256": "done"}

    ticks = iter([0, 0, 1, 2])
    assert upload_audio(
        PendingAPI(), info(tmp_path), timeout=10, poll_interval=0, sleeper=lambda _: None, clock=lambda: next(ticks)
    )["track_url"] == "yoto:#done"
