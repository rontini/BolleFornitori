from bolle.config import LlmConfig
from bolle.header_extractor import extract_header


def _cfg() -> LlmConfig:
    # Disabilitato: usiamo il fallback regex (no Ollama in test).
    return LlmConfig(enabled=False)


def test_estrae_entrambi_gli_ordini_cliente_e_fornitore():
    # Pezzo di testo reale come prodotto dall'OCR sulla bolla del cliente.
    text = (
        "Ordine 26ODV00156 del 20/01/2026\n"
        "Vs. Ordine Nr. 26402153-OC-00040 del 10/01/2026\n"
        "99951827 COPERTURA LATERALE (VERN)\n"
    )
    t = extract_header(text, _cfg())
    # numero_ordine = ordine del cliente (Vs. Ordine) -> usato per il match Oracle
    assert t.numero_ordine == "26402153-OC-00040"
    # numero_ordine_fornitore = ordine interno del fornitore (bare "Ordine")
    assert t.numero_ordine_fornitore == "26ODV00156"


def test_solo_ordine_fornitore_se_manca_vs_ordine():
    text = "Ordine 26ODV00156 del 20/01/2026\nResto della bolla...\n"
    t = extract_header(text, _cfg())
    assert t.numero_ordine is None
    assert t.numero_ordine_fornitore == "26ODV00156"


def test_solo_ordine_cliente_se_manca_quello_interno():
    text = "Vs. Ordine Nr. 26402153-OC-00040 del 10/01/2026\n"
    t = extract_header(text, _cfg())
    assert t.numero_ordine == "26402153-OC-00040"
    assert t.numero_ordine_fornitore is None


def test_ordine_cliente_abbreviato_vs_ord():
    # Formato Camozzi: "Saldo Vs.ord. 26423188-OK del 26.05.2026"
    text = "Saldo Vs.ord. 26423188-OK del 26.05.2026\n"
    t = extract_header(text, _cfg())
    assert t.numero_ordine == "26423188-OK"


def test_riferimento_normativo_dpr_non_inquina_la_testata():
    # "(D.P.R. N. 472 del 14/8/96)" stampato sui DDT NON deve diventare
    # numero bolla 472 / data 2096. La data vera arriva dopo.
    text = (
        "DOCUMENTO DI TRASPORTO (D.P.R. N. 472 del 14/8/96)\n"
        "T.DOC N.DOC DATA\n"
        "CI 1380 27/05/26\n"
    )
    t = extract_header(text, _cfg())
    assert t.numero_bolla != "472"
    assert t.data_bolla is not None
    assert t.data_bolla.isoformat() == "2026-05-27"


def test_data_anni_90_non_diventa_2096():
    # Pivot anno: 96 non deve diventare 2096; in assenza di date plausibili
    # il campo resta vuoto.
    t = extract_header("Rif. legge del 14/8/96\n", _cfg())
    assert t.data_bolla is None


def test_fornitore_riconosciuto_da_ragione_sociale():
    fornitori = [{"pattern": r"Verniciatura\s*Bolognese", "nome": "VERNICIATURA BOLOGNESE S.R.L."}]
    t = extract_header("Verniciatura Bolognese s.r.l.\nIndirizzo...\n", _cfg(), fornitori_noti=fornitori)
    assert t.fornitore == "VERNICIATURA BOLOGNESE S.R.L."


def test_fornitore_riconosciuto_da_numero_bolla():
    # Caso bolla01: la ragione sociale non e' nel .md, ma il numero bolla 26DT-* lo identifica.
    fornitori = [{"pattern": r"26DT-\d+", "nome": "VERNICIATURA BOLOGNESE S.R.L."}]
    md = "CEFLA S.C.\n...\nDocumento di trasporto Nr. 26DT-01995 Data 27/05/2026\n"
    t = extract_header(md, _cfg(), fornitori_noti=fornitori)
    assert t.fornitore == "VERNICIATURA BOLOGNESE S.R.L."


def test_fornitore_primo_pattern_che_matcha_vince():
    fornitori = [
        {"pattern": "SOFT", "nome": "SOFT ITALIA"},
        {"pattern": "Verniciatura", "nome": "VERNICIATURA"},
    ]
    t = extract_header("SOFT Italia S.p.A.\n", _cfg(), fornitori_noti=fornitori)
    assert t.fornitore == "SOFT ITALIA"


def test_fornitore_pattern_malformato_non_crasha():
    fornitori = [{"pattern": "[unclosed", "nome": "ROTTO"}, {"pattern": "ACME", "nome": "ACME"}]
    t = extract_header("ACME srl\n", _cfg(), fornitori_noti=fornitori)
    assert t.fornitore == "ACME"


def test_estrazione_automatica_ragione_sociale_da_testo():
    # Quando il modello trascrive "SOFT Italia S.p.A." la regex generica la
    # cattura senza bisogno di fornitori_noti.
    t = extract_header("SOFT Italia S.p.A.\nVia Roma 1\n", _cfg())
    assert t.fornitore == "SOFT Italia S.P.A"


def test_estrazione_automatica_scarta_il_cliente_CEFLA():
    # CEFLA S.C. nei DDT e' il destinatario, non il fornitore: NON deve essere
    # catturato dalla regex generica (S.C. non e' una qualifica societaria
    # standard, e "cefla" e' nei termini di scarto).
    md = "Indirizzo spedizione\nCEFLA S.C. MEDICAL EQUIPMENT\nVIA ...\n"
    t = extract_header(md, _cfg())
    assert t.fornitore is None


def test_estrazione_automatica_scarta_il_vettore_trasportatore():
    # Caso reale bolla04 (Camozzi): "VETTORE TRASPORTATORE ARCO'S SPEDIZIONI SPA"
    # ARCO'S SPEDIZIONI SPA e' una ragione sociale vera ma e' il vettore del
    # trasporto, non il fornitore della merce -> deve essere scartato.
    md = "DESTINATARIO\nCEFLA S.C.\n...\nVETTORE TRASPORTATORE ARCO'S SPEDIZIONI SPA\n"
    t = extract_header(md, _cfg())
    assert t.fornitore is None  # meglio null che sbagliato


def test_fornitori_noti_vincono_sulla_regex_generica():
    # Se l'utente ha configurato un nome canonico, vince sulla regex generica.
    fornitori = [{"pattern": "Verniciatura", "nome": "VERNICIATURA BOLOGNESE S.R.L."}]
    t = extract_header("Verniciatura Bolognese s.r.l.\n", _cfg(), fornitori_noti=fornitori)
    assert t.fornitore == "VERNICIATURA BOLOGNESE S.R.L."
