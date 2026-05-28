"""Router documenti.

Decide come trattare ogni file in ingresso:
  - PDF con layer di testo  -> estrazione diretta (niente OCR, niente errori di lettura)
  - PDF scansione           -> OCR in modalita tabella
  - XML FatturaPA           -> parser dedicato (raro nel contesto)
"""

from __future__ import annotations

from pathlib import Path

from .models import DocumentKind

# Sotto questa soglia di caratteri estraibili consideriamo il PDF una scansione.
_MIN_TEXT_CHARS = 32


def classify(path: str | Path) -> DocumentKind:
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix == ".xml":
        return DocumentKind.XML_FATTURAPA
    if suffix == ".pdf":
        return DocumentKind.PDF_TEXT if _pdf_has_text_layer(p) else DocumentKind.PDF_SCAN
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        return DocumentKind.PDF_SCAN
    return DocumentKind.UNKNOWN


def _pdf_has_text_layer(path: Path) -> bool:
    """True se il PDF contiene testo estraibile (PDF nativo)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        # Senza PyMuPDF non possiamo distinguere: prudenzialmente trattiamo come scan.
        return False

    chars = 0
    with fitz.open(path) as doc:
        for page in doc:
            chars += len(page.get_text("text").strip())
            if chars >= _MIN_TEXT_CHARS:
                return True
    return chars >= _MIN_TEXT_CHARS
