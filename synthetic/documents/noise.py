"""Scan noise (docs/10 §6).

A document that came out of a scanner is rotated a little, slightly blurred and
JPEG-compressed. Extraction has to cope with that, so the generated set looks
like scans rather than like clean renders. One in ten stays clean digital.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
from PIL.Image import Resampling

__all__ = ["ScanProfile", "apply_scan_noise", "clean_digital_share"]

#: docs/10 §6 — a tenth of the corpus is a clean digital original.
clean_digital_share = 0.10


@dataclass(frozen=True, slots=True)
class ScanProfile:
    rotation_degrees: float
    blur_radius: float
    jpeg_quality: int
    brightness: float
    contrast: float

    @classmethod
    def draw(cls, rng: np.random.Generator) -> ScanProfile:
        return cls(
            rotation_degrees=float(rng.uniform(-1.5, 1.5)),
            blur_radius=float(rng.uniform(0.0, 0.8)),
            jpeg_quality=int(rng.integers(70, 91)),
            brightness=float(rng.uniform(0.94, 1.06)),
            contrast=float(rng.uniform(0.94, 1.08)),
        )

    @classmethod
    def clean(cls) -> ScanProfile:
        return cls(0.0, 0.0, 95, 1.0, 1.0)

    @property
    def is_clean(self) -> bool:
        return self.rotation_degrees == 0.0 and self.blur_radius == 0.0


def apply_scan_noise(png_bytes: bytes, profile: ScanProfile) -> bytes:
    """Return a PNG that looks like it came off a scanner."""
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")

    if profile.rotation_degrees:
        image = image.rotate(
            profile.rotation_degrees, resample=Resampling.BICUBIC, expand=False, fillcolor=(255, 255, 255)
        )
    if profile.blur_radius:
        image = image.filter(ImageFilter.GaussianBlur(profile.blur_radius))
    if profile.brightness != 1.0:
        image = ImageEnhance.Brightness(image).enhance(profile.brightness)
    if profile.contrast != 1.0:
        image = ImageEnhance.Contrast(image).enhance(profile.contrast)

    if profile.jpeg_quality < 95:
        # a round trip through JPEG is what actually produces scan artefacts
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=profile.jpeg_quality)
        buffer.seek(0)
        image = Image.open(buffer).convert("RGB")

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
