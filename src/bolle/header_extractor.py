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
con esattamente queste chiavi: numero_ordine, fornitore, numero_bolla, data_bolla
(formato YYYY-MM-DD). Usa null se un campo non e presente. Non inventare valori.

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
        fornitore=payload.get("fornitore"),
        numero_bolla=payload.get("numero_bolla"),
        data_bolla=_parse_date(payload.get("data_bolla")),
    )


_RE_ORDINE = re.compile(r"(?:ordine|ns\.?\s*ordine|n[.\s]*ordine|p\.?o\.?)\D{0,8}([A-Z0-9/\-]{3,})", re.I)
_RE_BOLLA = re.compile(r"(?:bolla|ddt|d\.d\.t\.?|documento)\D{0,8}([A-Z0-9/\-]{2,})", re.I)
_RE_DATA = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")


def _extract_via_regex(full_text: str) -> Testata:
    t = Testata()
    if m := _RE_ORDINE.search(full_text):
        t.numero_ordine = m.group(1)
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
