from decimal import Decimal

from bolle.api_client import InMemoryAziendaApi
from bolle.models import Bolla, DocumentKind, RigaBolla, Testata
from bolle.ocr.base import OcrResult, TableCell, TableRow
from bolle.parsing import parse_lines
from bolle.validation import valida_bolla


class _Resolver:
    def __init__(self, api):
        self._api = api

    def da_cross_reference(self, fornitore, codice):
        return self._api.cross_reference(fornitore, codice)

    def esiste_in_anagrafica(self, codice):
        return self._api.esiste_articolo(codice)


def _row(*texts, conf=1.0):
    return TableRow(cells=[TableCell(text=t, confidence=conf) for t in texts])


def test_codice_commerciale_e_chiave_di_match():
    # Il codice commerciale (99xxxxxx) e' il NOSTRO codice: se esiste in
    # anagrafica la riga e' risolta, anche se il codice fornitore non lo e'.
    api = InMemoryAziendaApi(anagrafica={"99928399"})
    bolla = Bolla(
        documento_id="D",
        kind=DocumentKind.PDF_TEXT,
        testata=Testata(fornitore="VERNICIATURA"),
        righe=[
            RigaBolla(
                1,
                "088608.0401",                  # codice fornitore (non in anagrafica)
                codice_commerciale="99928399",  # nostro codice (in anagrafica)
                quantita=Decimal(15),
            )
        ],
    )
    in_rev = valida_bolla(bolla, _Resolver(api))
    assert in_rev == []
    assert bolla.righe[0].risolto is True
    assert bolla.righe[0].codice_interno == "99928399"


def test_parse_riga_formato_italiano():
    ocr = OcrResult(rows=[_row("ART-100", "Bulloni M8", "10", "1,50", "15,00")])
    righe = parse_lines(ocr, confidence_threshold=0.8)
    assert len(righe) == 1
    r = righe[0]
    assert r.codice_letto == "ART-100"
    assert r.quantita == Decimal("10")
    assert r.prezzo_unitario == Decimal("1.50")
    assert r.totale_riga == Decimal("15.00")


def test_confidenza_bassa_annotata():
    ocr = OcrResult(rows=[_row("ART-100", "x", "10", "1,50", "15,00", conf=0.5)])
    righe = parse_lines(ocr, confidence_threshold=0.8)
    assert any("confidenza" in n for n in righe[0].note)


def test_codice_interno_pre_risolto_salta_cross_reference():
    # Riga con 'Vs. CODICE' dalla bolla (formato Camozzi): gia' risolta,
    # niente cross-reference ne' anagrafica.
    api = InMemoryAziendaApi()  # vuota: cross-ref e anagrafica non risolvono nulla
    bolla = Bolla(
        documento_id="D",
        kind=DocumentKind.PDF_TEXT,
        testata=Testata(fornitore="CAMOZZI"),
        righe=[RigaBolla(1, "97270158", quantita=Decimal(200), codice_interno="97270158")],
    )
    in_rev = valida_bolla(bolla, _Resolver(api))
    assert in_rev == []
    assert bolla.righe[0].risolto is True
    assert any("pre-risolto" in n for n in bolla.righe[0].note)


def test_aritmetica_fallita_manda_in_revisione():
    api = InMemoryAziendaApi(anagrafica={"ART-100"})
    bolla = Bolla(
        documento_id="D",
        kind=DocumentKind.PDF_TEXT,
        testata=Testata(fornitore="ACME"),
        righe=[RigaBolla(1, "ART-100", quantita=Decimal(10), prezzo_unitario=Decimal("1.50"), totale_riga=Decimal("99.00"))],
    )
    in_rev = valida_bolla(bolla, _Resolver(api))
    assert len(in_rev) == 1
    assert any("aritmetica" in n for n in in_rev[0].note)
