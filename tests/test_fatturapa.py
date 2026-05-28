from decimal import Decimal

from bolle import fatturapa
from bolle.models import DocumentKind

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<p:FatturaElettronica xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2" versione="FPR12">
  <FatturaElettronicaHeader>
    <CedentePrestatore>
      <DatiAnagrafici>
        <Anagrafica>
          <Denominazione>ACME Forniture S.r.l.</Denominazione>
        </Anagrafica>
      </DatiAnagrafici>
    </CedentePrestatore>
  </FatturaElettronicaHeader>
  <FatturaElettronicaBody>
    <DatiGenerali>
      <DatiGeneraliDocumento>
        <Numero>DDT-2026-001</Numero>
        <Data>2026-05-20</Data>
      </DatiGeneraliDocumento>
      <DatiOrdineAcquisto>
        <IdDocumento>ORD100</IdDocumento>
      </DatiOrdineAcquisto>
    </DatiGenerali>
    <DatiBeniServizi>
      <DettaglioLinee>
        <NumeroLinea>1</NumeroLinea>
        <CodiceArticolo>
          <CodiceTipo>FORN</CodiceTipo>
          <CodiceValore>F-1</CodiceValore>
        </CodiceArticolo>
        <Descrizione>Bulloni M8</Descrizione>
        <Quantita>10.00</Quantita>
        <PrezzoUnitario>1.50</PrezzoUnitario>
        <PrezzoTotale>15.00</PrezzoTotale>
      </DettaglioLinee>
      <DettaglioLinee>
        <NumeroLinea>2</NumeroLinea>
        <Descrizione>Trasporto</Descrizione>
        <Quantita>1.00</Quantita>
        <PrezzoUnitario>20.00</PrezzoUnitario>
        <PrezzoTotale>20.00</PrezzoTotale>
      </DettaglioLinee>
    </DatiBeniServizi>
  </FatturaElettronicaBody>
</p:FatturaElettronica>
"""


def test_parse_fatturapa(tmp_path):
    f = tmp_path / "fattura.xml"
    f.write_text(_XML, encoding="utf-8")

    bolla = fatturapa.parse(f)

    assert bolla.kind == DocumentKind.XML_FATTURAPA
    assert bolla.testata.fornitore == "ACME Forniture S.r.l."
    assert bolla.testata.numero_ordine == "ORD100"
    assert bolla.testata.numero_bolla == "DDT-2026-001"
    assert bolla.testata.data_bolla.isoformat() == "2026-05-20"

    assert len(bolla.righe) == 2
    r1 = bolla.righe[0]
    assert r1.codice_letto == "F-1"
    assert r1.quantita == Decimal("10.00")
    assert r1.prezzo_unitario == Decimal("1.50")
    assert r1.totale_riga == Decimal("15.00")

    # Riga senza CodiceArticolo: ripiega sulla descrizione come codice letto.
    assert bolla.righe[1].codice_letto == "Trasporto"
