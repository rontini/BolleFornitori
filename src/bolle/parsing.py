"""Parsing deterministico delle righe dalla tabella OCR.

Le righe NON passano dall'LLM: si interpretano le celle della tabella OCR mappando
le colonne (codice, descrizione, quantita, prezzo, totale). La mappatura colonne e
una euristica iniziale; in produzione va calibrata sui fornitori reali.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .ocr.base import OcrResult, TableRow
from .models import RigaBolla

# Una cella e "codice" se alfanumerica con almeno una cifra e lunghezza plausibile.
_RE_CODICE = re.compile(r"^[A-Z0-9][A-Z0-9.\-/]{2,}$", re.I)
_RE_NUMERO = re.compile(r"^-?\d{1,3}(?:[.\s]?\d{3})*(?:[.,]\d+)?$")


def parse_lines(ocr: OcrResult, confidence_threshold: float) -> list[RigaBolla]:
    righe: list[RigaBolla] = []
    n = 0
    for row in ocr.rows:
        riga = _row_to_riga(row, numero=n + 1)
        if riga is None:
            continue
        n += 1
        if row.min_confidence() < confidence_threshold:
            riga.note.append(f"confidenza OCR bassa ({row.min_confidence():.2f})")
        righe.append(riga)
    return righe


def _row_to_riga(row: TableRow, numero: int) -> RigaBolla | None:
    texts = [c.text.strip() for c in row.cells if c.text.strip()]
    if not texts:
        return None

    codice = next((t for t in texts if _RE_CODICE.match(t) and any(ch.isdigit() for ch in t)), None)
    if codice is None:
        return None  # riga senza codice -> intestazione/nota, non e una riga merce

    numeri = [_to_decimal(t) for t in texts if _RE_NUMERO.match(t)]
    numeri = [d for d in numeri if d is not None]

    quantita = numeri[0] if numeri else None
    prezzo = numeri[1] if len(numeri) >= 2 else None
    totale = numeri[-1] if len(numeri) >= 3 else None

    descrizione = " ".join(t for t in texts if t != codice and not _RE_NUMERO.match(t)) or None

    return RigaBolla(
        numero_riga=numero,
        codice_letto=codice,
        descrizione=descrizione,
        quantita=quantita,
        prezzo_unitario=prezzo,
        totale_riga=totale,
    )


def _to_decimal(text: str) -> Decimal | None:
    # Normalizza il formato italiano: 1.234,56 -> 1234.56
    norm = text.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return Decimal(norm)
    except InvalidOperation:
        return None
