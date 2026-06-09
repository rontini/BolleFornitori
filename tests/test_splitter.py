from bolle.splitter import _boundaries_from_markers, _parse_marker


# --- parsing del marker di pagina --------------------------------------------

def test_parse_marker_pagina_slash():
    assert _parse_marker("Pagina 1/5 Verniciatura Bolognese") == (1, 5)


def test_parse_marker_pag_di_padded():
    # 'Pag 1 di 00002' del formato Camozzi/SOFT: lo zero-padding viene rimosso.
    assert _parse_marker("Pag 1 di 00002") == (1, 2)


def test_parse_marker_spazi():
    assert _parse_marker("Pagina 1 / 5") == (1, 5)


def test_parse_marker_pagina_centrale():
    assert _parse_marker("Pagina 3/5") == (3, 5)


def test_parse_marker_pagina_unica():
    assert _parse_marker("Pagina 1/1") == (1, 1)


def test_parse_marker_assente():
    assert _parse_marker("Documento di trasporto") == (None, None)
    assert _parse_marker("") == (None, None)


# --- calcolo dei confini ------------------------------------------------------

def test_boundaries_singola_bolla():
    # Una sola bolla con marker su pagina 1.
    assert _boundaries_from_markers([0], 17) == [(0, 16)]
    # Anche senza marker il default e' bolla unica (caso produzione).
    assert _boundaries_from_markers([], 17) == [(0, 16)]


def test_boundaries_singola_pagina():
    assert _boundaries_from_markers([0], 1) == [(0, 0)]


def test_boundaries_pdf_di_prova_a_6_bolle():
    # Marker rilevati sulle pagine 1, 6, 8, 10, 11, 16 (1-based)
    # -> indici 0-based: 0, 5, 7, 9, 10, 15
    starts = [0, 5, 7, 9, 10, 15]
    assert _boundaries_from_markers(starts, 17) == [
        (0, 4),    # bolla 1: pagine 1-5
        (5, 6),    # bolla 2: pagine 6-7
        (7, 8),    # bolla 3: pagine 8-9
        (9, 9),    # bolla 4: pagina 10
        (10, 14),  # bolla 5: pagine 11-15
        (15, 16),  # bolla 6: pagine 16-17
    ]


def test_boundaries_inserisce_inizio_se_manca():
    # Se il marker della prima pagina non e' stato letto, includiamo comunque
    # pagina 0 come inizio della prima bolla (conservatori: non perdiamo nulla).
    assert _boundaries_from_markers([5], 10) == [(0, 4), (5, 9)]


def test_boundaries_dedup_e_filtra_out_of_range():
    # Indici duplicati o fuori range vengono filtrati.
    assert _boundaries_from_markers([0, 0, 5, 99], 10) == [(0, 4), (5, 9)]
