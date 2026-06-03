"""Adapter PaddleOCR-VL (motore OCR primario).

Modello vision-language ~0.9B, Apache 2.0, modalita tabella dedicata, multilingua
(copre le bolle estere), CPU-friendly. Generalizza sui layout: nessun template
per fornitore.

NB: l'import di paddleocr e lazy. Lo skeleton e eseguibile e testabile anche senza
il modello installato; in quel caso recognize() solleva un errore esplicito che
indica cosa installare.
"""

from __future__ import annotations

from pathlib import Path

from ..config import OcrConfig
from .base import OcrEngine, OcrResult, TableCell, TableRow


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
                "e scarica il modello (vedi README)."
            ) from exc
        self._pipeline = PaddleOCRVL()

    def recognize(self, path: str | Path, pages: list[int] | None = None) -> OcrResult:
        self._ensure_loaded()
        image = _load_and_resize(path, self.cfg.resize_px)
        raw = self._pipeline.predict(image)  # type: ignore[union-attr]
        return _to_ocr_result(raw)


def _load_and_resize(path: str | Path, target_px: int):
    """Carica l'immagine e la ridimensiona a ~target_px sul lato lungo."""
    from PIL import Image  # type: ignore

    img = Image.open(path).convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > target_px:
        scale = target_px / longest
        img = img.resize((int(w * scale), int(h * scale)))
    return img


def _to_ocr_result(raw) -> OcrResult:
    """Normalizza l'output PaddleOCR-VL nello schema interno (TableRow/TableCell).

    Lo schema esatto va adattato alla versione del runtime in fase di validazione
    sui documenti reali; qui isoliamo la mappatura in un solo punto.
    """
    rows: list[TableRow] = []
    text_parts: list[str] = []
    for block in raw or []:
        for line in block.get("table", {}).get("rows", []):
            cells = [
                TableCell(text=str(c.get("text", "")), confidence=float(c.get("score", 1.0)))
                for c in line
            ]
            rows.append(TableRow(cells=cells))
        if t := block.get("text"):
            text_parts.append(str(t))
    return OcrResult(rows=rows, full_text="\n".join(text_parts))
