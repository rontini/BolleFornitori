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


def test_streaming_sse_estrae_il_contenuto():
    # Riga "data: {...}" tipica dello stream OpenAI-compatibile di llama-server.
    line = b'data: {"choices":[{"delta":{"content":"ART"}}]}'
    assert _delta_from_sse_line(line) == "ART"
    # Righe non pertinenti -> None (saltate dall'adapter).
    assert _delta_from_sse_line(b"data: [DONE]") is None
    assert _delta_from_sse_line(b"") is None
    assert _delta_from_sse_line(b": keep-alive") is None
    assert _delta_from_sse_line(b'data: {"choices":[{"delta":{}}]}') is None
