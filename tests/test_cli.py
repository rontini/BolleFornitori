from bolle.cli import _parse_pages


def test_parse_pages_singola():
    assert _parse_pages("1") == [0]


def test_parse_pages_intervallo():
    assert _parse_pages("1-3") == [0, 1, 2]


def test_parse_pages_lista_mista():
    assert _parse_pages("1,5,7") == [0, 4, 6]


def test_parse_pages_vuoto_e_none():
    assert _parse_pages(None) is None
    assert _parse_pages("") is None


def test_flag_reuse_ocr_salta_engine_e_splitter(tmp_path, monkeypatch):
    """--reuse-ocr ricarica il .md gia' presente e non chiama ne' OCR ne' splitter."""
    import json
    from pathlib import Path as _P

    from bolle import cli
    from bolle.api_client import build_api
    from bolle.config import Config

    # Predispone un PDF reale (anche solo 1 pagina vuota) e il .md "gia' prodotto".
    import pytest
    fitz = pytest.importorskip("fitz")
    pdf = tmp_path / "fattura.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf)
    doc.close()
    work = tmp_path / "work"
    (work / "ocr_raw").mkdir(parents=True)
    (work / "ocr_raw" / "fattura.md").write_text(
        "| Nr. | Descrizione | Quantita | U.d.M. |\n"
        "| --- | --- | --- | --- |\n"
        "| 088578.0163 | 99951827 COPERTURA LATERALE | 18 | NR |\n",
        encoding="utf-8",
    )

    chiamato = {"split": False, "ocr_engine_built": False}

    def _no_split(*_a, **_kw):
        chiamato["split"] = True
        return [pdf]

    def _no_engine(*_a, **_kw):
        chiamato["ocr_engine_built"] = True
        raise RuntimeError("non dovrebbe essere costruito con --reuse-ocr")

    # Config con paths che puntano alla tmp_path, backend memory.
    cfg = Config()
    cfg.paths.work = work
    cfg.paths.review_queue = work / "revisione"
    cfg.api.backend = "memory"

    monkeypatch.setattr(cli, "split_pdf", _no_split)
    monkeypatch.setattr(cli, "Config", type("C", (), {"load": staticmethod(lambda _: cfg)}))
    monkeypatch.setattr(cli, "build_api", build_api)
    monkeypatch.setattr("bolle.ocr.build_engine", _no_engine)

    rc = cli.main(["--reuse-ocr", str(pdf)])

    assert rc == 0
    assert chiamato["split"] is False, "splitter non doveva essere chiamato"
    assert chiamato["ocr_engine_built"] is False, "engine OCR non doveva essere costruito"
    # Il parser ha letto il .md e prodotto l'output completo.
    out = json.loads((work / "output" / "fattura.json").read_text(encoding="utf-8"))
    assert out["righe"][0]["codice_letto"] == "088578.0163"
    assert out["righe"][0]["codice_commerciale"] == "99951827"
    assert out["righe"][0]["quantita"] == "18"


def test_flag_fornitore_override_vince_su_estrazione(tmp_path, monkeypatch):
    """--fornitore "NOME" sovrascrive qualunque cosa estratta dall'OCR."""
    import json
    import pytest

    from bolle import cli
    from bolle.api_client import build_api
    from bolle.config import Config

    fitz = pytest.importorskip("fitz")
    pdf = tmp_path / "fattura.pdf"
    doc = fitz.open(); doc.new_page(); doc.save(pdf); doc.close()
    work = tmp_path / "work"
    (work / "ocr_raw").mkdir(parents=True)
    (work / "ocr_raw" / "fattura.md").write_text(
        "| Nr. | Descrizione | Quantita | U.d.M. |\n"
        "| --- | --- | --- | --- |\n"
        "| 088578.0163 | 99951827 COPERTURA | 18 | NR |\n",
        encoding="utf-8",
    )

    cfg = Config(); cfg.paths.work = work; cfg.paths.review_queue = work / "revisione"
    cfg.api.backend = "memory"

    monkeypatch.setattr(cli, "Config", type("C", (), {"load": staticmethod(lambda _: cfg)}))
    monkeypatch.setattr(cli, "build_api", build_api)

    cli.main(["--reuse-ocr", "--fornitore", "ACME SPA", str(pdf)])

    out = json.loads((work / "output" / "fattura.json").read_text(encoding="utf-8"))
    assert out["testata"]["fornitore"] == "ACME SPA"


def test_flag_no_split_disattiva_lo_splitter(tmp_path, monkeypatch):
    # Quando l'utente passa --no-split lo splitter NON viene mai chiamato:
    # il file arriva diretto alla pipeline. Test su un PDF vuoto fittizio.
    pdf = tmp_path / "bolla.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    from bolle import cli

    chiamato = {"split": False}

    def _no_split_chiamata(*_a, **_kw):
        chiamato["split"] = True
        return [pdf]

    class _PipelineFinta:
        def __init__(self, *_a, **_kw): pass
        def process(self, *_a, **_kw):
            from bolle.models import EsitoRiconciliazione
            return EsitoRiconciliazione(numero_ordine=None)

    monkeypatch.setattr(cli, "split_pdf", _no_split_chiamata)
    monkeypatch.setattr(cli, "Pipeline", _PipelineFinta)

    cli.main(["--no-split", str(pdf)])
    assert chiamato["split"] is False, "lo splitter non doveva essere chiamato con --no-split"
