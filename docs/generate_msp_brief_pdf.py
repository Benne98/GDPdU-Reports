"""Generate styled PDFs from MSP architecture brief markdown files."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parent
CSS = DOCS / "msp-brief-pdf.css"
SVG = DOCS / "msp-architecture-diagram.svg"
SVG_IMG_PATTERN = "msp-architecture-diagram.svg"

SOURCES = [
    ("msp-architecture-brief.md", "msp-architecture-brief.pdf"),
    ("msp-architecture-brief-en.md", "msp-architecture-brief-en.pdf"),
    ("msp-architecture-brief-en.md", "architecture-finssentials-en.pdf"),
]

HTML_SHELL = """<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def md_to_html(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8")
    title = text.splitlines()[0].lstrip("# ").strip()
    lang = "en" if md_path.stem.endswith("-en") else "de"
    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
    if SVG.exists() and SVG_IMG_PATTERN in body:
        svg = SVG.read_text(encoding="utf-8").strip()
        body = re.sub(
            rf'<p><img alt="[^"]*" src="{re.escape(SVG_IMG_PATTERN)}" /></p>',
            f'<div class="arch-diagram">{svg}</div>',
            body,
        )
    css = CSS.read_text(encoding="utf-8")
    return HTML_SHELL.format(title=title, lang=lang, css=css, body=body)


async def html_to_pdf(html: str, pdf_path: Path) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(device_scale_factor=2)
        await page.set_content(html, wait_until="networkidle")
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            margin={"top": "18mm", "right": "16mm", "bottom": "20mm", "left": "16mm"},
        )
        await browser.close()


def main() -> int:
    if not CSS.exists():
        print(f"Missing stylesheet: {CSS}", file=sys.stderr)
        return 1

    async def run() -> None:
        for src_name, pdf_name in SOURCES:
            md_path = DOCS / src_name
            pdf_path = DOCS / pdf_name
            if not md_path.exists():
                print(f"Skip missing source: {md_path}", file=sys.stderr)
                continue
            html = md_to_html(md_path)
            await html_to_pdf(html, pdf_path)
            print(f"Created {pdf_path}")

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
