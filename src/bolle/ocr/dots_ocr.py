"""Adapter dots.ocr tramite llama.cpp (motore OCR alternativo, CPU senza AVX).

dots.ocr e' un modello vision-language per il parsing documentale (licenza MIT,
disponibile in GGUF). Gira con llama.cpp, che funziona anche su CPU prive di AVX:
utile quando PaddleOCR-VL non e' eseguibile (es. VM senza AVX esposto).

L'adapter NON carica il modello in-process: si appoggia a un `llama-server` locale
con API OpenAI-compatibile e supporto immagini (--mmproj). Cosi' il runtime resta
fuori da Python e nessun dato esce dalla macchina.

Le pagine PDF vengono rasterizzate (PyMuPDF) e ridimensionate a ~resize_px; ogni
pagina viene mandata al modello che restituisce il documento in Markdown. Da li'
si ricavano testo (per la testata) e righe (dalle tabelle Markdown).

NB: import di requests/fitz pigro -> lo skeleton resta importabile senza runtime.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from ..config import OcrConfig
from .base import OcrEngine, OcrResult, TableCell, TableRow

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
        text_parts: list[str] = []
        rows: list[TableRow] = []
        for png in images:
            markdown = self._call_server(png)
            text_parts.append(markdown)
            rows.extend(_markdown_to_rows(markdown))
        return OcrResult(rows=rows, full_text="\n\n".join(text_parts))

    def _call_server(self, png_bytes: bytes) -> str:
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
            "stream": False,
        }
        resp = requests.post(
            f"{self.cfg.dots_server_url.rstrip('/')}/v1/chat/completions",
            json=payload,
            timeout=self.cfg.request_timeout_s,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


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
