"""Fast persistence for already encoded renderer output."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


NATIVE_JPEG_INFO_KEY = "deckview_encoded_jpeg"


def native_jpeg_bytes(image: Any) -> bytes | None:
    """Return Rust-produced JPEG bytes when they are attached to a PIL image."""
    info = getattr(image, "info", None)
    if not isinstance(info, dict):
        return None
    payload = info.get(NATIVE_JPEG_INFO_KEY)
    if not isinstance(payload, bytes) or len(payload) < 4:
        return None
    if not payload.startswith(b"\xff\xd8") or not payload.endswith(b"\xff\xd9"):
        return None
    return payload


def write_rendered_jpeg(
    image: Any,
    destination: str | os.PathLike[str],
    *,
    quality: int = 92,
    optimize: bool = True,
    progressive: bool = False,
) -> bool:
    """Write a unique render artifact and report whether Rust bytes were reused.

    Render-cache publication remains atomic in ``store_render_cache``. These
    destinations are unique job artifacts, so an extra temporary file and
    ``fsync`` only add latency without protecting a shared filename.
    """
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = native_jpeg_bytes(image)
    if encoded is not None:
        target.write_bytes(encoded)
    else:
        info = getattr(image, "info", {})
        high_resolution = (
            isinstance(info, dict) and info.get("deckview_image_resolution") == "high"
        )
        image.save(
            target,
            format="JPEG",
            quality=95 if high_resolution else quality,
            **({"subsampling": 0} if high_resolution else {}),
            optimize=optimize,
            progressive=progressive,
        )
    return encoded is not None
