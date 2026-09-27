from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from .api import YotoAPI
from .errors import YotoError, require
from .storage import app_data_dir


@dataclass(frozen=True)
class PreparedIcon:
    source: str
    icon: str
    preview: str
    width: int = 16
    height: int = 16
    mode: str = "RGBA"

    def json(self) -> dict[str, Any]:
        return asdict(self)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40] or "song"


def prepare_icon(source: str | Path, title: str, root: Path | None = None) -> PreparedIcon:
    original = Path(source).expanduser().resolve()
    require(original.is_file(), "icon_not_found", f"Icon source does not exist: {original}")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()[:12]
    destination = (root or app_data_dir() / "generated-icons") / f"{_slug(title)}-{digest}"
    destination.mkdir(parents=True, exist_ok=True)
    saved_source = destination / f"source{original.suffix.lower() or '.png'}"
    if original != saved_source:
        shutil.copy2(original, saved_source)
    icon_path = destination / "icon-16.png"
    preview_path = destination / "preview-256.png"
    try:
        with Image.open(original) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA")
            width, height = image.size
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            image = image.crop((left, top, left + side, top + side))
            # Icons are displayed against black on Yoto Players. Composite first so
            # the upload is deterministic, opaque RGBA rather than palette PNG.
            background = Image.new("RGBA", image.size, (0, 0, 0, 255))
            background.alpha_composite(image)
            small = background.convert("RGB").resize((16, 16), Image.Resampling.LANCZOS)
            small = small.quantize(colors=32, method=Image.Quantize.MEDIANCUT).convert("RGBA")
            small.save(icon_path, format="PNG", optimize=True)
            small.resize((256, 256), Image.Resampling.NEAREST).save(preview_path, format="PNG")
    except (UnidentifiedImageError, OSError) as exc:
        raise YotoError("invalid_icon", f"Could not prepare icon image: {original}", str(exc)) from exc
    with Image.open(icon_path) as check:
        require(check.size == (16, 16) and check.mode == "RGBA", "invalid_icon_output", "Icon conversion failed")
    return PreparedIcon(str(saved_source), str(icon_path), str(preview_path))


_DIGITS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
}


def numbered_icon(number: int, root: Path | None = None) -> PreparedIcon:
    """Create a 16x16 flame-and-number icon for a track without a verified chapter title."""
    require(1 <= number <= 999, "invalid_chapter_number", "Track number must be between 1 and 999")
    digits = f"{number:02d}"
    destination = (root or app_data_dir() / "generated-icons") / f"chapter-number-{digits}"
    destination.mkdir(parents=True, exist_ok=True)
    source = destination / "source.png"
    icon_path = destination / "icon-16.png"
    preview_path = destination / "preview-256.png"
    image = Image.new("RGBA", (16, 16), (0, 0, 0, 255))
    draw = ImageDraw.Draw(image)
    for x, y, color in (
        (7, 0, "#ffcd32"), (7, 1, "#ff8c19"), (6, 2, "#dc281e"),
        (7, 2, "#ff8c19"), (8, 2, "#dc281e"), (6, 3, "#dc281e"),
        (7, 3, "#ffcd32"), (8, 3, "#dc281e"),
    ):
        draw.point((x, y), fill=color)
    scale = 2 if len(digits) == 2 else 1
    glyph_width = 3 * scale
    gap = scale
    width = len(digits) * glyph_width + (len(digits) - 1) * gap
    left = (16 - width) // 2
    top = 6 if scale == 2 else 8
    for index, digit in enumerate(digits):
        for row, pixels in enumerate(_DIGITS[digit]):
            for column, pixel in enumerate(pixels):
                if pixel == "1":
                    x = left + index * (glyph_width + gap) + column * scale
                    y = top + row * scale
                    draw.rectangle((x, y, x + scale - 1, y + scale - 1), fill="#ffe270")
    image.save(source, format="PNG", optimize=True)
    image.save(icon_path, format="PNG", optimize=True)
    image.resize((256, 256), Image.Resampling.NEAREST).save(preview_path, format="PNG")
    return PreparedIcon(str(source), str(icon_path), str(preview_path))


def upload_icon(api: YotoAPI, prepared: PreparedIcon) -> dict[str, str]:
    path = Path(prepared.icon)
    response = api.upload_icon(path.name, path.read_bytes())
    icon = response.get("displayIcon", response)
    media_id = icon.get("mediaId") or icon.get("media_id")
    require(bool(media_id), "icon_upload_invalid_response", "Icon upload response has no media ID", response)
    return {"media_id": str(media_id), "icon_url": f"yoto:#{media_id}"}
