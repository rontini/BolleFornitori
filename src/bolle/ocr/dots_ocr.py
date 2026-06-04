"""Adapter dots.ocr / GLM-OCR tramite llama.cpp (motore OCR per CPU senza AVX).

Si appoggia a un `llama-server` locale con API OpenAI-compatibile e supporto
immagini (--mmproj). Funziona con qualsiasi modello OCR multimodale servito da
llama-server (GLM-OCR, dots.ocr, ...). Nessun dato esce dalla macchina.

Le pagine PDF vengono rasterizzate (PyMuPDF) e ridimensionate a ~resize_px; ogni
pagina viene inviata al modello che restituisce il documento in Markdown. Da li'
si ricavano testo (per la testata) e righe (dalle tabelle Markdown).

La risposta viene letta in STREAMING (Server-Sent Events): i token arrivano in
continuazione, quindi non si incappa nel timeout di lettura su CPU lente, e si
puo' mostrare l'avanzamento per pagina.

NB: import di requests/fitz pigro -> lo skeleton resta importabile senza runtime.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import OcrConfig
from .base import OcrEngine, OcrResult, TableCell, TableRow

log = logging.getLogger("bolle.ocr.dots")

_PROMPT = (
    "Sei un OCR per bolle di consegna (DDT). Estrai SOLO i dati, senza commenti.\n"
    "Riga 1: numero ordine, fornitore, numero bolla, data (se presenti).\n"
    "Poi UNA sola tabella Markdown con queste colonne (in quest'ordine):\n"
    "| codice | descrizione | quantita | udm | prezzo | totale |\n"
    "Una riga per articolo. Lascia la cella vuota se il dato non c'e' nel "
    "documento (es. in un DDT mancano prezzo e totale). Non inventare valori. "
    "Non descrivere il documento, non aggiungere testo prima o dopo la tabella, "
    "non includere righe di colli/peso/firme/vettore: fermati dopo l'ultima "
    "riga articolo."
)


class DotsOcrEngine(OcrEngine):
    def __init__(self, cfg: OcrConfig) -> None:
        self.cfg = cfg

    def recognize(self, path: str | Path, pages: list[int] | None = None) -> OcrResult:
        rendered = _render_pages(Path(path), self.cfg.resize_px, pages)
        total = len(rendered)
        text_parts: list[str] = []
        rows: list[TableRow] = []
        for i, (page_no, png) in enumerate(rendered, start=1):
            log.info("OCR pagina %d (%d/%d selezionate)", page_no, i, total)
            markdown = self._call_server(png, page_no)
            text_parts.append(markdown)
            rows.extend(_markdown_to_rows(markdown))
        return OcrResult(rows=rows, full_text="\n\n".join(text_parts))

    def _call_server(self, png_bytes: bytes, page_no: int) -> str:
        import requests  # type: ignore

        b64 = base64.b64encode(png_bytes).decode("ascii")
        payload = {
            "model": self.cfg.dots_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": self.cfg.dots_max_tokens,
            "stream": True,
        }
        url = f"{self.cfg.dots_server_url.rstrip('/')}/v1/chat/completions"
        parts: list[str] = []
        n_tok = 0
        # connect-timeout breve (server giu' -> errore rapido); read-timeout per
        # singolo chunk: in streaming i token arrivano di continuo.
        with requests.post(
            url, json=payload, stream=True, timeout=(10, self.cfg.request_timeout_s)
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines(decode_unicode=False):
                delta = _delta_from_sse_line(raw)
                if delta is None:
                    continue
                parts.append(delta)
                n_tok += 1
                _progress(page_no, n_tok)
        _progress_end()
        return "".join(parts)


def _delta_from_sse_line(raw: bytes) -> str | None:
    """Estrae il pezzo di testo da una riga SSE `data: {...}`; None se non pertinente."""
    if not raw or not raw.startswith(b"data:"):
        return None
    data = raw[len(b"data:"):].strip()
    if not data or data == b"[DONE]":
        return None
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None
    choices = obj.get("choices") or [{}]
    return choices[0].get("delta", {}).get("content") or None


def _progress(page_no: int, n_tok: int) -> None:
    sys.stderr.write(f"\r  pagina {page_no} · token letti: {n_tok}   ")
    sys.stderr.flush()


def _progress_end() -> None:
    sys.stderr.write("\n")
    sys.stderr.flush()


def _render_pages(
    path: Path, target_px: int, pages: list[int] | None = None
) -> list[tuple[int, bytes]]:
    """Restituisce (numero_pagina_1based, PNG). pages: indici 0-based, None = tutte."""
    if path.suffix.lower() == ".pdf":
        import fitz  # PyMuPDF

        out: list[tuple[int, bytes]] = []
        with fitz.open(path) as doc:
            if pages is None:
                indici = range(len(doc))
            else:
                indici = [i for i in pages if 0 <= i < len(doc)]
            for i in indici:
                page = doc[i]
                longest = max(page.rect.width, page.rect.height) or 1
                zoom = target_px / longest
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                out.append((i + 1, pix.tobytes("png")))
        return out

    from io import BytesIO

    from PIL import Image  # type: ignore

    img = Image.open(path).convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > target_px:
        scale = target_px / longest
        img = img.resize((int(w * scale), int(h * scale)))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return [(1, buf.getvalue())]


_SEP_CELL = re.compile(r"^:?-{2,}:?$")

# Sinonimi di intestazione che il modello puo' produrre, per colonna logica.
_HEADER_ALIASES = {
    "codice": ("codice", "nr.", "nr", "codice articolo", "articolo", "n.", "n"),
    "descrizione": ("descrizione", "denominazione"),
    "quantita": ("quantita", "quantità", "qta", "q.ta", "qty"),
    "udm": ("udm", "u.d.m.", "u.m.", "um"),
    "prezzo": ("prezzo", "prezzo unitario", "prezzo unit."),
    "totale": ("totale", "importo", "totale riga"),
}


def _normalize_header(text: str) -> str | None:
    """Mappa un testo di intestazione a uno dei nomi logici di colonna (o None)."""
    t = text.strip().lower().rstrip(".:")
    for key, aliases in _HEADER_ALIASES.items():
        if t in aliases:
            return key
    return None


@dataclass
class _Table:
    columns: list[str | None]                # nome logico per colonna (None = ignota)
    rows: list[list[str]]                    # celle delle sole righe dati


def _extract_articoli_table(markdown: str) -> _Table | None:
    """Trova la PRIMA tabella articoli e ritorna intestazione + righe.

    Logica: si entra in una "tabella" quando si incontra una riga separatrice
    `| --- | --- |`. La riga immediatamente sopra e' l'header. Si raccolgono le
    righe successive finche' (a) finiscono le righe `|...|`, oppure (b) cambia
    il numero di colonne, oppure (c) compare una riga non-articolo (colli/peso/
    firme/vettore): a quel punto la tabella articoli e' finita.
    """
    lines = markdown.splitlines()
    header_idx = None
    n_cols = None
    for i, line in enumerate(lines):
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|") and s.count("|") >= 2):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(_SEP_CELL.match(c) for c in cells if c):
            header_idx = i - 1
            n_cols = len(cells)
            data_start = i + 1
            break
    if header_idx is None or n_cols is None:
        return None

    header_cells = [c.strip() for c in lines[header_idx].strip().strip("|").split("|")]
    columns = [_normalize_header(c) for c in header_cells]

    rows: list[list[str]] = []
    for line in lines[data_start:]:
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            break
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) != n_cols:
            break  # cambio di tabella (es. colli/peso): articoli finiti
        if _is_non_articolo(cells, columns):
            break
        if all(not c for c in cells):
            continue
        rows.append(cells)
    return _Table(columns=columns, rows=rows)


_NON_ARTICOLO_HINTS = (
    "colli",
    "peso",
    "vettore",
    "trasporto",
    "destinatario",
    "firma",
    "asporto",
    "spediz",
    "località",
    "localita",
    "volume",
)


def _is_non_articolo(cells: list[str], columns: list[str | None]) -> bool:
    """True se la riga e' chiaramente del blocco logistico, non un articolo."""
    joined = " ".join(c.lower() for c in cells)
    if any(h in joined for h in _NON_ARTICOLO_HINTS):
        return True
    # Se la colonna "quantita" non contiene cifre, probabilmente non e' un articolo.
    try:
        qi = columns.index("quantita")
    except ValueError:
        return False
    return not any(ch.isdigit() for ch in cells[qi])


_RE_NUMERO = re.compile(r"-?\d{1,3}(?:[.\s]?\d{3})*(?:[.,]\d+)?")


def _decimale(text: str) -> "Decimal | None":
    from decimal import Decimal, InvalidOperation

    if not text:
        return None
    m = _RE_NUMERO.search(text)
    if not m:
        return None
    norm = m.group(0).replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return Decimal(norm)
    except InvalidOperation:
        return None


def parse_articoli(markdown: str) -> list["RigaBolla"]:
    """Converte l'output Markdown in RigaBolla mappando le colonne per NOME.

    Robusto rispetto a:
      - colonne in ordine diverso o sotto-insiemi (DDT senza prezzo/totale),
      - quantita scritte con unita' nella stessa cella ('18 NR' -> 18),
      - tabelle di colli/peso/firme che seguono quella degli articoli (vengono ignorate).
    """
    from ..models import RigaBolla

    table = _extract_articoli_table(markdown)
    if not table:
        return []

    def cell(row: list[str], col: str) -> str:
        try:
            return row[table.columns.index(col)]
        except ValueError:
            return ""

    out: list[RigaBolla] = []
    for i, row in enumerate(table.rows, start=1):
        codice = cell(row, "codice")
        if not codice:
            continue
        out.append(
            RigaBolla(
                numero_riga=i,
                codice_letto=codice,
                descrizione=cell(row, "descrizione") or None,
                quantita=_decimale(cell(row, "quantita")),
                prezzo_unitario=_decimale(cell(row, "prezzo")),
                totale_riga=_decimale(cell(row, "totale")),
            )
        )
    return out


def _markdown_to_rows(markdown: str) -> list[TableRow]:
    """Fallback generico: rappresentazione "celle grezze" per il parser legacy.

    Usato dagli adapter OCR diversi da dots/llama (PaddleOCR-VL/GLM-OCR), che
    passano per src/bolle/parsing.py. Per dots/llama si usa parse_articoli().
    """
    table = _extract_articoli_table(markdown)
    if table is not None:
        return [
            TableRow(cells=[TableCell(text=c, confidence=1.0) for c in row])
            for row in table.rows
        ]
    # Nessuna tabella Markdown: ripiega separando le colonne sugli spazi multipli.
    rows: list[TableRow] = []
    for line in markdown.splitlines():
        s = line.strip()
        if not s:
            continue
        cells = re.split(r"\s{2,}|\t", s)
        if len(cells) >= 2:
            rows.append(TableRow(cells=[TableCell(text=c.strip(), confidence=1.0) for c in cells]))
    return rows
