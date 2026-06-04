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
