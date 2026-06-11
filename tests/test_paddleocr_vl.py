from bolle.config import OcrConfig
from bolle.ocr import build_engine
from bolle.ocr.paddleocr_vl import _html_tables_to_pipe, _markdown_da_risultato


class _Res:
    def __init__(self, markdown=None, json_=None):
        if markdown is not None:
            self.markdown = markdown
        if json_ is not None:
            self.json = json_


def test_factory_costruisce_paddleocr_vl():
    engine = build_engine(OcrConfig(engine="paddleocr_vl"))
    assert engine.__class__.__name__ == "PaddleOcrVlEngine"


def test_markdown_stringa_diretta():
    assert _markdown_da_risultato(_Res(markdown="| a | b |")) == "| a | b |"


def test_markdown_dict_con_chiavi_note():
    assert _markdown_da_risultato(_Res(markdown={"markdown_texts": "TESTO"})) == "TESTO"
    assert _markdown_da_risultato(_Res(markdown={"text": "ALTRO"})) == "ALTRO"


def test_markdown_dict_generico_concatena_stringhe():
    out = _markdown_da_risultato(_Res(markdown={"x": "riga1", "y": "riga2", "n": 3}))
    assert "riga1" in out and "riga2" in out


def test_fallback_su_json():
    out = _markdown_da_risultato(_Res(json_={"k": "v"}))
    assert '"k"' in out and '"v"' in out


def test_markdown_callable():
    class ResMetodo:
        def markdown(self):
            return "DA METODO"

    assert _markdown_da_risultato(ResMetodo()) == "DA METODO"


# --- conversione tabelle HTML -> pipe -----------------------------------------

def test_html_table_reale_paddle_convertita_e_parsata():
    # Snippet REALE dall'output PaddleOCR-VL sulla bolla Verniciatura (pag 2).
    html = (
        "<div style=\"text-align: center;\">Documento di trasporto</div>\n"
        "<table border=1 style='margin: auto;'>"
        "<tr><td>Nr.</td><td>Descrizione</td><td>Quantità</td><td>U.d.M.</td></tr>"
        "<tr><td>088631.0127</td><td>99937761 MANIGLIA MYRAY SX VERN.\\nNr. commessa cliente: 405MAG</td>"
        "<td>32</td><td>NR ✓</td></tr>"
        "<tr><td>088076.0077-F</td><td>Nr. commessa cliente: 400MAG\\n99928186 BASE IDRICO COLONNA</td>"
        "<td>5</td><td>NR</td></tr>"
        "<tr><td>088643.0077_F</td><td>99928827 TAVOLETTA INTERN 2024 (EX 99924169)</td>"
        "<td>30</td><td>NR ✓</td></tr>"
        "</table>"
    )
    out = _html_tables_to_pipe(html)
    # La tabella e' diventata pipe-Markdown con separatore.
    assert "| Nr. | Descrizione | Quantità | U.d.M. |" in out
    assert "| --- |" in out
    assert "<table" not in out and "<div" not in out

    # E il parser esistente la digerisce: codici (anche con suffisso) e quantita'.
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(out)
    per_codice = {r.codice_letto: r for r in righe}
    assert set(per_codice) == {"088631.0127", "088076.0077-F", "088643.0077_F"}
    assert str(per_codice["088631.0127"].quantita) == "32"
    assert str(per_codice["088076.0077-F"].quantita) == "5"
    assert str(per_codice["088643.0077_F"].quantita) == "30"


def test_html_testata_non_produce_articoli():
    # La tabella di testata (Nr. 26DT-01995 / Data / Pagina 1/5) convertita in
    # pipe NON deve produrre righe articolo.
    html = (
        "<table><tr><td>Nr. 26DT-01995</td><td>Data 27/05/2026</td>"
        "<td colspan=\"2\">Causale del trasporto\\nRESO LAVORATO</td><td>Pagina 1/5</td></tr>"
        "<tr><td>P.IVA\\nCodice fiscale</td><td colspan=\"2\">00499791200\\n00293150371</td>"
        "<td>Met. spedizione</td><td>PORTO ASSEGNATO</td></tr></table>"
    )
    out = _html_tables_to_pipe(html)
    from bolle.ocr.dots_ocr import parse_articoli

    assert parse_articoli(out) == []
    # Il marker di pagina resta leggibile per lo splitter.
    from bolle.splitter import _parse_marker

    assert _parse_marker(out) == (1, 5)
