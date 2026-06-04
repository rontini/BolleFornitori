from decimal import Decimal

from bolle.config import OcrConfig
from bolle.ocr import build_engine
from bolle.ocr.dots_ocr import _delta_from_sse_line, _markdown_to_rows
from bolle.parsing import parse_lines
from bolle.ocr.base import OcrResult


def test_factory_costruisce_dots_ocr():
    engine = build_engine(OcrConfig(engine="dots_ocr"))
    assert engine.__class__.__name__ == "DotsOcrEngine"


def test_markdown_table_diventa_righe_parsabili():
    md = """Numero ordine: ORD100  Fornitore: ACME

| codice | descrizione | quantita | prezzo | totale |
| --- | --- | --- | --- | --- |
| ART-100 | Bulloni M8 | 10 | 1,50 | 15,00 |
| ART-200 | Dado inox | 5 | 3,00 | 15,00 |
"""
    rows = _markdown_to_rows(md)
    assert len(rows) == 2

    righe = parse_lines(OcrResult(rows=rows), confidence_threshold=0.8)
    assert len(righe) == 2
    assert righe[0].codice_letto == "ART-100"
    assert righe[0].quantita == Decimal("10")
    assert righe[0].totale_riga == Decimal("15.00")


def test_fallback_senza_tabella_markdown():
    md = "ART-100   Bulloni M8   10   1,50   15,00"
    rows = _markdown_to_rows(md)
    assert len(rows) == 1
    assert rows[0].cells[0].text == "ART-100"


def test_parse_articoli_su_markdown_reale_ddt():
    # Markdown effettivamente prodotto da GLM-OCR su una pagina del DDT.
    md = """| Nr. | Descrizione | Quantita | U.d.M. |
| :--- | :--- | :--- | :--- |
| 088578.0163 | Ordine 26ODV00156 del 20/01/2026 | 18 NR | |
| 088608.0401 | 99928399 CARTER LATO ASPIRAZIONE SKEM (NERO) | 3 NR | |
| 088632.0127 | 99932839 CARTER LATO ASPIRAZIONE SKEM (NERO) | 32 NR | |
| 088553.0077 | 99924671 ASS BRACCIO FISSO TAV MED | 19 NR | |
| Asporto del beni SCATOLA | Nr. di colli 14 | Inizia trasporto 27/05/2026 | 0 |
| Speed. da Vettore | Localita di Resa | Peso lordo 0 | Peso netto 0 |
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    codici = [r.codice_letto for r in righe]
    quantita = [r.quantita for r in righe]

    assert codici == ["088578.0163", "088608.0401", "088632.0127", "088553.0077"]
    assert [str(q) for q in quantita] == ["18", "3", "32", "19"]
    # La riga di colli/peso NON deve finire fra gli articoli.
    assert all("colli" not in (r.descrizione or "").lower() for r in righe)


def test_parse_articoli_tabella_senza_riga_separatrice():
    md = """| Nr. | Descrizione | Quantita | U.d.M. |
| 088578.0163 | Bulloni | 18 NR | |
| 088608.0401 | Carter | 3 NR | |
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    assert [r.codice_letto for r in righe] == ["088578.0163", "088608.0401"]
    assert [str(r.quantita) for r in righe] == ["18", "3"]


def test_parse_articoli_fallback_freetext():
    # Caso in cui il modello scivola in prosa: nessuna tabella a pipe, ma le
    # righe articolo sono comunque riconoscibili dal codice e dalla quantita.
    md = """Documento di trasporto
Nr. Descrizione Quantita U.d.M.
088578.0163 Bulloni M8 18 NR
088608.0401 CARTER LATO ASPIRAZIONE 3 NR
088553.0077 ASS BRACCIO FISSO 19 NR
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    assert [r.codice_letto for r in righe] == ["088578.0163", "088608.0401", "088553.0077"]
    assert [str(r.quantita) for r in righe] == ["18", "3", "19"]


def test_freetext_fallback_codici_a_8_cifre_senza_quantita():
    # Caso peggiore: il modello trascrive solo i codici commerciali (8 cifre) e
    # le descrizioni, omettendo le quantita. Il fallback secondario pesca almeno
    # codici e descrizioni, cosi' non perdiamo le righe articolo.
    md = """Ordine 260DV00156 del 20/01/2026
Vs. Ordine Nr. 26402153-OC-00040 del
99951827 COPERTURA LATERALE (VERN)
Nr. commessa cliente: 400MAG
99928399 CARTER LATO ASPIRAZIONE SKEM (NERO)
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    codici = [r.codice_letto for r in righe]
    assert codici == ["99951827", "99928399"]
    assert all(r.quantita is None for r in righe)
    assert "COPERTURA" in (righe[0].descrizione or "")


def test_freetext_fallback_riga_con_metadati_in_coda():
    # Caso reale: il modello produce per ogni articolo una riga unica con
    # codice + descrizione + metadati ("Nr. commessa...", "Rf. Vs DDT...",
    # "Ordine ... del ...", "Vs. Ordine Nr. ...") tutti concatenati.
    md = """Nr. Descrizione Quantita U.d.M.
Ordine 260DV00156 del 20/01/2026 Vs. Ordine Nr. 26402153-OC-00040 del
99951827 COPERTURA LATERALE (VERN) Nr. commessa cliente: 400MAG Rf. Vs DDT 2203 del 24/09/25 - SALDO Rf. Vs DDT 3956 del 17/10/25 - ACCONTO Ordine 260DV00324 del 04/02/2026 Vs. Ordine Nr. OC/26404399 del
99928399 CARTER LATO ASPIRAZIONE SKEM (NERO) Nr. commessa cliente: 400MAG Rf. Vs DDT 26450414 del 09/02/26 - ACCONTO Ordine 260DV00532 del 24/02/2026 Vs. Ordine Nr. OC/26407264 del
99932839 MANIGLIA MYRAX DX VERN. Nr. commessa cliente: 405MAG Rf. Vs DDT 19 del 13/01/26 - ACCONTO Ordine 260DV00867 del 25/03/2026 Vs. Ordine Nr. OC/26412075 del
99924671 ASS BRACCIO FISSO TAV MED Rf. Vs DDT del 31/03/26 - ACCONTO Ordine 260DV00900 del 27/03/2026 Vs. Ordine Nr. 26412532-OC-00040 del
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    codici = [r.codice_letto for r in righe]
    assert codici == ["99951827", "99928399", "99932839", "99924671"]
    # Le descrizioni devono fermarsi PRIMA di "Nr. commessa" / "Rf. Vs DDT" / etc.
    assert righe[0].descrizione == "COPERTURA LATERALE (VERN)"
    assert righe[1].descrizione == "CARTER LATO ASPIRAZIONE SKEM (NERO)"
    assert righe[2].descrizione == "MANIGLIA MYRAX DX VERN."
    assert righe[3].descrizione == "ASS BRACCIO FISSO TAV MED"


def test_streaming_sse_estrae_il_contenuto():
    # Riga "data: {...}" tipica dello stream OpenAI-compatibile di llama-server.
    line = b'data: {"choices":[{"delta":{"content":"ART"}}]}'
    assert _delta_from_sse_line(line) == "ART"
    # Righe non pertinenti -> None (saltate dall'adapter).
    assert _delta_from_sse_line(b"data: [DONE]") is None
    assert _delta_from_sse_line(b"") is None
    assert _delta_from_sse_line(b": keep-alive") is None
    assert _delta_from_sse_line(b'data: {"choices":[{"delta":{}}]}') is None
