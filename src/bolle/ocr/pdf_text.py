"""Estrazione diretta da PDF nativi (PyMuPDF), senza OCR.

Per i PDF con layer di testo questo e il percorso preferito: nessun errore di
lettura sui codici/quantita. Le righe di tabella vengono ricostruite dai blocchi
di testo disposti sulla stessa riga (clustering per coordinata Y).
"""

from __future__ import annotations

from pathlib import Path

from .base import OcrResult, TableCell, TableRow

# Tolleranza verticale (punti PDF) per considerare due span sulla stessa riga.
_Y_TOLERANCE = 3.0


def extract(path: str | Path) -> OcrResult:
    import fitz  # PyMuPDF

    rows: list[TableRow] = []
    full_text_parts: list[str] = []

    with fitz.open(path) as doc:
        for page in doc:
            full_text_parts.append(page.get_text("text"))
            words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
            rows.extend(_words_to_rows(words))

    return OcrResult(rows=rows, full_text="\n".join(full_text_parts))


def _words_to_rows(words: list[tuple]) -> list[TableRow]:
    """Raggruppa le parole per riga (Y) e ordina le celle per X."""
    if not words:
        return []

    words = sorted(words, key=lambda w: (round(w[1] / _Y_TOLERANCE), w[0]))
    rows: list[TableRow] = []
    current: list[tuple] = []
    current_y: float | None = None

    for w in words:
        y0 = w[1]
        if current_y is None or abs(y0 - current_y) <= _Y_TOLERANCE:
            current.append(w)
            current_y = y0 if current_y is None else current_y
        else:
            rows.append(_row_from_words(current))
            current = [w]
            current_y = y0
    if current:
        rows.append(_row_from_words(current))
    return rows


def _row_from_words(words: list[tuple]) -> TableRow:
    cells = [TableCell(text=w[4], confidence=1.0) for w in sorted(words, key=lambda w: w[0])]
    return TableRow(cells=cells)
