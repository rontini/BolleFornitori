"""Strato percettivo: trasforma carta/PDF in righe di tabella strutturate.

L'AI vive solo qui. Tutto cio che segue (matching, riconciliazione) e
deterministico e verificabile.
"""

from __future__ import annotations

from ..config import OcrConfig
from .base import OcrEngine, OcrResult, TableCell, TableRow


def build_engine(cfg: OcrConfig) -> OcrEngine:
    """Factory: istanzia il motore OCR scelto in configurazione."""
    if cfg.engine == "paddleocr_vl":
        from .paddleocr_vl import PaddleOcrVlEngine

        return PaddleOcrVlEngine(cfg)
    raise ValueError(f"Motore OCR non supportato: {cfg.engine!r}")


__all__ = [
    "OcrEngine",
    "OcrResult",
    "TableCell",
    "TableRow",
    "build_engine",
]
