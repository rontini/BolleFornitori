"""Orchestratore della pipeline per singola bolla.

Catena: router -> (PDF text | OCR) -> testata (LLM) -> parsing righe ->
validazione/risoluzione codici -> invio API -> riconciliazione -> coda revisione.

L'elaborazione e "man mano" (a flusso): nessun requisito real-time.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import fatturapa, router
from .api_client import AziendaApi
from .config import Config
from .header_extractor import extract_header
from .models import Bolla, DocumentKind, EsitoRiconciliazione
from .ocr import build_engine
from .ocr.base import OcrResult
from .parsing import parse_lines
from .reconciliation import riconcilia
from .review_queue import accoda
from .validation import CodiceResolver, valida_bolla

log = logging.getLogger("bolle.pipeline")

# Motori che producono Markdown (pagine separate da \f): per questi si usa il
# parser per-nome-colonna (parse_articoli), NON quello generico posizionale.
_MARKDOWN_ENGINES = {"dots_ocr", "paddleocr_vl"}


class _ApiResolver:
    """Adatta AziendaApi all'interfaccia CodiceResolver usata dalla validazione."""

    def __init__(self, api: AziendaApi) -> None:
        self._api = api

    def da_cross_reference(self, fornitore: str | None, codice_fornitore: str) -> str | None:
        return self._api.cross_reference(fornitore, codice_fornitore)

    def esiste_in_anagrafica(self, codice_interno: str) -> bool:
        return self._api.esiste_articolo(codice_interno)


class Pipeline:
    def __init__(self, cfg: Config, api: AziendaApi) -> None:
        self.cfg = cfg
        self.api = api
        self._resolver: CodiceResolver = _ApiResolver(api)
        self._ocr_engine = None  # lazy: si costruisce solo se serve l'OCR

    def process(
        self,
        path: str | Path,
        pages: list[int] | None = None,
        reuse_ocr: bool = False,
        fornitore_override: str | None = None,
    ) -> EsitoRiconciliazione:
        """Elabora un documento.

        Se reuse_ocr=True salta lo strato OCR e ricarica il Markdown gia'
        prodotto in work/ocr_raw/<stem>.md: utile in sviluppo per iterare sul
        parser/validazione senza ri-eseguire l'OCR (secondi vs. minuti).
        """
        path = Path(path)
        kind = router.classify(path)
        log.info("documento %s classificato come %s", path.name, kind.value)

        if kind == DocumentKind.XML_FATTURAPA:
            bolla = fatturapa.parse(path)
        else:
            if reuse_ocr:
                md_path = self.cfg.paths.work / "ocr_raw" / f"{path.stem}.md"
                if not md_path.exists():
                    raise FileNotFoundError(
                        f"--reuse-ocr: nessun OCR precedente trovato in {md_path}"
                    )
                log.info("riuso OCR esistente da %s", md_path)
                ocr = OcrResult(rows=[], full_text=md_path.read_text(encoding="utf-8"))
            else:
                ocr = self._read_document(path, kind, pages)
                self._dump_ocr(path.stem, ocr.full_text)
            bolla = Bolla(documento_id=path.stem, kind=kind)
            bolla.testata = extract_header(
                ocr.full_text,
                self.cfg.llm,
                fornitori_noti=self.cfg.ocr.fornitori_noti,
            )
            bolla.righe = self._parse_righe(ocr)

        # Override esplicito del fornitore (precedenza su qualsiasi estrazione).
        if fornitore_override:
            bolla.testata.fornitore = fornitore_override

        in_revisione = valida_bolla(bolla, self._resolver)
        log.info(
            "%s: %d righe, %d in revisione", path.name, len(bolla.righe), len(in_revisione)
        )

        self.api.invia_bolla(bolla)
        esito = riconcilia(bolla, self.api)
        self._dump_esito(bolla)

        if esito.righe_in_revisione or any(
            p.tipo.value == "revisione" for p in esito.proposte
        ):
            accoda(esito, bolla.documento_id, self.cfg.paths.review_queue)
        return esito

    def _parse_righe(self, ocr: OcrResult):
        """Per i motori Markdown usa SOLO il parser per-nome-colonna.

        Niente fallback al parser generico quando parse_articoli non trova
        nulla: il generico non ha il filtro sul codice articolo e produce
        righe spazzatura dai pezzi di testata (es. '26DT-01997', 'C0088').
        Meglio zero righe oneste che righe inventate."""
        if self.cfg.ocr.engine in _MARKDOWN_ENGINES and ocr.full_text:
            from .ocr.dots_ocr import parse_articoli

            return parse_articoli(ocr.full_text)
        return parse_lines(ocr, self.cfg.ocr.confidence_threshold)

    def _dump_esito(self, bolla: Bolla) -> None:
        """Salva l'esito COMPLETO (testata + tutte le righe, risolte e non) in
        work/output/<id>.json. In coda di revisione finiscono solo le righe non
        risolte: senza questo dump le righe risolte non sarebbero ispezionabili
        quando si gira in dry-run / senza API."""
        import json
        from dataclasses import asdict

        from .review_queue import _json_default

        out_dir = self.cfg.paths.work / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "documento_id": bolla.documento_id,
            "testata": asdict(bolla.testata),
            "righe": [asdict(r) for r in bolla.righe],
        }
        path = out_dir / f"{bolla.documento_id}.json"
        path.write_text(
            json.dumps(payload, default=_json_default, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("esito completo salvato in %s", path)

    def _dump_ocr(self, stem: str, full_text: str) -> None:
        """Salva l'output grezzo dell'OCR (Markdown) per ispezione/taratura parser."""
        if not full_text:
            return
        out_dir = self.cfg.paths.work / "ocr_raw"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{stem}.md"
        path.write_text(full_text, encoding="utf-8")
        log.info("output OCR grezzo salvato in %s", path)

    def _read_document(
        self, path: Path, kind: DocumentKind, pages: list[int] | None = None
    ) -> OcrResult:
        if kind == DocumentKind.PDF_TEXT:
            from .ocr import pdf_text

            return pdf_text.extract(path)
        if kind == DocumentKind.PDF_SCAN:
            if self._ocr_engine is None:
                self._ocr_engine = build_engine(self.cfg.ocr)
            return self._ocr_engine.recognize(path, pages)
        raise ValueError(f"tipo documento non gestito: {kind}")
