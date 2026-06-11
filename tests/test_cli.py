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
