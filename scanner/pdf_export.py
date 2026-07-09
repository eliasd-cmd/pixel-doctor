"""Convierte el informe HTML a PDF usando el Chromium de Playwright.

Se ejecuta como subproceso para no chocar con el event loop de Streamlit:

    python3 -m scanner.pdf_export informe.html informe.pdf
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def html_to_pdf(html_path, pdf_path):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(Path(html_path).absolute().as_uri())
        page.pdf(path=str(pdf_path), format="A4", print_background=True,
                 margin={"top": "14mm", "bottom": "14mm",
                         "left": "12mm", "right": "12mm"})
        browser.close()


if __name__ == "__main__":
    html_to_pdf(sys.argv[1], sys.argv[2])
    print(f"[ok] PDF generado en {sys.argv[2]}", file=sys.stderr)
