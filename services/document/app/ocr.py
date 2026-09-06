"""Tesseract OCR: words, their confidence and where they sit (docs/07 §1.3).

Extraction anchors every value to an OCR word span, which is what gives a field
its bounding box. Without that anchor a value cannot be pointed at on the page,
and an officer cannot check it (docs/09 §3.7).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = ["Line", "Word", "ocr_available", "read_lines", "read_words"]

#: Conda and apt install tessdata in different places; find it once.
_TESSDATA_CANDIDATES = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
)


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    confidence: float
    #: normalised to the page, [x0, y0, x1, y1]
    bbox: tuple[float, float, float, float]
    line: int


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    words: tuple[Word, ...]

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            min(w.bbox[0] for w in self.words),
            min(w.bbox[1] for w in self.words),
            max(w.bbox[2] for w in self.words),
            max(w.bbox[3] for w in self.words),
        )

    @property
    def confidence(self) -> float:
        return sum(w.confidence for w in self.words) / len(self.words)


@lru_cache(maxsize=1)
def _prepare_environment() -> bool:
    """Point Tesseract at its language data. Returns whether OCR is usable."""
    try:
        import pytesseract
    except ImportError:
        return False

    if "TESSDATA_PREFIX" not in os.environ:
        for candidate in _TESSDATA_CANDIDATES:
            if Path(candidate, "eng.traineddata").is_file():
                os.environ["TESSDATA_PREFIX"] = candidate
                break
        else:
            # conda ships it beside the binary
            import shutil

            binary = shutil.which("tesseract")
            if binary:
                guess = Path(binary).resolve().parent.parent / "share" / "tessdata"
                if (guess / "eng.traineddata").is_file():
                    os.environ["TESSDATA_PREFIX"] = str(guess)

    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


def ocr_available() -> bool:
    return _prepare_environment()


def read_words(image: Any, *, min_confidence: float = 30.0) -> list[Word]:
    """Every legible word on the page, with its normalised box."""
    if not _prepare_environment():
        return []

    import pytesseract

    width, height = image.size
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config="--psm 6")

    words: list[Word] = []
    for index in range(len(data["text"])):
        text = (data["text"][index] or "").strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            continue
        if confidence < min_confidence:
            continue

        x, y = data["left"][index], data["top"][index]
        w, h = data["width"][index], data["height"][index]
        words.append(
            Word(
                text=text,
                confidence=confidence / 100.0,
                bbox=(
                    round(x / width, 5),
                    round(y / height, 5),
                    round((x + w) / width, 5),
                    round((y + h) / height, 5),
                ),
                line=data["block_num"][index] * 1000 + data["par_num"][index] * 100 + data["line_num"][index],
            )
        )
    return words


def read_lines(image: Any, *, min_confidence: float = 30.0) -> list[Line]:
    """Words grouped into the lines Tesseract found them on."""
    words = read_words(image, min_confidence=min_confidence)
    grouped: dict[int, list[Word]] = {}
    for word in words:
        grouped.setdefault(word.line, []).append(word)

    lines: list[Line] = []
    for key in sorted(grouped):
        ordered = sorted(grouped[key], key=lambda w: w.bbox[0])
        lines.append(Line(text=" ".join(w.text for w in ordered), words=tuple(ordered)))
    return sorted(lines, key=lambda line: line.bbox[1])
