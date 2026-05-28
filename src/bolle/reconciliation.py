"""Motore di riconciliazione - logica deterministica, NON AI.

Confronta le quantita attese (ordine) con quelle dichiarate (bolla) e produce:
  - ammanchi (ordinato e non presente / quantita inferiore),
  - eccedenze (quantita superiore); per le eccedenze cerca ordini aperti dello
    stesso fornitore/articolo con consegna successiva da cui anticipare,
  - ridatazioni (placeholder: quando i tempi reali divergono dal previsto),
  - righe non risolte -> coda di revisione.

L'abbinamento e ancorato sul numero d'ordine presente in bolla (match 1-a-1).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .api_client import AziendaApi
from .models import (
    Bolla,
    EsitoRiconciliazione,
    Proposta,
    RigaBolla,
    RigaOrdine,
    TipoProposta,
)


def riconcilia(bolla: Bolla, api: AziendaApi) -> EsitoRiconciliazione:
    numero_ordine = bolla.testata.numero_ordine
    esito = EsitoRiconciliazione(numero_ordine=numero_ordine)

    risolte = [r for r in bolla.righe if r.risolto and r.codice_interno]
    esito.righe_in_revisione = [r for r in bolla.righe if not r.risolto]

    if not numero_ordine:
        # Senza ancora d'ordine non possiamo abbinare: tutto in revisione.
        esito.proposte.append(
            Proposta(tipo=TipoProposta.REVISIONE, dettaglio="numero d'ordine assente in bolla")
        )
        return esito

    attese = _quantita_per_codice(api.righe_ordine(numero_ordine))
    dichiarate = _quantita_dichiarate(risolte)

    for codice in set(attese) | set(dichiarate):
        att = attese.get(codice, Decimal(0))
        dic = dichiarate.get(codice, Decimal(0))
        if dic < att:
            esito.proposte.append(
                Proposta(
                    tipo=TipoProposta.AMMANCO,
                    codice_interno=codice,
                    quantita_attesa=att,
                    quantita_dichiarata=dic,
                    dettaglio=f"ammanco di {att - dic}",
                )
            )
        elif dic > att:
            esito.proposte.append(_gestisci_eccedenza(api, bolla, codice, att, dic))

    return esito


def _gestisci_eccedenza(
    api: AziendaApi, bolla: Bolla, codice: str, att: Decimal, dic: Decimal
) -> Proposta:
    eccedenza = dic - att
    aperti = [
        o
        for o in api.ordini_aperti(bolla.testata.fornitore, codice)
        if o.numero_ordine != bolla.testata.numero_ordine
    ]
    aperti.sort(key=lambda o: (o.data_consegna is None, o.data_consegna))
    if aperti:
        target = aperti[0]
        return Proposta(
            tipo=TipoProposta.SPOSTAMENTO_ORDINE_FUTURO,
            codice_interno=codice,
            quantita_attesa=att,
            quantita_dichiarata=dic,
            dettaglio=(
                f"eccedenza di {eccedenza}: prevista nell'ordine {target.numero_ordine} "
                f"(consegna {target.data_consegna}); proporre anticipo/spostamento"
            ),
        )
    return Proposta(
        tipo=TipoProposta.ECCEDENZA,
        codice_interno=codice,
        quantita_attesa=att,
        quantita_dichiarata=dic,
        dettaglio=f"eccedenza di {eccedenza} non riconducibile a ordini aperti",
    )


def _quantita_per_codice(righe: list[RigaOrdine]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for r in righe:
        out[r.codice_interno] += r.quantita_attesa
    return dict(out)


def _quantita_dichiarate(righe: list[RigaBolla]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for r in righe:
        if r.codice_interno and r.quantita is not None:
            out[r.codice_interno] += r.quantita
    return dict(out)
