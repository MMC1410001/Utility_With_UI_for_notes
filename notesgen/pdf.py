"""Make a PDF from the exported web page.

Rendering the self-contained HTML in headless Chrome beats exporting from
Google Docs on every count: no 10MB Drive export ceiling, no dependency on
having published anything, and the diagrams are already embedded in the page
so they come through as-is.

Google Doc export stays as a fallback for anyone who has the gdocs extra
installed but not a browser.
"""

from __future__ import annotations

from pathlib import Path


class PdfError(RuntimeError):
    pass


def available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def from_html(html_path: Path, out_path: Path) -> Path:
    """Print a local HTML file to PDF using headless Chrome."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PdfError(
            "making a PDF needs a browser:\n"
            "    python3 -m notesgen setup --extra udemy\n"
            "(that installs Playwright, which this shares with the Udemy fetch)"
        ) from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        for channel in ("chrome", None):
            try:
                browser = pw.chromium.launch(headless=True, channel=channel)
                break
            except Exception:  # noqa: BLE001 - real Chrome absent; use bundled
                browser = None
        if browser is None:
            raise PdfError("could not start a browser to render the PDF")

        try:
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="load", timeout=180_000)
            # The navigation sidebar is fixed positioning; in print it would
            # either repeat on every page or cover the text.
            page.add_style_tag(content="#toc{display:none!important}main{margin-left:0!important}")
            page.emulate_media(media="print")
            page.pdf(
                path=str(out_path),
                format="A4",
                print_background=True,
                margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"},
            )
        finally:
            browser.close()

    return out_path
