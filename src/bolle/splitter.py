"""Splitter di PDF multi-bolla.

Caso d'uso primario in produzione: il PDF contiene UNA bolla -> lo splitter
restituisce il file invariato (no-op). Caso secondario: un PDF contiene piu'
bolle accodate (capita con la scansione massiva del fornitore). Lo splitter
le separa PRIMA dell'OCR, cosi' il modello processa una bolla per volta e
hallucina molto meno (e la riconciliazione lavora per bolla come da analisi).

Rilevamento: si fa un'OCR LEGGERA solo sull'intestazione di ogni pagina e si
cerca un marker tipo 'Pagina 1/N', 'Pag 1 di N', 'Pagina 1 / N'. Ogni volta
che vediamo un '1/N' inizia una nuova bolla.

Logica di pura computazione (`_boundaries_from_markers`) e parsing del marker
(`_parse_marker`) sono testabili senza OCR.
"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path

from .config import OcrConfig

log = logging.getLogger("bolle.splitter")

_PROMPT_HEADER = (
    "Trascrivi SOLO la prima riga di intestazione del documento di trasporto, "
    "in particolare il marker di pagina se presente (es. 'Pagina 1/5', "
    "'Pag 1 di 00002', 'Pagina 1 / 5', 'Pagina 1/1'). Nessun altro testo, "
    "max una riga."
)

_RE_PAGINA = re.compile(
    r"(?:pagina|pag\.?)[^\d]{0,5}(\d+)\s*(?:/|di)\s*0*(\d+)",
    re.IGNORECASE,
)


def split_pdf(
    pdf_path: str | Path,
    cfg: OcrConfig,
    work_dir: Path | None = None,
) -> list[Path]:
    """Splitta un PDF multi-bolla in PDF separati.

    Ritorna [pdf_path] se il file non e' un PDF, ha una sola pagina, o contiene
    una sola bolla. Altrimenti crea N PDF in `work_dir` e ritorna i loro path.
    """
    pdf_path = Path(pdf_path)
    if pdf_path.suffix.lower() != ".pdf":
        return [pdf_path]

    try:
        import fitz  # PyMuPDF
    except ImportError:
        log.warning("PyMuPDF non disponibile: splitter disattivato")
        return [pdf_path]

    with fitz.open(pdf_path) as doc:
        n_pages = len(doc)
        if n_pages <= 1:
            return [pdf_path]

        log.info("splitter: ispeziono %d pagine per rilevare i confini fra bolle", n_pages)
        page_starts = _scan_page_starts(doc, cfg)
        boundaries = _boundaries_from_markers(page_starts, n_pages)

        if len(boundaries) <= 1:
            log.info("splitter: bolla unica, nessuno split necessario")
            return [pdf_path]

        out_dir = Path(work_dir) if work_dir else pdf_path.parent / f"split_{pdf_path.stem}"
        out_dir.mkdir(parents=True, exist_ok=True)

        out_paths: list[Path] = []
        for i, (start, end) in enumerate(boundaries, start=1):
            sub_path = out_dir / f"{pdf_path.stem}_bolla{i:02d}.pdf"
            sub_doc = fitz.open()
            sub_doc.insert_pdf(doc, from_page=start, to_page=end)
            sub_doc.save(sub_path)
            sub_doc.close()
            out_paths.append(sub_path)
            log.info(
                "splitter: bolla %d/%d -> pagine %d-%d -> %s",
                i, len(boundaries), start + 1, end + 1, sub_path,
            )
    return out_paths


def _scan_page_starts(doc, cfg: OcrConfig) -> list[int]:
    """Per ogni pagina chiede al modello il marker di intestazione.

    Restituisce gli indici 0-based delle pagine che hanno page_num == 1.
    """
    starts: list[int] = []
    for i in range(len(doc)):
        text = _ocr_top_of_page(doc[i], cfg)
        page_num, total = _parse_marker(text)
        log.info("splitter: pagina %d -> marker '%s' (n=%s/%s)", i + 1, text.strip(), page_num, total)
        if page_num == 1:
            starts.append(i)
    return starts


def _ocr_top_of_page(page, cfg: OcrConfig) -> str:
    """Rendi la parte alta della pagina e chiedi al modello solo il marker."""
    import fitz
    import requests  # type: ignore

    rect = page.rect
    # Top 30% della pagina: copre l'intestazione di tutte le bolle viste.
    crop = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.30)
    longest = max(crop.width, crop.height) or 1
    zoom = cfg.resize_px / longest
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=crop)
    png_bytes = pix.tobytes("png")

    payload = {
        "model": cfg.dots_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT_HEADER},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii"),
                        },
                    },
                ],
            }
        ],
        "temperature": 0.0,
        "max_tokens": 60,
        "stream": False,
    }
    resp = requests.post(
        f"{cfg.dots_server_url.rstrip('/')}/v1/chat/completions",
        json=payload,
        timeout=cfg.request_timeout_s,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _parse_marker(text: str) -> tuple[int | None, int | None]:
    """Estrae (numero_pagina, totale_pagine) da una stringa di intestazione.

    Accetta forme: 'Pagina 1/5', 'Pag 1 di 00002', 'Pagina 1 / 5', 'pag. 2 di 2'.
    """
    if not text:
        return None, None
    m = _RE_PAGINA.search(text)
    if not m:
        return None, None
    try:
        return int(m.group(1)), int(m.group(2))
    except ValueError:
        return None, None


def _boundaries_from_markers(page_starts: list[int], n_pages: int) -> list[tuple[int, int]]:
    """Pure function: dati gli indici di 'pagina 1 di N' calcola (start, end) per ogni bolla.

    Robusto a:
      - assenza di marker (assume bolla unica),
      - prima pagina senza marker leggibile (la consideriamo comunque inizio bolla 1).
    """
    if n_pages <= 0:
        return []
    starts = list(page_starts)
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    # Dedup mantenendo l'ordine
    seen: set[int] = set()
    uniq: list[int] = []
    for s in starts:
        if s not in seen and 0 <= s < n_pages:
            seen.add(s)
            uniq.append(s)

    out: list[tuple[int, int]] = []
    for idx, start in enumerate(uniq):
        end = uniq[idx + 1] - 1 if idx + 1 < len(uniq) else n_pages - 1
        out.append((start, end))
    return out
