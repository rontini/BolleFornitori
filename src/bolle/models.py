"""Modelli di dominio della pipeline bolle fornitori.

Sono oggetti puri (dataclass), senza dipendenze da OCR, LLM o API aziendali,
così da poter essere usati e testati in isolamento dalla logica deterministica.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class SourceType(str, Enum):
    EMAIL = "email"
    PEC = "pec"
    PORTAL = "portal"


class DocumentKind(str, Enum):
    PDF_TEXT = "pdf_text"          # PDF nativo con layer di testo -> niente OCR
    PDF_SCAN = "pdf_scan"          # scansione -> OCR in modalita tabella
    XML_FATTURAPA = "xml_fatturapa"
    UNKNOWN = "unknown"


@dataclass
class Testata:
    """Campi di testata estratti semanticamente (per significato, non posizione)."""

    numero_ordine: str | None = None
    fornitore: str | None = None
    numero_bolla: str | None = None
    data_bolla: date | None = None


@dataclass
class RigaBolla:
    numero_riga: int
    codice_letto: str                       # codice articolo come letto dall'OCR
    descrizione: str | None = None
    quantita: Decimal | None = None
    prezzo_unitario: Decimal | None = None
    totale_riga: Decimal | None = None
    codice_interno: str | None = None       # risolto via cross reference / anagrafica
    risolto: bool = False
    note: list[str] = field(default_factory=list)


@dataclass
class Bolla:
    documento_id: str
    kind: DocumentKind
    testata: Testata = field(default_factory=Testata)
    righe: list[RigaBolla] = field(default_factory=list)
    source: SourceType | None = None


@dataclass
class RigaOrdine:
    """Riga d'ordine recuperata dai sistemi aziendali per il match."""

    numero_ordine: str
    codice_interno: str
    quantita_attesa: Decimal
    data_consegna: date | None = None
    fornitore: str | None = None


class TipoProposta(str, Enum):
    AMMANCO = "ammanco"
    ECCEDENZA = "eccedenza"
    RIDATAZIONE = "ridatazione"
    SPOSTAMENTO_ORDINE_FUTURO = "spostamento_ordine_futuro"
    REVISIONE = "revisione"


@dataclass
class Proposta:
    tipo: TipoProposta
    codice_interno: str | None = None
    quantita_attesa: Decimal | None = None
    quantita_dichiarata: Decimal | None = None
    dettaglio: str = ""


@dataclass
class EsitoRiconciliazione:
    numero_ordine: str | None
    proposte: list[Proposta] = field(default_factory=list)
    righe_in_revisione: list[RigaBolla] = field(default_factory=list)
