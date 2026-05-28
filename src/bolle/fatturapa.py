"""Parser XML FatturaPA -> Bolla.

Nel contesto le XML FatturaPA sono praticamente assenti, ma il router le classifica
e qui le trasformiamo in una Bolla con lo stesso schema degli altri canali, cosi da
riusare validazione e riconciliazione deterministiche senza casi speciali a valle.

I namespace XML vengono ignorati (si ragiona sui local-name) per robustezza rispetto
ai prefissi usati dai diversi gestionali.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import Bolla, DocumentKind, RigaBolla, Testata


def parse(path: str | Path) -> Bolla:
    p = Path(path)
    root = ET.parse(p).getroot()
    bolla = Bolla(documento_id=p.stem, kind=DocumentKind.XML_FATTURAPA)
    bolla.testata = _testata(root)
    bolla.righe = _righe(root)
    return bolla


def _testata(root: ET.Element) -> Testata:
    t = Testata()

    anagrafica = _find(root, "FatturaElettronicaHeader", "CedentePrestatore", "DatiAnagrafici", "Anagrafica")
    if anagrafica is not None:
        t.fornitore = _testo(_find(anagrafica, "Denominazione")) or _nome_cognome(anagrafica)

    body = _find(root, "FatturaElettronicaBody")
    if body is not None:
        dgd = _find(body, "DatiGenerali", "DatiGeneraliDocumento")
        if dgd is not None:
            t.numero_bolla = _testo(_find(dgd, "Numero"))
            t.data_bolla = _data(_testo(_find(dgd, "Data")))
        t.numero_ordine = _testo(_find(body, "DatiGenerali", "DatiOrdineAcquisto", "IdDocumento"))

    return t


def _righe(root: ET.Element) -> list[RigaBolla]:
    body = _find(root, "FatturaElettronicaBody")
    dbs = _find(body, "DatiBeniServizi") if body is not None else None
    if dbs is None:
        return []

    righe: list[RigaBolla] = []
    for i, linea in enumerate(_findall(dbs, "DettaglioLinee"), start=1):
        codice = _testo(_find(linea, "CodiceArticolo", "CodiceValore"))
        descrizione = _testo(_find(linea, "Descrizione"))
        righe.append(
            RigaBolla(
                numero_riga=_intero(_testo(_find(linea, "NumeroLinea")), default=i),
                codice_letto=codice or descrizione or "",
                descrizione=descrizione,
                quantita=_decimale(_testo(_find(linea, "Quantita"))),
                prezzo_unitario=_decimale(_testo(_find(linea, "PrezzoUnitario"))),
                totale_riga=_decimale(_testo(_find(linea, "PrezzoTotale"))),
            )
        )
    return righe


def _nome_cognome(anagrafica: ET.Element) -> str | None:
    parti = [_testo(_find(anagrafica, n)) for n in ("Nome", "Cognome")]
    parti = [p for p in parti if p]
    return " ".join(parti) if parti else None


# --- helper su local-name (namespace-agnostici) ---

def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(elem: ET.Element | None, *names: str) -> ET.Element | None:
    cur = elem
    for name in names:
        if cur is None:
            return None
        cur = next((c for c in cur if _localname(c.tag) == name), None)
    return cur


def _findall(elem: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in elem if _localname(c.tag) == name]


def _testo(elem: ET.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    return elem.text.strip() or None


def _decimale(text: str | None) -> Decimal | None:
    if not text:
        return None
    try:
        return Decimal(text)  # FatturaPA usa il punto come separatore decimale
    except InvalidOperation:
        return None


def _intero(text: str | None, default: int) -> int:
    try:
        return int(text) if text else default
    except ValueError:
        return default


def _data(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
