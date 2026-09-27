from __future__ import annotations

import hashlib
import io
import ipaddress
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont, ImageOps, ImageStat, UnidentifiedImageError

from .errors import YotoError, require
from .storage import app_data_dir

COVER_SIZE = (638, 1011)
PREVIEW_SIZE = (160, 254)
MAX_IMAGE_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class PreparedCover:
    source: str
    cover: str
    preview: str
    source_width: int
    source_height: int
    width: int = COVER_SIZE[0]
    height: int = COVER_SIZE[1]
    part_label: str | None = None
    title: str | None = None

    def json(self) -> dict[str, str | int | None]:
        return asdict(self)


def _public_https(url: str) -> None:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    require(parsed.scheme == "https" and hostname and not parsed.username and not parsed.password,
            "invalid_cover_url", "Cover image URL must be public HTTPS")
    require(hostname != "localhost" and not hostname.endswith(".local"),
            "invalid_cover_url", "Cover image URL must be public HTTPS")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    require(address.is_global, "invalid_cover_url", "Cover image URL must be public HTTPS")


def download_image(url: str, client: httpx.Client | None = None) -> bytes:
    """Read a direct HTTPS image URL without following redirects or unbounded downloads."""
    _public_https(url)
    own_client = client is None
    http = client or httpx.Client(timeout=30, follow_redirects=False)
    try:
        with http.stream("GET", url, follow_redirects=False) as response:
            require(response.status_code == 200, "cover_download_failed",
                    f"Cover image returned HTTP {response.status_code}; use its direct public HTTPS URL")
            length = response.headers.get("Content-Length")
            if length and length.isdigit():
                require(int(length) <= MAX_IMAGE_BYTES, "cover_too_large", "Cover image exceeds 15 MB")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                require(size <= MAX_IMAGE_BYTES, "cover_too_large", "Cover image exceeds 15 MB")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.RequestError as exc:
        raise YotoError("cover_download_failed", "Could not download the cover image", {"exception": type(exc).__name__}) from exc
    finally:
        if own_client:
            http.close()


def _open_source(raw: bytes) -> tuple[Image.Image, str]:
    require(bool(raw) and len(raw) <= MAX_IMAGE_BYTES, "invalid_cover", "Cover source must be a JPEG or PNG under 15 MB")
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            image_format = opened.format
            require(image_format in {"JPEG", "PNG"}, "invalid_cover", "Cover source must be JPEG or PNG")
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise YotoError("invalid_cover", "Cover source is not a readable JPEG or PNG") from exc
    return image, ".jpg" if image_format == "JPEG" else ".png"


def _fit_font(label: str, max_width: int, maximum: int, minimum: int) -> ImageFont.ImageFont:
    for size in range(maximum, minimum - 1, -2):
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", size)
        except OSError:
            font = ImageFont.load_default(size=size)
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        if max(draw.textbbox((0, 0), line, font=font)[2] for line in label.splitlines()) <= max_width:
            return font
    raise YotoError("cover_text_too_long", f"Cover text does not fit: {label}")


def prepare_cover(
    source: bytes | str | Path,
    *,
    part_number: int | None = None,
    part_total: int | None = None,
    header: str | None = None,
    title: str | None = None,
    subtitle: str | None = None,
    background: str = "#172030",
    foreground: str = "#FFFFFF",
    root: Path | None = None,
) -> PreparedCover:
    """Contain the entire source on a portrait canvas before Yoto's centre crop."""
    require((part_number is None) == (part_total is None), "invalid_part", "Provide both part number and total")
    if part_number is not None:
        require(2 <= part_total <= 99 and 1 <= part_number <= part_total,
                "invalid_part", "Part number must be within a total of 2 to 99")
    if isinstance(source, bytes):
        raw = source
    else:
        path = Path(source).expanduser().resolve()
        require(path.is_file(), "cover_not_found", f"Cover source does not exist: {path}")
        raw = path.read_bytes()
    image, extension = _open_source(raw)
    label = f"{part_number}/{part_total}" if part_number is not None else None
    try:
        background_rgb = ImageColor.getrgb(background)
        foreground_rgb = ImageColor.getrgb(foreground)
    except ValueError as exc:
        raise YotoError("invalid_cover_color", "Cover colors must be valid CSS color values") from exc
    options = {"part": label, "header": header, "title": title, "subtitle": subtitle,
               "background": background, "foreground": foreground}
    digest = hashlib.sha256(raw + json.dumps(options, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    destination = (root or app_data_dir() / "prepared-covers") / digest
    destination.mkdir(parents=True, exist_ok=True)
    source_path = destination / f"source{extension}"
    source_path.write_bytes(raw)

    canvas = Image.new("RGB", COVER_SIZE, background_rgb)
    draw = ImageDraw.Draw(canvas)
    captioned = bool(title or subtitle)
    art_box = (590, 590) if captioned else (590, 850 if not (header or label) else 780)
    art_top = 145 if captioned else (135 if header or label else 32)
    fitted = ImageOps.contain(image, art_box, Image.Resampling.LANCZOS)
    art_x = (COVER_SIZE[0] - fitted.width) // 2
    art_y = art_top + (art_box[1] - fitted.height) // 2
    draw.rounded_rectangle((art_x - 3, art_y - 3, art_x + fitted.width + 2, art_y + fitted.height + 2),
                           radius=13, fill=foreground_rgb)
    canvas.paste(fitted, (art_x, art_y))

    if header or label:
        heading = header or "PARTIE DU LIVRE"
        font = _fit_font(heading, 445 if label else 580, 28, 18)
        draw.text((28, 48), heading, font=font, fill=foreground_rgb)
    if label:
        badge = (493, 27, 609, 113)
        draw.rounded_rectangle(badge, radius=18, fill=(246, 212, 104))
        font = _fit_font(label, 100, 58, 38)
        bounds = draw.textbbox((0, 0), label, font=font)
        text_x = (badge[0] + badge[2] - (bounds[2] - bounds[0])) // 2 - bounds[0]
        text_y = (badge[1] + badge[3] - (bounds[3] - bounds[1])) // 2 - bounds[1]
        draw.text((text_x, text_y), label, font=font, fill=(21, 39, 68))
    if captioned:
        title_end = 790
        if title:
            font = _fit_font(title, 574, 52, 28)
            bounds = draw.multiline_textbbox((28, 790), title, font=font, spacing=4)
            title_end = bounds[3]
            draw.multiline_text((28, 790), title, font=font, fill=foreground_rgb, spacing=4)
        if subtitle:
            font = _fit_font(subtitle, 574, 38, 22)
            subtitle_y = max(868, title_end + 15)
            bounds = draw.multiline_textbbox((28, subtitle_y), subtitle, font=font, spacing=4)
            require(bounds[3] <= 969, "cover_text_too_long", "Cover captions exceed the safe lower margin")
            draw.multiline_text((28, subtitle_y), subtitle, font=font, fill=foreground_rgb, spacing=4)
        draw.line((28, 978, 610, 978), fill=foreground_rgb, width=3)

    cover_path = destination / "cover-638x1011.png"
    preview_path = destination / "preview-160x254.png"
    canvas.save(cover_path, format="PNG", optimize=True)
    canvas.resize(PREVIEW_SIZE, Image.Resampling.LANCZOS).save(preview_path, format="PNG")
    return PreparedCover(str(source_path), str(cover_path), str(preview_path), image.width, image.height,
                         part_label=label, title=title)


def inspect_published_cover(raw: bytes, prepared_file: str | Path, root: Path | None = None) -> dict[str, str | int | float]:
    """Save and compare the actual Yoto image with the submitted portrait canvas."""
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            published = opened.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise YotoError("cover_publication_unverified", "Yoto's published cover is not a readable image") from exc
    digest = hashlib.sha256(raw).hexdigest()[:16]
    destination = (root or app_data_dir() / "published-covers") / digest
    destination.mkdir(parents=True, exist_ok=True)
    image_path = destination / "published.png"
    preview_path = destination / "published-preview-160x254.png"
    published.save(image_path, format="PNG", optimize=True)
    actual_small = published.resize(PREVIEW_SIZE, Image.Resampling.LANCZOS)
    actual_small.save(preview_path, format="PNG")
    paths = {"image": str(image_path), "preview": str(preview_path)}
    require(published.size == COVER_SIZE, "cover_publication_unverified",
            f"Yoto published {published.size}, expected {COVER_SIZE}", paths)
    with Image.open(prepared_file) as opened:
        expected = opened.convert("RGB")
    require(expected.size == COVER_SIZE, "cover_not_prepared", "Run media prepare-cover before uploading")
    expected_small = expected.resize(PREVIEW_SIZE, Image.Resampling.LANCZOS)
    difference = ImageChops.difference(actual_small, expected_small)
    mean_difference = sum(ImageStat.Stat(difference).mean) / 3
    header_difference = sum(ImageStat.Stat(difference.crop((0, 0, PREVIEW_SIZE[0], 28))).mean) / 3
    require(mean_difference < 24 and header_difference < 30, "cover_publication_unverified",
            "Yoto's published cover differs from the prepared image; inspect it before reporting success",
            {**paths, "mean_difference": round(mean_difference, 1), "header_difference": round(header_difference, 1)})
    return {"image": str(image_path), "preview": str(preview_path), "width": published.width,
            "height": published.height, "mean_difference": round(mean_difference, 1)}
