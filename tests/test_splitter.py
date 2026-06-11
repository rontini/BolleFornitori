from bolle.splitter import (
    _boundaries_from_markers,
    _fingerprint,
    _parse_marker,
    _starts_from_scan,
)


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


# --- impronta fornitore -------------------------------------------------------

def test_fingerprint_robusta_al_rumore_ocr():
    # Stessa carta intestata trascritta in modi diversi -> stessa impronta.
    a = _fingerprint("Verniciatura Bolognese s.r.l.\nPartita IVA: IT00565371200")
    b = _fingerprint("Verniciatura\nBolognese srl\n\nVerniciatura Bolognese s.r.l.")
    assert a == b == "verniciaturabolognesesrl"


def test_fingerprint_fornitori_diversi():
    soft = _fingerprint("SOFT Italia S.p.A.\nSelle, cuscini bauletti ed accessori")
    zinc = _fingerprint("ZINC-CROM srl\n\nVia Bicocca, 13/C - 40026 Imola (Bo)")
    assert soft != zinc
    assert soft.startswith("softitaliaspa")
    assert zinc.startswith("zinccromsrl")


# --- inizio bolla da marker + impronta ----------------------------------------

def test_starts_dal_pdf_di_prova_reale():
    # Replica del log reale: 17 pagine, marker leggibile solo su Camozzi (11-15),
    # impronte fornitore per il resto.
    vern = "verniciaturabolognesesrl"
    soft = "softitaliaspaselleecusci"
    camozzi = "copiaadusointernodocumen"
    camozzi_seg = "documentoditrasportodpr1"
    zinc = "zinccromsrlviabicocca13c"
    infos = [
        (None, None, vern),        # pag 1   bolla Verniciatura 01995
        (None, None, vern),        # pag 2
        (None, None, vern),        # pag 3
        (None, None, vern),        # pag 4
        (None, None, vern),        # pag 5
        (None, None, soft),        # pag 6   SOFT (impronta cambia -> nuova bolla)
        (None, None, soft),        # pag 7
        (None, None, vern),        # pag 8   Verniciatura 01997 (impronta cambia)
        (None, None, vern),        # pag 9
        (None, None, vern),        # pag 10  Verniciatura 01961 (merge accettato)
        (1, 5, camozzi),           # pag 11  Camozzi (marker 1/5)
        (2, 5, camozzi_seg),       # pag 12  marker 2/5: mai inizio
        (3, 5, camozzi_seg),       # pag 13
        (4, 5, camozzi_seg),       # pag 14
        (5, 5, camozzi_seg),       # pag 15
        (None, None, zinc),        # pag 16  Zinc-Crom (impronta cambia)
        (None, None, zinc),        # pag 17
    ]
    starts = _starts_from_scan(infos)
    assert starts == [5, 7, 10, 15]
    # Con l'inserimento automatico della pagina 0: 5 bolle.
    assert _boundaries_from_markers(starts, 17) == [
        (0, 4),    # Verniciatura 01995 (pagg 1-5)
        (5, 6),    # SOFT (pagg 6-7)
        (7, 9),    # Verniciatura 01997+01961 (pagg 8-10, merge accettato)
        (10, 14),  # Camozzi (pagg 11-15)
        (15, 16),  # Zinc-Crom (pagg 16-17)
    ]


def test_starts_bolla_singola_multipagina_stessa_impronta():
    # Caso produzione: DDT di 3 pagine dello stesso fornitore, marker illeggibile
    # -> nessuno split.
    fp = "fornitorequalunquesrlsed"
    assert _starts_from_scan([(None, None, fp), (None, None, fp), (None, None, fp)]) == []


def test_marker_1_su_n_blocca_falsi_split_da_impronta_variabile():
    # Caso PaddleOCR: il marker '1/5' e' letto sulla prima pagina, ma le
    # impronte delle pagine interne variano (l'OCR dell'header non e' stabile).
    # Il totale del marker dice che le 4 pagine successive sono continuazione:
    # NESSUN falso split anche se l'impronta cambia e il marker non si legge.
    infos = [
        (1, 5, "improntaA"),
        (None, None, "improntaB"),   # diversa, ma attesa come continuazione
        (None, None, "improntaC"),
        (2, 5, "improntaD"),         # marker letto male come 2/5: comunque continuazione
        (None, None, "improntaE"),
        (None, None, "altrofornitore"),  # pag 6: fuori dall'atteso -> nuova bolla
    ]
    assert _starts_from_scan(infos) == [0, 5]
