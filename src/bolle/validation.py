"""Validazione e risoluzione codici.

Due controlli deterministici che fungono anche da validatore implicito dell'OCR:

  1. Risoluzione codice: codice letto -> cross reference (codice fornitore ->
     codice interno); se assente, il fornitore usa gia il codice interno ->
     verifica in anagrafica; se non risolvibile -> coda di revisione.
  2. Verifica aritmetica: qta x prezzo = totale riga (con tolleranza).

Un codice letto ma non presente ne in cross-ref ne in anagrafica e quasi sempre
un errore di lettura: viene segnalato automaticamente.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from .models import Bolla, RigaBolla

_TOLLERANZA_ARITMETICA = Decimal("0.02")


class CodiceResolver(Protocol):
    """Astrae cross reference + anagrafica (servite dalle API aziendali)."""

    def da_cross_reference(self, fornitore: str | None, codice_fornitore: str) -> str | None:
        ...

    def esiste_in_anagrafica(self, codice_interno: str) -> bool:
        ...


def valida_bolla(bolla: Bolla, resolver: CodiceResolver) -> list[RigaBolla]:
    """Risolve i codici e valida l'aritmetica. Ritorna le righe da mandare in revisione."""
    in_revisione: list[RigaBolla] = []
    for riga in bolla.righe:
        _risolvi_codice(riga, bolla.testata.fornitore, resolver)
        _valida_aritmetica(riga)
        if not riga.risolto:
            in_revisione.append(riga)
    return in_revisione


def _risolvi_codice(riga: RigaBolla, fornitore: str | None, resolver: CodiceResolver) -> None:
    # 1) Alcuni fornitori stampano in bolla il NOSTRO codice (colonna 'Vs. CODICE'):
    #    il parser lo ha gia' messo in codice_interno -> riga gia' risolta.
    if riga.codice_interno:
        riga.risolto = True
        riga.note.append("codice interno pre-risolto dalla bolla (Vs. CODICE)")
        return

    # 2) Codice commerciale (es. 99xxxxxx) = NOSTRO codice interno, stampato in
    #    descrizione accanto al nome articolo. E' la chiave preferita: lo
    #    validiamo direttamente in anagrafica (niente cross-reference).
    if riga.codice_commerciale and resolver.esiste_in_anagrafica(riga.codice_commerciale):
        riga.codice_interno = riga.codice_commerciale
        riga.risolto = True
        riga.note.append("risolto via codice commerciale (anagrafica)")
        return

    # 3) Codice fornitore -> cross reference verso il codice interno.
    interno = resolver.da_cross_reference(fornitore, riga.codice_letto)
    if interno is not None:
        riga.codice_interno = interno
        riga.risolto = True
        return

    # 4) Il fornitore potrebbe gia usare il codice interno.
    if resolver.esiste_in_anagrafica(riga.codice_letto):
        riga.codice_interno = riga.codice_letto
        riga.risolto = True
        return

    riga.risolto = False
    if riga.codice_commerciale:
        # Il codice commerciale e' stato letto ma non trovato in anagrafica:
        # o l'anagrafica non e' raggiungibile (backend memory / API giu') o il
        # codice non esiste davvero. NON e' un indizio di errore OCR.
        riga.note.append(
            f"codice commerciale {riga.codice_commerciale} senza riscontro in anagrafica"
        )
    else:
        # Nessun codice commerciale leggibile e nessun riscontro: qui un errore
        # di lettura OCR e' plausibile.
        riga.note.append(
            "codice senza riscontro in cross-reference/anagrafica (possibile errore OCR)"
        )


def _valida_aritmetica(riga: RigaBolla) -> None:
    if riga.quantita is None or riga.prezzo_unitario is None or riga.totale_riga is None:
        return
    atteso = (riga.quantita * riga.prezzo_unitario).quantize(Decimal("0.01"))
    if abs(atteso - riga.totale_riga) > _TOLLERANZA_ARITMETICA:
        riga.note.append(
            f"verifica aritmetica fallita: {riga.quantita} x {riga.prezzo_unitario} "
            f"= {atteso} != totale {riga.totale_riga}"
        )
        riga.risolto = False
