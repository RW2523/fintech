"""Rendering documents with Chromium, and capturing where each field landed.

Ground truth is read straight from the DOM: every field carries a `data-field`
attribute, so its bounding box comes from the browser rather than from a guess.
That is what makes the extraction accuracy target in T-023 measurable.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

__all__ = ["TEMPLATE_DIR", "RenderResult", "Renderer"]

TEMPLATE_DIR = Path(__file__).parent / "templates"

#: docs/07 §1.1 renders pages at 200 dpi. A browser's CSS pixel is 96 dpi, so
#: the device scale factor makes up the difference. It matters most for the
#: identity card, which is small enough that OCR fails at 96 dpi.
DEVICE_SCALE = 200 / 96

#: A4 at 96 CSS dpi.
_A4 = (794, 1123)
#: The identity card's own page size, 100mm x 64mm.
CARD_VIEWPORT = (378, 242)


@dataclass(slots=True)
class RenderResult:
    png: bytes
    pdf: bytes
    #: field name -> bbox normalised to the page, as [x0, y0, x1, y1]
    boxes: dict[str, list[float]] = field(default_factory=dict)
    width: float = 0.0
    height: float = 0.0


class Renderer:
    """A single Chromium instance, reused across the whole corpus."""

    def __init__(self, template_dir: Path = TEMPLATE_DIR) -> None:
        self._env = Environment(
            loader=FileSystemLoader(template_dir),
            undefined=StrictUndefined,
            autoescape=True,
        )
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._viewport: tuple[int, int] = _A4

    def __enter__(self) -> Renderer:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(args=["--font-render-hinting=none"])
        self._page = self._browser.new_page(
            viewport={"width": 794, "height": 1123},
            device_scale_factor=DEVICE_SCALE,
        )
        return self

    def __exit__(self, *exc: object) -> None:
        for closer in (self._page, self._browser):
            if closer is not None:
                with contextlib.suppress(Exception):
                    closer.close()
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                self._playwright.stop()

    def html(self, template: str, context: dict[str, Any]) -> str:
        return self._env.get_template(template).render(**context)

    def render(
        self,
        template: str,
        context: dict[str, Any],
        *,
        pdf: bool = True,
        viewport: tuple[int, int] | None = None,
    ) -> RenderResult:
        """Render one document and read every field's position from the DOM.

        ``viewport`` sizes the page to the document. The identity card is
        100mm x 64mm, and inside an A4 viewport it occupies too small a share of
        the pixels for OCR to read it.
        """
        assert self._page is not None, "use Renderer as a context manager"
        page = self._page

        wanted = viewport or _A4
        if wanted != self._viewport:
            page.set_viewport_size({"width": wanted[0], "height": wanted[1]})
            self._viewport = wanted

        page.set_content(self.html(template, context), wait_until="load")

        size = page.evaluate(
            "() => ({w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight})"
        )
        width, height = float(size["w"]), float(size["h"])

        raw = page.eval_on_selector_all(
            "[data-field]",
            "els => els.map(e => { const r = e.getBoundingClientRect();"
            " return {f: e.dataset.field, x: r.x + window.scrollX,"
            " y: r.y + window.scrollY, w: r.width, h: r.height}; })",
        )

        boxes: dict[str, list[float]] = {}
        for item in raw:
            name = item["f"]
            if name in boxes:  # repeated fields keep the first occurrence
                continue
            boxes[name] = [
                round(item["x"] / width, 5),
                round(item["y"] / height, 5),
                round((item["x"] + item["w"]) / width, 5),
                round((item["y"] + item["h"]) / height, 5),
            ]

        # "device" honours the device scale factor, which is what puts the page
        # at 200 dpi rather than the browser's 96.
        png = page.screenshot(full_page=True, scale="device", type="png")
        pdf_bytes = page.pdf(format="A4", print_background=True) if pdf else b""

        return RenderResult(png=png, pdf=pdf_bytes, boxes=boxes, width=width, height=height)
