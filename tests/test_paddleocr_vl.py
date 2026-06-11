from bolle.config import OcrConfig
from bolle.ocr import build_engine
from bolle.ocr.paddleocr_vl import _markdown_da_risultato


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
