"""Interfaccia comune dei motori OCR.

Permette di confrontare PaddleOCR-VL, GLM-OCR e dots.ocr sullo stesso codice
(vedi prossimi passi: validazione su 20-30 bolle reali) senza toccare la pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TableCell:
    text: str
    confidence: float = 1.0


@dataclass
class TableRow:
    cells: list[TableCell] = field(default_factory=list)

    def text_at(self, idx: int) -> str:
        return self.cells[idx].text if 0 <= idx < len(self.cells) else ""

    def min_confidence(self) -> float:
        return min((c.confidence for c in self.cells), default=1.0)


@dataclass
class OcrResult:
    rows: list[TableRow] = field(default_factory=list)
    full_text: str = ""             # testo grezzo, utile per la testata


class OcrEngine(ABC):
    @abstractmethod
    def recognize(self, path: str | Path, pages: list[int] | None = None) -> OcrResult:
        """Esegue OCR in modalita tabella su un documento.

        pages: indici di pagina 0-based da elaborare (None = tutte). Utile per
        provare/tarare su una singola pagina senza attendere l'intero PDF.
        """
        raise NotImplementedError
