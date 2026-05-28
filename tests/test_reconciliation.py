from datetime import date
from decimal import Decimal

from bolle.api_client import InMemoryAziendaApi
from bolle.models import Bolla, DocumentKind, RigaBolla, RigaOrdine, Testata, TipoProposta
from bolle.reconciliation import riconcilia
from bolle.validation import valida_bolla


class _Resolver:
    def __init__(self, api):
        self._api = api

    def da_cross_reference(self, fornitore, codice):
        return self._api.cross_reference(fornitore, codice)

    def esiste_in_anagrafica(self, codice):
        return self._api.esiste_articolo(codice)


def _bolla(righe):
    return Bolla(
        documento_id="DDT1",
        kind=DocumentKind.PDF_TEXT,
        testata=Testata(numero_ordine="ORD100", fornitore="ACME"),
        righe=righe,
    )


def test_ammanco_rilevato():
    api = InMemoryAziendaApi(
        crossref={("ACME", "F-1"): "INT-1"},
        righe_ordine={"ORD100": [RigaOrdine("ORD100", "INT-1", Decimal(10))]},
    )
    bolla = _bolla([RigaBolla(1, "F-1", quantita=Decimal(7))])
    valida_bolla(bolla, _Resolver(api))

    esito = riconcilia(bolla, api)
    ammanchi = [p for p in esito.proposte if p.tipo == TipoProposta.AMMANCO]
    assert len(ammanchi) == 1
    assert ammanchi[0].codice_interno == "INT-1"
    assert ammanchi[0].quantita_attesa - ammanchi[0].quantita_dichiarata == Decimal(3)


def test_eccedenza_su_ordine_futuro():
    api = InMemoryAziendaApi(
        crossref={("ACME", "F-1"): "INT-1"},
        righe_ordine={"ORD100": [RigaOrdine("ORD100", "INT-1", Decimal(10))]},
        ordini_aperti={
            ("ACME", "INT-1"): [RigaOrdine("ORD200", "INT-1", Decimal(5), date(2026, 7, 1), "ACME")]
        },
    )
    bolla = _bolla([RigaBolla(1, "F-1", quantita=Decimal(12))])
    valida_bolla(bolla, _Resolver(api))

    esito = riconcilia(bolla, api)
    spost = [p for p in esito.proposte if p.tipo == TipoProposta.SPOSTAMENTO_ORDINE_FUTURO]
    assert len(spost) == 1
    assert "ORD200" in spost[0].dettaglio


def test_riga_non_risolta_va_in_revisione():
    api = InMemoryAziendaApi(crossref={}, righe_ordine={"ORD100": []})
    bolla = _bolla([RigaBolla(1, "SCONOSCIUTO", quantita=Decimal(1))])
    valida_bolla(bolla, _Resolver(api))

    esito = riconcilia(bolla, api)
    assert len(esito.righe_in_revisione) == 1
    assert not esito.righe_in_revisione[0].risolto
