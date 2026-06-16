from decimal import Decimal

from bolle.config import OcrConfig
from bolle.ocr import build_engine
from bolle.ocr.dots_ocr import _delta_from_sse_line, _markdown_to_rows
from bolle.parsing import parse_lines
from bolle.ocr.base import OcrResult


def test_articolo_da_descrizione_estrae_codice_e_nome():
    from bolle.ocr.dots_ocr import _articolo_da_descrizione

    rec = _articolo_da_descrizione(
        "Vs. Ordine Nr. 25402153-OC-00040 del 99951827 COPERTURA LATERALE (VERN)"
    )
    assert rec == ("99951827", "COPERTURA LATERALE (VERN)")


def test_articolo_da_descrizione_si_ferma_al_prossimo_blocco():
    # Quando dopo la descrizione iniziano i metadati, ci fermiamo.
    from bolle.ocr.dots_ocr import _articolo_da_descrizione

    rec = _articolo_da_descrizione(
        "Nr. commessa cliente: 400MAG Rif. Vs DDT 2203 99928399 CARTER LATO ASPIRAZIONE Nr. commessa cliente: 400MAG"
    )
    assert rec == ("99928399", "CARTER LATO ASPIRAZIONE")


def test_paddle_riga_con_codice_mancante_recuperata_da_descrizione():
    # Caso REALE PaddleOCR-VL: la prima riga articolo ha la cella codice vuota
    # ma il commerciale e' visibile in descrizione. Il parser deve recuperarla.
    md = """| Nr. | Descrizione | Quantita | U.d.M. |
| --- | --- | --- | --- |
|  | Ordine 26ODV00156 del 20/01/2026 |  |  |
|  | Vs. Ordine Nr. 25402153-OC-00040 del 99951827 COPERTURA LATERALE (VERN) | 18 | NR |
| 088632.0127 | 99937760 MANIGLIA MYRAY DX VERN. | 32 | NR |
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    per_codice = {r.codice_letto: r for r in righe}
    # Recuperata: codice commerciale + descrizione pulita + quantita' dalla riga
    assert "99951827" in per_codice
    assert per_codice["99951827"].descrizione == "COPERTURA LATERALE (VERN)"
    assert str(per_codice["99951827"].quantita) == "18"
    # Riga con codice colonna valido: NON viene riscritta dal fallback
    assert "088632.0127" in per_codice
    assert str(per_codice["088632.0127"].quantita) == "32"
    # La riga "Ordine 26ODV00156 del..." (senza ne' codice valido ne' commerciale
    # in descrizione) NON deve diventare un articolo.
    assert len(righe) == 2


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


def test_filtro_codice_scarta_tabella_fasulla_da_testata_e_pesca_freetext():
    # Caso reale: la chiamata "testata" produce una tabella Markdown con dati di
    # testata travestiti da articoli; la chiamata "tabella" produce prosa con i
    # veri articoli. Il parser deve rifiutare la prima e pescare la seconda.
    md = """| Nr. | Descrizione | Quantita | U.d.M. |
| :--- | :--- | :--- | :--- |
| 26DGT-01995 | CUIPMENT | 180 ogp F.M. | CUTLCONSAI COOP. |
| | P.IVA | PORTO ASSEGNATO | |

Ordine 26DVDV0156 del 20/01/2026
Vs. Ordine Nr. 26402153-OC-00040 del
99951827 COPERTURA LATERALE (VERN) Nr. commessa cliente: 400MAG Rf. Vs DDT ...
99928399 CARTER LATO ASPIRAZIONE SKEM (NERO) Nr. commessa cliente: 400MAG
99932839 MANIGLIA MYRAX DX VERN. Nr. commessa cliente: 405MAG
99924671 ASS BRACCIO FISSO TAV MED Rf. Vs DDT del 31/03/26
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    codici = [r.codice_letto for r in righe]
    # La riga "26DGT-01995" NON deve comparire (non e' un codice articolo).
    assert "26DGT-01995" not in codici
    # I 4 articoli veri devono esserci tutti.
    assert codici == ["99951827", "99928399", "99932839", "99924671"]


def test_codici_dentro_frasi_non_diventano_articoli():
    # Numeri a 8 cifre dentro frasi tipo "Vs. Ordine Nr. OC/26404399" o
    # "Rf. Vs DDT 26450414 del 09/02/26" NON devono essere catturati come
    # codici articolo: solo i codici a INIZIO RIGA sono righe articolo vere.
    md = """Vs. Ordine Nr. OC/26404399 del
Rf. Vs DDT 26450414 del 09/02/26 - ACCONTO
Vs. Ordine Nr. OC/26407264 del
99951827 COPERTURA LATERALE (VERN) Nr. commessa cliente: 400MAG
99928399 CARTER LATO ASPIRAZIONE SKEM (NERO) Nr. commessa cliente: 400MAG
Vs. Ordine Nr. OC/26412075 del
99924671 ASS BRACCIO FISSO TAV MED Rf. Vs DDT del 31/03/26
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    codici = [r.codice_letto for r in righe]
    # Solo i 3 articoli veri (a inizio riga), nessun numero d'ordine spurio.
    assert codici == ["99951827", "99928399", "99924671"]


def test_codici_in_markdown_pesca_dotted_e_8_cifre():
    from bolle.ocr.dots_ocr import _codici_in_markdown
    md = """088578.0163 Bulloni 18 NR
99951827 COPERTURA LATERALE
| 99928399 | CARTER | 3 NR |
99951827 duplicato ignorato
Vs. Ordine Nr. OC/26404399 del"""
    assert _codici_in_markdown(md) == ["088578.0163", "99951827", "99928399"]


def test_ha_quantita_riconosce_pipe_e_freetext():
    from bolle.ocr.dots_ocr import _ha_quantita
    # Quantita in tabella pipe
    assert _ha_quantita("| 088578.0163 | Bulloni | 18 | NR |") is True
    # Quantita in testo libero
    assert _ha_quantita("088578.0163 Bulloni 18 NR") is True
    # Senza quantita (solo descrizione)
    assert _ha_quantita("99951827 COPERTURA LATERALE (VERN)") is False


def test_seconda_passata_patcha_le_quantita():
    # Simulo l'output della 2a passata che il modello aggiunge in fondo.
    md = """| Nr. | Descrizione | Quantita | U.d.M. |
| --- | --- | --- | --- |
| 99951827 | COPERTURA LATERALE (VERN) | | |
| 99928399 | CARTER LATO ASPIRAZIONE | | |

QTA:99951827=18
QTA:99928399=3
"""
    from bolle.ocr.dots_ocr import parse_articoli
    righe = parse_articoli(md)
    assert [r.codice_letto for r in righe] == ["99951827", "99928399"]
    assert [str(r.quantita) for r in righe] == ["18", "3"]


def test_codice_articolo_filtro_rifiuta_partita_iva_11_cifre():
    # P.IVA e Codice Fiscale italiani sono 11 cifre: NON sono codici articolo.
    from bolle.ocr.dots_ocr import _is_codice_articolo
    assert _is_codice_articolo("088578.0163") is True
    assert _is_codice_articolo("99951827") is True   # 8 cifre, codice commerciale
    assert _is_codice_articolo("00497971200") is False  # 11 cifre = P.IVA
    assert _is_codice_articolo("00293150371") is False  # 11 cifre = cod. fiscale
    assert _is_codice_articolo("26DGT-01995") is False  # alfanumerico = numero DDT


def test_caso_reale_pagina_1_con_2a_passata_per_posizione():
    # Output reale: prima la finta tabella di testata (rifiutata dal filtro
    # codice perche' 00497971200 e' P.IVA), poi gli articoli in prosa, poi la
    # 2a passata in formato libero. I codici della 2a passata sono diversi
    # (lato fornitore) ma in stesso numero/ordine -> patch per posizione.
    md = """| Nr. | Descrizione | Quantita | U.d.M. |
| :--- | :--- | :--- | :--- |
| 26DGT-01995 | CeFLA S.C. MEDICAL EQUIPMENT | 180 ogp F.M. | CUTLCONSAI |
| 00497971200 | P.IVA | PORTO ASSEGNATO | |
| 00293150371 | Codice Faciale | Vettore | |

Ordine 26DVDV0156 del 20/01/2026
Vs. Ordine Nr. 26402153-OC-00040 del
99951827 COPERTURA LATERALE (VERN)
Nr. commessa cliente: 400MAG
99928399 CARTER LATO ASPIRAZIONE SKEM (NERO)
99932839 MANIGLIA MYRAX DX VERN.
99924671 ASS BRACCIO FISSO TAV MED

Nr. Descrizione Quantita U.d.M.
088578.0163 18 NR
088608.0401 3 NR
088632.0127 32 NR
088553.0077 19 NR
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    codici = [r.codice_letto for r in righe]
    quantita = [str(r.quantita) for r in righe]

    # I codici letti sono quelli della prima passata (commerciali, gestionale)
    assert codici == ["99951827", "99928399", "99932839", "99924671"]
    # Le quantita arrivano dalla 2a passata per posizione
    assert quantita == ["18", "3", "32", "19"]


def test_qta_patch_per_codice_quando_2a_passata_usa_stessi_codici():
    md = """99951827 COPERTURA LATERALE (VERN) Nr. commessa cliente: 400MAG
99928399 CARTER LATO ASPIRAZIONE SKEM (NERO)

QTA:99951827=18
QTA:99928399=3
"""
    from bolle.ocr.dots_ocr import parse_articoli
    righe = parse_articoli(md)
    assert [r.codice_letto for r in righe] == ["99951827", "99928399"]
    assert [str(r.quantita) for r in righe] == ["18", "3"]


def test_parsing_per_pagina_con_formfeed():
    # Pagina 1: tabella fasulla + articoli in prosa + 2a passata quantita'.
    # Pagina 2: tabella vera con righe-metadati che ripetono il codice.
    # Il \f separa le pagine: tabelle e patch restano locali a ciascuna.
    pag1 = """| Nr. | Descrizione | Quantita | U.d.M. |
| :--- | :--- | :--- | :--- |
| 26DGT-01995 | CeFLA S.C. | 180 ogp F.M. | COOP |
| 00497971200 | P.IVA | PORTO | |

99951827 COPERTURA LATERALE (VERN)
99928399 CARTER LATO ASPIRAZIONE SKEM (NERO)

Nr. Descrizione Quantita U.d.M.
088578.0163 18 NR
088608.0401 3 NR
"""
    pag2 = """| Nr. | Descrizione | Quantita | U.d.M. |
| :--- | :--- | :--- | :--- |
| 088631.0127 | 99937761 MANIGLIA MYRAY SX VERNN | 32 | NR |
| 088631.0127 | Nr. commessa clienta: 405MAG | | |
| 088631.0127 | Rf. Vs DDT 19 del 13/01/26 - ACCONTO | | |
| 088676.0077 | 99922011 PRIMO BRACCIO TAV.ASS | 5 | NR |
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(pag1 + "\n\f\n" + pag2)
    per_codice = {r.codice_letto: r for r in righe}

    # Pagina 1: articoli in prosa con quantita' patchate per posizione.
    assert str(per_codice["99951827"].quantita) == "18"
    assert str(per_codice["99928399"].quantita) == "3"
    # Pagina 2: tabella vera letta con colonne giuste, metadati dedupati.
    assert str(per_codice["088631.0127"].quantita) == "32"
    assert per_codice["088631.0127"].descrizione == "99937761 MANIGLIA MYRAY SX VERNN"
    assert str(per_codice["088676.0077"].quantita) == "5"
    # Numerazione progressiva globale.
    assert [r.numero_riga for r in righe] == [1, 2, 3, 4]


def test_camozzi_inline_vs_codice_pre_risolto():
    md = """CODICE NODELLO
CODICE COMMERCIALE - DESCRISIONE
Va. CODICE
DN
QUANTITA'

Saldo Vs.ord. 26423188-OK del 26.05.2026
1463 5/3-SM-S01/K01 RACORDI RAPIDI 97270158 PZ 200
Orig: IT Comb.nom.: 74122000
M008-RS20/K01 REGOLATORE PER ARIA 97290058 PZ 56
Orig: IT Comb.nom.: 84811005
N008-F03/K01 FILTRO PER ACQUA 97290116 KANBAN CERT PZ 60
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    per_codice = {r.codice_letto: r for r in righe}

    assert set(per_codice) == {"97270158", "97290058", "97290116"}
    # Vs. CODICE = codice interno gia' risolto
    assert per_codice["97270158"].codice_interno == "97270158"
    assert str(per_codice["97270158"].quantita) == "200"
    assert "RACORDI RAPIDI" in per_codice["97270158"].descrizione
    # Annotazione 'KANBAN CERT' fra codice e UM non disturba
    assert str(per_codice["97290116"].quantita) == "60"


def test_camozzi_multiriga_vs_codice():
    md = """40-1028-130007
N08-F04/K01 FILTRO PER ARIA
Orig: IT Comb.nom: 84211925
97290115
PZ
20

40-1028-130006
N08-F03/K01 FILTRO PER ACQUA
Orig: IT Comb.nom: 84211925
97290116
KANBAN CERT
PZ
60
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    per_codice = {r.codice_letto: r for r in righe}

    assert set(per_codice) == {"97290115", "97290116"}
    assert per_codice["97290115"].codice_interno == "97290115"
    assert str(per_codice["97290115"].quantita) == "20"
    assert per_codice["97290115"].descrizione == "N08-F04/K01 FILTRO PER ARIA"
    assert str(per_codice["97290116"].quantita) == "60"


def test_camozzi_annotazione_con_slash_e_cifre():
    # Annotazioni tipo 'CERT.KTW/W2' fra Vs.CODICE e UM non devono rompere il match.
    md = "M008-RS20/K01 REGOLATORE ACQUA 97977500 CERT.KTW/W2 PZ 4\n"
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    assert len(righe) == 1
    assert righe[0].codice_letto == "97977500"
    assert righe[0].codice_interno == "97977500"
    assert str(righe[0].quantita) == "4"


def test_descrizione_ripulita_da_pipe_residue():
    # Righe pipe senza header riconoscibile finiscono nel freetext: le pipe
    # residue non devono sporcare la descrizione.
    md = "| 088631.0127 | MANIGLIA MYRAY SX VERNN | 32 | NR |\n"
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    assert len(righe) == 1
    assert righe[0].descrizione == "MANIGLIA MYRAY SX VERNN"
    assert str(righe[0].quantita) == "32"


def test_codice_riparazione_con_suffisso_lettere():
    # Variante Verniciatura per le riparazioni: 088552RIP.0077.
    md = "088552RIP.0077 ASS.BRACCIO ORIZZ.CRIC. (VERN) 2 NR\n"
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    assert len(righe) == 1
    assert righe[0].codice_letto == "088552RIP.0077"
    assert str(righe[0].quantita) == "2"


def test_codice_doganale_comb_nom_non_e_vs_codice():
    # 'Orig: IT Comb.nom.: 74122000' e righe hallucinate che lo inglobano:
    # il codice doganale non deve diventare un Vs. CODICE risolto.
    md = "1511 6/4-M5/K01 RACCORDI RAPIDI 74122000 Orig: IT Comb.nom. PZ 300\n"
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    assert all(r.codice_letto != "74122000" for r in righe)


def test_codice_a_meta_riga_con_quantita_in_fondo():
    # Output reale (DDT 01961): il modello appiattisce la riga tabella in testo
    # unico. Il codice sta dopo "Descrizione", la quantita' in fondo. Le righe
    # con "Ordine Nr. <numero>" NON devono produrre falsi articoli.
    md = """Descrizione Ordine 28OUV01423 del 25/05/2026 Vs. Ordine Nr. 26439577 SU Del 9003 Rif. Vs DDT 26450760 del 13/03/26 - ACCONTO 1 NR
Descrizione 99934970 supporto craniostato bianco ral 9003 Rif. Vs DDT 26450760 del 13/03/26 - ACCONTO 1 NR
"""
    from bolle.ocr.dots_ocr import parse_articoli

    righe = parse_articoli(md)
    codici = [r.codice_letto for r in righe]
    assert "26439577" not in codici          # numero d'ordine, non articolo
    assert "26450760" not in codici          # numero DDT, non articolo
    assert codici == ["99934970"]
    assert str(righe[0].quantita) == "1"
    assert righe[0].descrizione.startswith("supporto craniostato bianco")


def test_ordini_di_produzione_soft_non_diventano_articoli():
    # Formato SOFT: '26421479 OP U97003102 ...' e' un ordine di produzione.
    md = """26421479 OP U97003102 SED SEG ST 102 BLD ATLANTICO INU 9,000
26422750 OP U97003132
"""
    from bolle.ocr.dots_ocr import parse_articoli

    assert parse_articoli(md) == []


def test_streaming_sse_estrae_il_contenuto():
    # Riga "data: {...}" tipica dello stream OpenAI-compatibile di llama-server.
    line = b'data: {"choices":[{"delta":{"content":"ART"}}]}'
    assert _delta_from_sse_line(line) == "ART"
    # Righe non pertinenti -> None (saltate dall'adapter).
    assert _delta_from_sse_line(b"data: [DONE]") is None
    assert _delta_from_sse_line(b"") is None
    assert _delta_from_sse_line(b": keep-alive") is None
    assert _delta_from_sse_line(b'data: {"choices":[{"delta":{}}]}') is None
