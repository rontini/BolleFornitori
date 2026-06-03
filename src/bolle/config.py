"""Configurazione della pipeline.

Caricata da un file YAML (vedi config/settings.example.yaml) con override da
variabili d'ambiente. Nessun valore di default punta a servizi esterni: la
soluzione gira interamente in locale.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OcrConfig:
    engine: str = "paddleocr_vl"        # paddleocr_vl | glm_ocr | dots_ocr
    resize_px: int = 1024               # sweet spot per inferenza su CPU
    table_mode: bool = True
    confidence_threshold: float = 0.80  # sotto soglia -> coda di revisione
    # dots.ocr via llama.cpp (usato quando engine == "dots_ocr"). Funziona su CPU
    # senza AVX; si appoggia a un llama-server locale OpenAI-compatibile.
    dots_server_url: str = "http://localhost:8080"
    dots_model: str = "dots.ocr"
    request_timeout_s: int = 300


@dataclass
class LlmConfig:
    """LLM piccolo (Ollama) usato SOLO per testata e casi sporchi (~10%)."""

    enabled: bool = True
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct-q4_K_M"
    timeout_s: int = 60


@dataclass
class ApiConfig:
    """API REST aziendali: unico canale verso Oracle (ordini, cross-ref, anagrafica)."""

    base_url: str = "http://localhost:8080"
    token: str | None = None
    timeout_s: int = 30
    dry_run: bool = True                # in skeleton non scrive davvero su Oracle


@dataclass
class Paths:
    inbox: Path = Path("./inbox")
    work: Path = Path("./work")
    review_queue: Path = Path("./work/revisione")


@dataclass
class Config:
    ocr: OcrConfig = field(default_factory=OcrConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    paths: Paths = field(default_factory=Paths)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        data: dict[str, Any] = {}
        if path is not None:
            data = _read_yaml(Path(path))
        cfg = cls(
            ocr=OcrConfig(**data.get("ocr", {})),
            llm=LlmConfig(**data.get("llm", {})),
            api=ApiConfig(**data.get("api", {})),
            paths=_paths_from(data.get("paths", {})),
        )
        cfg._apply_env_overrides()
        return cfg

    def _apply_env_overrides(self) -> None:
        if v := os.environ.get("BOLLE_API_BASE_URL"):
            self.api.base_url = v
        if v := os.environ.get("BOLLE_API_TOKEN"):
            self.api.token = v
        if v := os.environ.get("BOLLE_LLM_BASE_URL"):
            self.llm.base_url = v


def _paths_from(d: dict[str, Any]) -> Paths:
    p = Paths()
    if "inbox" in d:
        p.inbox = Path(d["inbox"])
    if "work" in d:
        p.work = Path(d["work"])
    if "review_queue" in d:
        p.review_queue = Path(d["review_queue"])
    return p


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "PyYAML non installato: 'pip install pyyaml' oppure passa config=None"
        ) from exc
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
