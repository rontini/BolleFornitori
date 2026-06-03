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
from pathlib import Path

from ..config import OcrConfig
from .base import OcrEngine, OcrResult, TableCell, TableRow

log = logging.getLogger("bolle.ocr.dots")

_PROMPT = (
    "Sei un OCR per bolle di consegna. Trascrivi fedelmente il documento. "
    "Riporta le righe articolo come tabella Markdown con intestazioni "
    "| codice | descrizione | quantita | prezzo | totale |. "
    "Non inventare valori; lascia vuota la cella se il dato non c'e'. "
    "Includi sopra la tabella le informazioni di testata (numero ordine, "
    "fornitore, numero bolla, data) come testo."
)


class DotsOcrEngine(OcrEngine):
    def __init__(self, cfg: OcrConfig) -> None:
        self.cfg = cfg

    def recognize(self, path: str | Path) -> OcrResult:
        images = _render_pages(Path(path), self.cfg.resize_px)
        total = len(images)
        text_parts: list[str] = []
        rows: list[TableRow] = []
        for i, png in enumerate(images, start=1):
            log.info("OCR pagina %d/%d", i, total)
            markdown = self._call_server(png, page_index=i, page_total=total)
            text_parts.append(markdown)
            rows.extend(_markdown_to_rows(markdown))
        return OcrResult(rows=rows, full_text="\n\n".join(text_parts))

    def _call_server(self, png_bytes: bytes, page_index: int, page_total: int) -> str:
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
                _progress(page_index, page_total, n_tok)
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


def _progress(page_index: int, page_total: int, n_tok: int) -> None:
    sys.stderr.write(f"\r  pagina {page_index}/{page_total} · token letti: {n_tok}   ")
    sys.stderr.flush()


def _progress_end() -> None:
    sys.stderr.write("\n")
    sys.stderr.flush()


def _render_pages(path: Path, target_px: int) -> list[bytes]:
    """Restituisce le pagine come PNG. PDF -> rasterizzazione; immagini -> resize."""
    if path.suffix.lower() == ".pdf":
        import fitz  # PyMuPDF

        out: list[bytes] = []
        with fitz.open(path) as doc:
            for page in doc:
                longest = max(page.rect.width, page.rect.height) or 1
                zoom = target_px / longest
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                out.append(pix.tobytes("png"))
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
    return [buf.getvalue()]


_SEP_CELL = re.compile(r"^:?-{2,}:?$")


def _markdown_to_rows(markdown: str) -> list[TableRow]:
    """Estrae le righe dalle tabelle Markdown; fallback su split per spazi."""
    rows: list[TableRow] = []
    found_table = False
    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(_SEP_CELL.match(c) for c in cells if c):
                # Riga separatrice ---|---: la riga sopra era l'intestazione, scartala.
                if rows:
                    rows.pop()
                found_table = True
                continue
            if all(not c for c in cells):
                continue
            rows.append(TableRow(cells=[TableCell(text=c, confidence=1.0) for c in cells]))

    if found_table:
        return rows

    # Nessuna tabella Markdown: ripiega separando le colonne sugli spazi multipli.
    for line in markdown.splitlines():
        s = line.strip()
        if not s:
            continue
        cells = re.split(r"\s{2,}|\t", s)
        if len(cells) >= 2:
            rows.append(TableRow(cells=[TableCell(text=c.strip(), confidence=1.0) for c in cells]))
    return rows
