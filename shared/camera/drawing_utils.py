"""
Drawing Utilities for AI Rehabilitation Assistant.
Provides high-performance, crisp UTF-8 / Vietnamese text rendering
on OpenCV BGR frames using Pillow TrueType fonts with outline stroke,
caching, and automatic fallback.
"""

from __future__ import annotations

import functools
import logging
import os
import unicodedata
from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("DrawingUtils")

__all__ = [
    "draw_vietnamese_text",
    "draw_pill_badge",
    "get_text_size",
    "strip_accents_ascii",
]

# Candidate TrueType fonts available on Linux/Ubuntu and Windows systems
_WIN_FONT_DIR = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
_FONT_CANDIDATES = [
    # Windows Fonts (High quality Vietnamese UTF-8)
    os.path.join(_WIN_FONT_DIR, "arialbd.ttf"),
    os.path.join(_WIN_FONT_DIR, "arial.ttf"),
    os.path.join(_WIN_FONT_DIR, "segoeuib.ttf"),
    os.path.join(_WIN_FONT_DIR, "segoeui.ttf"),
    os.path.join(_WIN_FONT_DIR, "tahomabd.ttf"),
    os.path.join(_WIN_FONT_DIR, "tahoma.ttf"),
    # Linux / Ubuntu Fonts
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]

_ACTIVE_FONT_PATH: Optional[str] = None
for _cand in _FONT_CANDIDATES:
    if os.path.exists(_cand):
        _ACTIVE_FONT_PATH = _cand
        break

_FONT_CACHE: Dict[int, ImageFont.FreeTypeFont] = {}
_PATCH_CACHE: Dict[Tuple[str, int, Tuple[int, int, int], Tuple[int, int, int], int], Tuple[np.ndarray, np.ndarray, int, int]] = {}
_MAX_PATCH_CACHE_SIZE = 256


def strip_accents_ascii(text: str) -> str:
    """Fallback transliterator to remove Vietnamese diacritics for plain ASCII display."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_bytes = nfkd.encode("ASCII", "ignore")
    return ascii_bytes.decode("ascii")


def _get_font(size: int) -> Optional[ImageFont.FreeTypeFont]:
    """Retrieve or load cached TrueType font at specified pixel size."""
    if _ACTIVE_FONT_PATH is None:
        return None
    if size not in _FONT_CACHE:
        try:
            _FONT_CACHE[size] = ImageFont.truetype(_ACTIVE_FONT_PATH, size)
        except Exception as e:
            logger.warning("Could not load TrueType font: %s", e)
            return None
    return _FONT_CACHE[size]


def _get_text_patch(
    text: str,
    font_size: int,
    color_bgr: Tuple[int, int, int],
    stroke_color_bgr: Tuple[int, int, int],
    stroke_width: int,
) -> Optional[Tuple[np.ndarray, np.ndarray, int, int]]:
    """Generate or retrieve cached RGBA raster patch for rendered text."""
    key = (text, font_size, color_bgr, stroke_color_bgr, stroke_width)
    if key in _PATCH_CACHE:
        return _PATCH_CACHE[key]

    font = _get_font(font_size)
    if font is None:
        return None

    # Calculate exact text bounding box including outline stroke
    bbox = font.getbbox(text, stroke_width=stroke_width)
    x_min, y_min, x_max, y_max = bbox
    tw = max(1, x_max - x_min + 6)
    th = max(1, y_max - y_min + 6)

    # Render on transparent RGBA canvas
    img = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Convert BGR to RGBA for Pillow
    rgb_fill = (color_bgr[2], color_bgr[1], color_bgr[0], 255)
    rgb_stroke = (stroke_color_bgr[2], stroke_color_bgr[1], stroke_color_bgr[0], 255)

    d.text(
        (-x_min + 2, -y_min + 2),
        text,
        font=font,
        fill=rgb_fill,
        stroke_width=stroke_width,
        stroke_fill=rgb_stroke,
    )

    arr = np.frombuffer(img.tobytes(), dtype=np.uint8).reshape((th, tw, 4))
    alpha = arr[:, :, 3:4].astype(np.uint16)
    bgr = arr[:, :, :3][:, :, [2, 1, 0]].astype(np.uint16)

    if len(_PATCH_CACHE) >= _MAX_PATCH_CACHE_SIZE:
        _PATCH_CACHE.clear()

    patch_data = (bgr, alpha, tw, th)
    _PATCH_CACHE[key] = patch_data
    return patch_data


def draw_vietnamese_text(
    frame: np.ndarray,
    text: str,
    pos: Tuple[int, int],
    font_size: int = 18,
    color: Tuple[int, int, int] = (255, 255, 255),
    stroke_color: Tuple[int, int, int] = (0, 0, 0),
    stroke_width: int = 2,
) -> None:
    """
    Render beautiful accented Vietnamese text directly onto OpenCV BGR image frame.
    Uses high-speed pre-rasterized alpha blending.
    
    Args:
        frame: OpenCV BGR image array (modified in-place).
        text: Vietnamese Unicode string.
        pos: (x, y) top-left coordinate for text placement.
        font_size: Font size in pixels.
        color: Primary text color in (B, G, R).
        stroke_color: Text outline stroke color in (B, G, R).
        stroke_width: Outline stroke width in pixels.
    """
    if not text:
        return

    patch = _get_text_patch(text, font_size, color, stroke_color, stroke_width)
    if patch is None:
        # Fallback to ASCII transliteration with cv2.putText if TrueType font unavailable
        safe_text = strip_accents_ascii(text)
        scale = max(0.4, font_size / 30.0)
        cv2.putText(frame, safe_text, (pos[0], pos[1] + font_size), cv2.FONT_HERSHEY_SIMPLEX, scale, stroke_color, stroke_width + 1, cv2.LINE_AA)
        cv2.putText(frame, safe_text, (pos[0], pos[1] + font_size), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)
        return

    bgr, alpha, tw, th = patch
    x, y = pos
    hf, wf = frame.shape[:2]

    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(wf, x + tw), min(hf, y + th)
    if x2 <= x1 or y2 <= y1:
        return

    crop_bgr = bgr[: y2 - y1, : x2 - x1]
    crop_alpha = alpha[: y2 - y1, : x2 - x1]

    roi = frame[y1:y2, x1:x2].astype(np.uint16)
    blended = (crop_bgr * crop_alpha + roi * (255 - crop_alpha)) // 255
    frame[y1:y2, x1:x2] = blended.astype(np.uint8)


def get_text_size(text: str, font_size: int = 18, stroke_width: int = 2) -> Tuple[int, int]:
    """Measure exact bounding box dimensions (width, height) of rendered text."""
    font = _get_font(font_size)
    if font is None:
        return len(text) * int(font_size * 0.6), font_size
    bbox = font.getbbox(text, stroke_width=stroke_width)
    return bbox[2] - bbox[0] + 6, bbox[3] - bbox[1] + 6


def draw_pill_badge(
    frame: np.ndarray,
    text: str,
    top_right: Tuple[int, int],
    bg_color: Tuple[int, int, int] = (18, 24, 36),
    border_color: Tuple[int, int, int] = (0, 196, 214),
    text_color: Tuple[int, int, int] = (0, 229, 250),
    font_size: int = 16,
    padding_x: int = 14,
    padding_y: int = 6,
) -> None:
    """Render a modern rounded pill badge anchored at top-right on the frame."""
    tw, th = get_text_size(text, font_size)
    box_w = tw + padding_x * 2
    box_h = th + padding_y * 2

    rx, ry = top_right
    x1 = rx - box_w
    y1 = ry
    x2 = rx
    y2 = ry + box_h

    # Draw dark pill background with border
    cv2.rectangle(frame, (x1, y1), (x2, y2), bg_color, -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), border_color, 1)

    # Render Vietnamese text centered inside badge
    draw_vietnamese_text(
        frame,
        text,
        (x1 + padding_x, y1 + padding_y),
        font_size=font_size,
        color=text_color,
        stroke_color=(0, 0, 0),
        stroke_width=2,
    )
