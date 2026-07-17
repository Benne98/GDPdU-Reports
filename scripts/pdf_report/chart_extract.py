"""Extract embedded chart images from openpyxl worksheets (anchor-ordered)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Optional


@dataclass
class SheetImage:
    path: str
    col: int
    row: int
    width_px: int
    height_px: int


def _anchor_col_row(img) -> tuple[int, int]:
    """Best-effort 1-based (col, row) from openpyxl image anchor."""
    anchor = getattr(img, "anchor", None)
    if anchor is None:
        return (1, 1)
    # TwoCellAnchor / OneCellAnchor
    for attr in ("_from", "from", "_from"):
        fr = getattr(anchor, attr, None) if not isinstance(anchor, str) else None
        if fr is not None:
            col = int(getattr(fr, "col", 0) or 0) + 1
            row = int(getattr(fr, "row", 0) or 0) + 1
            return (max(1, col), max(1, row))
    # Marker as string like "D6"
    if isinstance(anchor, str) and anchor:
        from openpyxl.utils.cell import coordinate_from_string, column_index_from_string

        try:
            col_letter, row = coordinate_from_string(anchor)
            return (column_index_from_string(col_letter), int(row))
        except Exception:
            pass
    return (1, 1)


def extract_sheet_images(ws, out_dir: Optional[str] = None) -> list[str]:
    """Paths only, left→right then top→bottom (period order for multi-chart sheets)."""
    return [im.path for im in extract_sheet_images_meta(ws, out_dir=out_dir)]


def extract_sheet_images_meta(ws, out_dir: Optional[str] = None) -> list[SheetImage]:
    images = list(getattr(ws, "_images", None) or [])
    if not images:
        return []

    if out_dir is None:
        out_dir = tempfile.mkdtemp(prefix="ft_pdf_img_")
    os.makedirs(out_dir, exist_ok=True)

    items: list[SheetImage] = []
    for idx, img in enumerate(images):
        try:
            data = _image_bytes(img)
            if not data:
                continue
            ext = _guess_ext(data)
            path = os.path.join(out_dir, f"{_safe_name(ws.title)}_{idx}{ext}")
            with open(path, "wb") as f:
                f.write(data)
            col, row = _anchor_col_row(img)
            # Approximate px from openpyxl width/height (EMU or px)
            w = int(getattr(img, "width", None) or 0) or _png_size(data)[0]
            h = int(getattr(img, "height", None) or 0) or _png_size(data)[1]
            items.append(SheetImage(path=path, col=col, row=row, width_px=w, height_px=h))
        except Exception:
            continue

    items.sort(key=lambda im: (im.row, im.col, -im.width_px * im.height_px))
    return items


def _png_size(data: bytes) -> tuple[int, int]:
    if data.startswith(b"\x89PNG") and len(data) >= 24:
        import struct

        return struct.unpack(">II", data[16:24])
    return (400, 300)


def _image_bytes(img) -> Optional[bytes]:
    if hasattr(img, "_data") and callable(img._data):
        try:
            raw = img._data()
            if isinstance(raw, (bytes, bytearray)):
                return bytes(raw)
        except Exception:
            pass
    path = getattr(img, "path", None)
    if path and isinstance(path, str) and os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read()
    return None


def _guess_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return ".png"


def _safe_name(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(name))
    return (keep or "sheet")[:40]
