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
