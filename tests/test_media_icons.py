from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from yoto_skill.errors import YotoError
from yoto_skill.icons import prepare_icon
from yoto_skill.media import AudioInfo, extract_cover, upload_audio


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


def test_extract_embedded_front_cover(tmp_path, monkeypatch) -> None:
    from io import BytesIO
    from types import SimpleNamespace

    buffer = BytesIO()
    Image.new("RGB", (32, 32), "blue").save(buffer, format="JPEG")
    front = SimpleNamespace(type=3, mime="image/jpeg", data=buffer.getvalue())
    other = SimpleNamespace(type=0, mime="image/jpeg", data=b"not-a-picture")
    monkeypatch.setattr("yoto_skill.media.MP3", lambda _: SimpleNamespace(tags=SimpleNamespace(getall=lambda key: [other, front])))
    monkeypatch.setenv("YOTO_DATA_DIR", str(tmp_path / "data"))
    source = tmp_path / "track.mp3"
    source.write_bytes(b"mp3")
    result = extract_cover(source)
    assert result["mime"] == "image/jpeg"
    assert (result["width"], result["height"]) == (32, 32)
    assert Path(result["path"]).read_bytes() == front.data


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
