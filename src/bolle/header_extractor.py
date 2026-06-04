"""Estrazione della testata (numero ordine, fornitore, data, numero bolla).

Estrazione semantica - per significato, non per posizione - con un LLM piccolo
via Ollama, cosi da non dipendere dal template del fornitore. Le 100 righe NON
passano dall'LLM (lento e a rischio allucinazioni sui numeri): si parsano in modo
deterministico dalla tabella OCR.

Senza Ollama disponibile, si usa un fallback a regex sui pattern piu comuni nelle
bolle italiane (sufficiente per lo skeleton e per i PDF nativi).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime

from .config import LlmConfig
from .models import Testata

_PROMPT = """Sei un estrattore di dati da bolle di consegna fornitori italiane.
Dal testo seguente estrai SOLO questi campi e rispondi con un oggetto JSON valido
con esattamente queste chiavi:
  - numero_ordine: numero d'ordine DEL CLIENTE (sulle bolle italiane appare come
    "Vs. Ordine" o "Vostro Ordine"). E' quello da abbinare al gestionale aziendale.
  - numero_ordine_fornitore: numero d'ordine INTERNO del fornitore (appare come
    "Ordine" o "Ns. Ordine" o "Nostro Ordine").
  - fornitore: ragione sociale del fornitore.
  - numero_bolla: numero del documento di trasporto/DDT.
  - data_bolla: data del documento, formato YYYY-MM-DD.
Usa null se un campo non e presente. Non inventare valori.

TESTO:
{text}
"""


def extract_header(full_text: str, cfg: LlmConfig) -> Testata:
    if cfg.enabled:
        try:
            return _extract_via_ollama(full_text, cfg)
        except Exception:
            # Fallback robusto: non blocchiamo la pipeline se l'LLM non risponde.
            pass
    return _extract_via_regex(full_text)


def _extract_via_ollama(full_text: str, cfg: LlmConfig) -> Testata:
    import requests  # type: ignore

    resp = requests.post(
        f"{cfg.base_url}/api/generate",
        json={
            "model": cfg.model,
            "prompt": _PROMPT.format(text=full_text[:6000]),
            "format": "json",
            "stream": False,
        },
        timeout=cfg.timeout_s,
    )
    resp.raise_for_status()
    payload = json.loads(resp.json()["response"])
    return Testata(
        numero_ordine=payload.get("numero_ordine"),
        numero_ordine_fornitore=payload.get("numero_ordine_fornitore"),
        fornitore=payload.get("fornitore"),
        numero_bolla=payload.get("numero_bolla"),
        data_bolla=_parse_date(payload.get("data_bolla")),
    )


# Vs./Vostro Ordine = ordine del CLIENTE (matching con Oracle).
# Il "salto" prima del codice permette parole come "Nr." in mezzo, e la cattura
# vera e' ancorata a un token alfanumerico di almeno 3 caratteri.
_RE_ORDINE_CLIENTE = re.compile(
    r"\b(?:vs|vostro)\.?\s*ordine\b[^\n]{0,30}?([A-Z0-9][A-Z0-9/\-]{2,})", re.I
)
# Ns./Nostro Ordine o bare "Ordine" = riferimento INTERNO del fornitore.
_RE_ORDINE_FORN = re.compile(
    r"\bordine\b[^\n]{0,30}?([A-Z0-9][A-Z0-9/\-]{2,})", re.I
)
# Numero della bolla/DDT: richiede che la cattura COMINCI con una cifra, cosi'
# non agganciamo per sbaglio parole tipo "trasporto" dopo "Documento di...".
# Il gap puo' scavalcare un newline perche' spesso "Documento di trasporto" e
# il "Nr. ..." sono su righe diverse.
_RE_BOLLA = re.compile(
    r"(?:bolla|ddt|d\.d\.t\.?|documento)[\s\S]{0,40}?(\d[A-Z0-9/\-]{2,})", re.I
)
_RE_DATA = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")


def _extract_via_regex(full_text: str) -> Testata:
    t = Testata()
    if m := _RE_ORDINE_CLIENTE.search(full_text):
        t.numero_ordine = m.group(1)
    # Per il fornitore cerchiamo "Ordine ..." sul testo "ripulito" dai match
    # gia' attribuiti al cliente, cosi' non lo ricatturiamo per sbaglio.
    cleaned = _RE_ORDINE_CLIENTE.sub("VOSTRO_ORDINE", full_text)
    if m := _RE_ORDINE_FORN.search(cleaned):
        t.numero_ordine_fornitore = m.group(1)
    if m := _RE_BOLLA.search(full_text):
        t.numero_bolla = m.group(1)
    if m := _RE_DATA.search(full_text):
        d, mo, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000
        try:
            t.data_bolla = date(y, mo, d)
        except ValueError:
            pass
    return t


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
