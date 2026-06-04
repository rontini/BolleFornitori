import json
from pathlib import Path

from bolle.models import EsitoRiconciliazione, Proposta, TipoProposta
from bolle.review_queue import accoda


def test_json_include_entrambi_i_numeri_di_ordine(tmp_path: Path):
    esito = EsitoRiconciliazione(
        numero_ordine="26402153-OC-00040",          # ordine cliente
        numero_ordine_fornitore="26ODV00156",       # ordine interno fornitore
        proposte=[Proposta(tipo=TipoProposta.AMMANCO, dettaglio="ammanco")],
    )
    out = accoda(esito, "DDT1", tmp_path)
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["numero_ordine"] == "26402153-OC-00040"
    assert payload["numero_ordine_fornitore"] == "26ODV00156"
