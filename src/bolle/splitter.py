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
    "Cerca in questa intestazione il marker di numerazione pagina, ad esempio "
    "'Pagina 1/5', 'PAGINA 1 / 5', 'Pag 1 di 00002', 'Pagina 1/1'. "
    "Trascrivi l'intestazione riga per riga finche' non lo trovi, poi fermati "
    "subito dopo averlo scritto."
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
        infos = _scan_pages(doc, cfg)
        raw_headers = [info[3] for info in infos]  # testo header per ogni pagina (per sidecar)
        boundaries = _boundaries_from_markers(_starts_from_scan(infos), n_pages)

        if len(boundaries) <= 1:
            log.info("splitter: bolla unica, nessuno split necessario")
            # Anche senza split: l'OCR dell'header (ritaglio in alto) legge la
            # ragione sociale meglio della pagina piena. La salviamo nel sidecar
            # accanto al PDF originale, cosi' la pipeline la usa comunque.
            _scrivi_sidecar(pdf_path, raw_headers[0] if raw_headers else "")
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
            # Sidecar: scrive accanto al PDF splittato la ragione sociale del
            # fornitore (letta dall'header della prima pagina della bolla).
            # La pipeline la riusa per popolare la testata anche quando il .md
            # del documento intero non la contiene.
            _scrivi_sidecar(sub_path, raw_headers[start] if start < len(raw_headers) else "")
            out_paths.append(sub_path)
            log.info(
                "splitter: bolla %d/%d -> pagine %d-%d -> %s",
                i, len(boundaries), start + 1, end + 1, sub_path,
            )
    return out_paths


def _scrivi_sidecar(pdf_path: Path, header_text: str) -> None:
    """Scrive <stem>.meta.json accanto al PDF con il fornitore estratto."""
    import json

    from .header_extractor import _estrai_ragione_sociale

    fornitore = _estrai_ragione_sociale(header_text)
    if not fornitore:
        return
    sidecar = pdf_path.with_suffix(".meta.json")
    sidecar.write_text(
        json.dumps({"fornitore": fornitore}, ensure_ascii=False),
        encoding="utf-8",
    )


def _scan_pages(doc, cfg: OcrConfig) -> list[tuple[int | None, int | None, str, str]]:
    """Per ogni pagina: (numero nel marker 'Pagina N/M' se letto, impronta fornitore).

    L'impronta e' derivata dalle prime righe della trascrizione dell'header
    (carta intestata): serve a rilevare il cambio di fornitore quando il
    marker di pagina non e' leggibile.
    """
    # Con il motore PaddleOCR-VL l'header viene letto in-process (niente
    # llama-server): istanziamo l'engine UNA volta per tutte le pagine.
    paddle_engine = None
    if cfg.engine == "paddleocr_vl":
        from .ocr.paddleocr_vl import PaddleOcrVlEngine

        paddle_engine = PaddleOcrVlEngine(cfg)

    out: list[tuple[int | None, int | None, str, str]] = []
    for i in range(len(doc)):
        text = _ocr_top_of_page(doc[i], cfg, paddle_engine)
        page_num, total = _parse_marker(text)
        fp = _fingerprint(text)
        log.info(
            "splitter: pagina %d -> n=%s/%s, impronta '%s'", i + 1, page_num, total, fp
        )
        # Tupla: (numero pagina nel marker, totale pagine, impronta, testo raw).
        # Il testo raw e' usato per costruire il sidecar con la ragione sociale.
        out.append((page_num, total, fp, text))
    return out


def _fingerprint(text: str) -> str:
    """Prime due righe non vuote, solo alfanumerico minuscolo, max 24 caratteri.

    Robusto al rumore OCR: 'Verniciatura Bolognese s.r.l.' e
    'Verniciatura\\nBolognese srl' producono la stessa impronta.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    joined = "".join(lines[:2])
    alnum = re.sub(r"[^a-z0-9]", "", joined.lower())
    return alnum[:24]


def _starts_from_scan(infos: list[tuple]) -> list[int]:
    """Indici 0-based delle pagine che iniziano una nuova bolla.

    Accetta tuple a 3 elementi (num, total, fp) o 4 (con raw_text in coda).

    Regole:
      - marker 'Pagina 1/N' -> inizio bolla; se N e' noto, le successive N-1
        pagine sono continuazione ATTESA (nessun controllo sull'impronta:
        l'OCR dell'header puo' variare fra pagine della stessa bolla);
      - marker 'Pagina K/N' con K>1 -> continuazione (mai inizio);
      - marker illeggibile e fuori da una continuazione attesa -> inizio se
        l'impronta fornitore cambia rispetto alla pagina precedente.
    """
    starts: list[int] = []
    prev_fp: str | None = None
    expected_until = -1  # ultimo indice di continuazione attesa dal marker 1/N
    for i, info in enumerate(infos):
        num, total, fp = info[0], info[1], info[2]
        if num == 1:
            starts.append(i)
            if total and total > 1:
                expected_until = i + total - 1
        elif i <= expected_until or num is not None:
            pass  # continuazione (attesa dal totale, o dichiarata dal marker K/N)
        elif prev_fp is not None and fp and fp != prev_fp:
            starts.append(i)
        prev_fp = fp
    return starts


def _ocr_top_of_page(page, cfg: OcrConfig, paddle_engine=None) -> str:
    """Rendi la parte alta della pagina e leggine il testo con il motore attivo."""
    import fitz

    rect = page.rect
    # Top 30% della pagina: copre l'intestazione di tutte le bolle viste.
    crop = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.30)
    longest = max(crop.width, crop.height) or 1
    zoom = cfg.resize_px / longest
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=crop)
    png_bytes = pix.tobytes("png")

    if paddle_engine is not None:
        # OCR completo del crop: marker e impronta si estraggono dal testo.
        return paddle_engine.recognize_png(png_bytes)
    return _header_via_llama(png_bytes, cfg)


def _header_via_llama(png_bytes: bytes, cfg: OcrConfig) -> str:
    import requests  # type: ignore

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
        # 250 token: l'intestazione delle bolle (carta intestata + indirizzi)
        # puo' precedere il marker 'Pagina 1/N' nell'ordine di lettura; con un
        # tetto troppo basso il modello si ferma prima di arrivarci.
        "max_tokens": 250,
        "repeat_penalty": 1.3,
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
