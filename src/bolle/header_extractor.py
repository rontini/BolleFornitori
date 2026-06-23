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


def extract_header(
    full_text: str,
    cfg: LlmConfig,
    fornitori_noti: list[dict[str, str]] | None = None,
) -> Testata:
    if cfg.enabled:
        try:
            t = _extract_via_ollama(full_text, cfg)
        except Exception:
            # Fallback robusto: non blocchiamo la pipeline se l'LLM non risponde.
            t = _extract_via_regex(full_text)
    else:
        t = _extract_via_regex(full_text)
    # Riconoscimento fornitore in cascata (il primo che trova qualcosa vince):
    #   1. pattern noti dall'utente (config-driven, nomi canonici esatti)
    #   2. ragione sociale italiana esplicita nel testo (s.r.l./S.p.A./...)
    # Cosi' l'utente puo' sempre forzare un nome canonico (es. con maiuscole
    # e abbreviazioni come le ha in anagrafica) anche quando la regex generica
    # troverebbe una variante diversa nel testo OCR.
    if t.fornitore is None and fornitori_noti:
        t.fornitore = _riconosci_fornitore(full_text, fornitori_noti)
    if t.fornitore is None:
        t.fornitore = _estrai_ragione_sociale(full_text)
    return t


def _riconosci_fornitore(full_text: str, fornitori_noti: list[dict[str, str]]) -> str | None:
    """Applica i pattern in ordine: il primo che matcha vince."""
    for entry in fornitori_noti:
        pattern = entry.get("pattern", "")
        nome = entry.get("nome", "")
        if not pattern or not nome:
            continue
        try:
            if re.search(pattern, full_text, re.IGNORECASE):
                return nome
        except re.error:
            continue  # pattern malformato in config: ignora invece di crashare
    return None


# Ragione sociale italiana con qualifica societaria (s.r.l., S.p.A., srl, snc,
# soc. coop., ...). La qualifica esclude il cliente quando e' indicato come
# "S.C." (es. "CEFLA S.C." → niente qualifica, non viene catturato).
_RE_RAGIONE_SOCIALE = re.compile(
    r"\b(?P<nome>[A-Z][A-Z0-9 .'\-&]{2,40}?)\s+"
    r"(?P<qual>s\.?r\.?l\.?|s\.?p\.?a\.?|s\.?n\.?c\.?|s\.?a\.?s\.?|soc\.?\s*coop\.?)\b",
    re.IGNORECASE,
)
# Termini da scartare: spesso il modello rende il destinatario come pseudo-fornitore
# o ci sono noti spedizionieri/vettori che non sono il fornitore della merce.
_SCARTI_RAGIONE_SOCIALE = (
    "cefla",                       # cliente tipico nei DDT di esempio
    "cuti", "consai",              # spedizionieri ricorrenti
    "intesa", "san pa",            # banca pagamenti
    "vettore", "trasportatore",    # label del trasportatore, non del fornitore
    "incaricato",                  # "INCARICATO TRASPORTO" sopra al vettore
    "destinatario", "mittente",    # label di indirizzo, non ragione sociale
)


def _estrai_ragione_sociale(full_text: str) -> str | None:
    """Cerca nel testo la prima ragione sociale italiana con qualifica
    societaria esplicita (s.r.l./s.p.a./srl/spa/snc/sas/soc. coop.).

    Filtra i nomi che sono chiaramente cliente o spedizioniere (lista nota di
    scarti). Funziona quando il modello ha trascritto la carta intestata;
    sulle bolle dove il modello la salta, restituisce None (fallback ai
    fornitori_noti o all'override CLI)."""
    for m in _RE_RAGIONE_SOCIALE.finditer(full_text):
        nome = re.sub(r"\s+", " ", m.group("nome")).strip()
        if not nome:
            continue
        lower = nome.lower()
        if any(s in lower for s in _SCARTI_RAGIONE_SOCIALE):
            continue
        # Compone "NOME S.R.L." normalizzando la qualifica
        qual = m.group("qual").upper().replace(" ", "").rstrip(".")
        return f"{nome} {qual}"
    return None


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
# Accetta anche le abbreviazioni 'Vs.ord.' / 'Vs. Ord' usate da alcuni
# fornitori (es. Camozzi: "Saldo Vs.ord. 26423188-OK del 26.05.2026").
_RE_ORDINE_CLIENTE = re.compile(
    r"\b(?:vs|vostro)\.?\s*ord(?:ine)?\b\.?[^\n]{0,30}?([A-Z0-9][A-Z0-9/\-]{2,})", re.I
)
# Ns./Nostro Ordine o bare "Ordine" = riferimento INTERNO del fornitore.
_RE_ORDINE_FORN = re.compile(
    r"\bordine\b[^\n]{0,30}?([A-Z0-9][A-Z0-9/\-]{2,})", re.I
)
# Numero della bolla/DDT: richiede che la cattura COMINCI con una cifra, cosi'
# non agganciamo per sbaglio parole tipo "trasporto" dopo "Documento di...".
# Il gap puo' scavalcare un newline perche' spesso "Documento di trasporto" e
# il "Nr. ..." sono su righe diverse. Minimo 4 caratteri: esclude il "472" di
# "(D.P.R. N. 472 del 14/8/96)" stampato su molti DDT.
_RE_BOLLA = re.compile(
    r"(?:bolla|ddt|d\.d\.t\.?|documento)[\s\S]{0,40}?(\d[A-Z0-9/\-]{3,})", re.I
)
_RE_DATA = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")

# Solo date di lavoro plausibili: esclude i riferimenti normativi stampati sui
# DDT ("D.P.R. ... del 14/8/96" -> 1996/2096) e altri rumori OCR.
_ANNO_MIN, _ANNO_MAX = 2015, 2049


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
    # Prima data con anno plausibile (non la prima in assoluto).
    for m in _RE_DATA.finditer(full_text):
        d, mo, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000
        if not (_ANNO_MIN <= y <= _ANNO_MAX):
            continue
        try:
            t.data_bolla = date(y, mo, d)
            break
        except ValueError:
            continue
    return t


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
