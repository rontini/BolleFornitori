"""Coda di revisione.

Raccoglie righe non risolte e proposte da validare dall'ufficio (il giorno prima,
coerentemente con l'obiettivo di anticipo). Persistenza semplice su file JSON;
in produzione potra diventare una tabella/endpoint dedicato.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from .models import EsitoRiconciliazione


def accoda(esito: EsitoRiconciliazione, bolla_id: str, directory: str | Path) -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{bolla_id}.json"
    payload = {
        "bolla_id": bolla_id,
        "numero_ordine": esito.numero_ordine,
        "proposte": [asdict(p) for p in esito.proposte],
        "righe_in_revisione": [asdict(r) for r in esito.righe_in_revisione],
    }
    out.write_text(json.dumps(payload, default=_json_default, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(f"non serializzabile: {type(o)}")
