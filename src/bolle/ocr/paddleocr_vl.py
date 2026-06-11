"""Adapter PaddleOCR-VL (motore OCR primario su macchine con AVX).

Modello vision-language ~0.9B, Apache 2.0, modalita' tabella dedicata,
multilingua, scelto come primario dall'analisi. Richiede PaddlePaddle, i cui
wheel Windows/Linux sono compilati con AVX: su macchine/VM senza AVX usare il
motore `dots_ocr` (llama.cpp).

Strategia identica al motore dots: ogni pagina viene rasterizzata e convertita
in MARKDOWN; le pagine sono separate da \f cosi' parse_articoli() le processa
come segmenti autonomi. Tutto il parsing robusto (tabelle per nome colonna,
freetext, formato Camozzi/Vs.CODICE, dedup) e' riusato senza modifiche.

NB: l'import di paddleocr e' lazy: lo skeleton resta importabile e testabile
anche senza il runtime installato.
"""

from __future__ import annotations

from pathlib import Path

from ..config import OcrConfig
from .base import OcrEngine, OcrResult


class PaddleOcrVlEngine(OcrEngine):
    def __init__(self, cfg: OcrConfig) -> None:
        self.cfg = cfg
        self._pipeline = None

    def _ensure_loaded(self) -> None:
        if self._pipeline is not None:
            return
        try:
            from paddleocr import PaddleOCRVL  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "PaddleOCR-VL non disponibile. Installa con:\n"
                "  pip install paddlepaddle paddleocr\n"
                "(richiede CPU con AVX; in alternativa usa ocr.engine=dots_ocr)."
            ) from exc
        self._pipeline = PaddleOCRVL()

    def recognize(self, path: str | Path, pages: list[int] | None = None) -> OcrResult:
        from .dots_ocr import _render_pages  # riusa rasterizzazione PDF/immagini

        self._ensure_loaded()
        rendered = _render_pages(Path(path), self.cfg.resize_px, pages)
        parts: list[str] = []
        for page_no, png in rendered:
            parts.append(self.recognize_png(png))
        # \f separa le pagine: parse_articoli le processa come segmenti autonomi.
        return OcrResult(rows=[], full_text="\n\f\n".join(parts))

    def recognize_png(self, png_bytes: bytes) -> str:
        """OCR di una singola immagine PNG -> Markdown. Usato anche dallo splitter."""
        from io import BytesIO

        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore

        self._ensure_loaded()
        img = np.array(Image.open(BytesIO(png_bytes)).convert("RGB"))
        results = self._pipeline.predict(img)  # type: ignore[union-attr]
        return "\n".join(_markdown_da_risultato(r) for r in results or [])


def _markdown_da_risultato(res) -> str:
    """Normalizza un risultato PaddleOCR-VL in testo Markdown.

    Lo schema dell'oggetto risultato varia fra versioni del runtime (attributo
    `markdown` come stringa, dict, o metodo): qui isoliamo la mappatura in un
    solo punto, con fallback progressivi.
    """
    md = getattr(res, "markdown", None)
    if callable(md):  # alcune versioni lo espongono come metodo
        try:
            md = md()
        except TypeError:
            md = None
    if isinstance(md, str) and md.strip():
        return md
    if isinstance(md, dict):
        for key in ("markdown_texts", "markdown", "text"):
            v = md.get(key)
            if isinstance(v, str) and v.strip():
                return v
        stringhe = [v for v in md.values() if isinstance(v, str) and v.strip()]
        if stringhe:
            return "\n".join(stringhe)
    j = getattr(res, "json", None)
    if isinstance(j, dict):
        import json as _json

        return _json.dumps(j, ensure_ascii=False)
    return str(res)
