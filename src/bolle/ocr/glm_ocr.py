"""Adapter GLM-OCR (motore OCR di confronto).

Modello ~0.9B, top su OmniDocBench, pensato per girare su laptop. In fase di
validazione va confrontato con PaddleOCR-VL sulle bolle reali (accuratezza su
codice e quantita): vince il modello sul dato reale.

Import lazy: lo skeleton resta eseguibile anche senza il modello installato.
NB: l'API esatta del runtime GLM-OCR va confermata in fase di validazione; la
mappatura dell'output verso lo schema interno e isolata in _to_ocr_result.
"""

from __future__ import annotations

from pathlib import Path

from ..config import OcrConfig
from .base import OcrEngine, OcrResult, TableCell, TableRow
from .paddleocr_vl import _load_and_resize


class GlmOcrEngine(OcrEngine):
    def __init__(self, cfg: OcrConfig) -> None:
        self.cfg = cfg
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            from glmocr import GLMOCR  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "GLM-OCR non disponibile. Installa il runtime del modello "
                "(vedi docs/INSTALL.md) prima del benchmark."
            ) from exc
        self._model = GLMOCR()

    def recognize(self, path: str | Path) -> OcrResult:
        self._ensure_loaded()
        image = _load_and_resize(path, self.cfg.resize_px)
        raw = self._model.predict(image)  # type: ignore[union-attr]
        return _to_ocr_result(raw)


def _to_ocr_result(raw) -> OcrResult:
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
