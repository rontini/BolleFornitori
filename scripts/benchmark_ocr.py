"""Benchmark OCR: misura l'accuratezza su CODICE e QUANTITA sui documenti reali.

La metrica per la scelta del modello NON e il punteggio di layout, ma quante volte
codice e quantita vengono letti correttamente (analisi, sez. 6.1). Questo script
gira uno o piu motori OCR su una cartella di documenti, allinea le righe lette al
ground-truth e stampa, per motore: % codici esatti, % quantita esatte e l'errore
medio carattere-per-carattere sui codici (CER).

Uso:
  python scripts/benchmark_ocr.py samples/ \\
      --ground-truth samples/ground_truth.json \\
      --engines paddleocr_vl glm_ocr

Ground-truth (JSON): { "bolla_01.pdf": [ {"codice": "AB12", "quantita": "10"}, ... ] }
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bolle import fatturapa, router  # noqa: E402
from bolle.config import OcrConfig  # noqa: E402
from bolle.models import DocumentKind, RigaBolla  # noqa: E402
from bolle.ocr import build_engine, pdf_text  # noqa: E402
from bolle.parsing import parse_lines  # noqa: E402


def leggi_righe(path: Path, engine, cfg: OcrConfig) -> list[RigaBolla]:
    kind = router.classify(path)
    if kind == DocumentKind.XML_FATTURAPA:
        return fatturapa.parse(path).righe
    if kind == DocumentKind.PDF_TEXT:
        ocr = pdf_text.extract(path)
    else:
        ocr = engine.recognize(path)  # costruito lazy solo quando serve l'OCR
    return parse_lines(ocr, cfg.confidence_threshold)


def _quantita(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        return None


def _cer(atteso: str, letto: str) -> float:
    """Character Error Rate = distanza di Levenshtein / lunghezza attesa."""
    if not atteso:
        return 0.0 if not letto else 1.0
    prev = list(range(len(letto) + 1))
    for i, ca in enumerate(atteso, start=1):
        cur = [i]
        for j, cl in enumerate(letto, start=1):
            cost = 0 if ca == cl else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1] / len(atteso)


class Conteggi:
    def __init__(self) -> None:
        self.righe = 0
        self.codice_ok = 0
        self.quantita_ok = 0
        self.cer_somma = 0.0

    def aggiungi(self, atteso: dict, letta: RigaBolla | None) -> None:
        self.righe += 1
        cod_atteso = str(atteso.get("codice", "")).strip()
        cod_letto = (letta.codice_letto if letta else "") or ""
        if cod_atteso.upper() == cod_letto.strip().upper():
            self.codice_ok += 1
        self.cer_somma += _cer(cod_atteso, cod_letto.strip())

        qta_attesa = _quantita(atteso.get("quantita"))
        qta_letta = letta.quantita if letta else None
        if qta_attesa is not None and qta_letta is not None and qta_attesa == qta_letta:
            self.quantita_ok += 1

    def stampa(self, engine: str) -> None:
        r = self.righe or 1
        print(f"\n=== {engine} ===")
        print(f"  righe valutate : {self.righe}")
        print(f"  codice esatto  : {self.codice_ok}/{self.righe}  ({100 * self.codice_ok / r:.1f}%)")
        print(f"  quantita esatta: {self.quantita_ok}/{self.righe}  ({100 * self.quantita_ok / r:.1f}%)")
        print(f"  CER codice     : {self.cer_somma / r:.3f}  (0 = perfetto)")


def valuta_engine(engine_name: str, docs: dict[str, list[dict]], samples_dir: Path) -> Conteggi:
    cfg = OcrConfig(engine=engine_name)
    engine = build_engine(cfg)
    conteggi = Conteggi()
    for nome_file, attese in docs.items():
        path = samples_dir / nome_file
        if not path.exists():
            print(f"  [skip] {nome_file}: file non trovato", file=sys.stderr)
            continue
        try:
            lette = leggi_righe(path, engine, cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"  [errore] {nome_file}: {exc}", file=sys.stderr)
            continue
        for i, atteso in enumerate(attese):
            conteggi.aggiungi(atteso, lette[i] if i < len(lette) else None)
    return conteggi


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark OCR su codice e quantita")
    parser.add_argument("samples", help="cartella con i documenti di prova")
    parser.add_argument("--ground-truth", required=True, help="JSON con le righe attese per file")
    parser.add_argument("--engines", nargs="+", default=["paddleocr_vl", "glm_ocr"])
    args = parser.parse_args(argv)

    samples_dir = Path(args.samples)
    docs = json.loads(Path(args.ground_truth).read_text(encoding="utf-8"))

    for engine_name in args.engines:
        try:
            valuta_engine(engine_name, docs, samples_dir).stampa(engine_name)
        except Exception as exc:  # noqa: BLE001
            print(f"\n=== {engine_name} ===\n  non disponibile: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
