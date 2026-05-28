"""Riconoscimento automatico delle bolle fornitori e riconciliazione con gli ordini.

Pipeline locale (on-premise, CPU): OCR vision-language nello strato percettivo,
matching e riconciliazione completamente deterministici.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .config import Config
from .pipeline import Pipeline

__all__ = ["Config", "Pipeline", "__version__"]
