"""Entry point CLI.

Esempi:
  python -m bolle.cli --config config/settings.yaml inbox/bolla.pdf
  python -m bolle.cli inbox/*.pdf            # elaborazione "man mano" a flusso
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .api_client import build_api
from .config import Config
from .models import EsitoRiconciliazione
from .pipeline import Pipeline
from .splitter import split_pdf


def _stampa_riepilogo(esiti: list[EsitoRiconciliazione], errori: list[str], out_path: Path) -> None:
    """Tabella di riepilogo a fine lotto + dump JSON in work/riepilogo.json.

    Con ~100 bolle/giorno serve una vista aggregata: quante righe risolte,
    quante in revisione, quali documenti sono andati in errore - senza aprire
    un file JSON per bolla.
    """
    if not esiti and not errori:
        return

    print("\n" + "=" * 78)
    print(f"{'documento':32} {'fornitore':22} {'righe':>6} {'ok':>4} {'rev':>4}")
    print("-" * 78)
    tot_righe = tot_ok = tot_rev = 0
    for e in esiti:
        doc = (e.documento_id or "?")[:32]
        forn = (e.fornitore or "-")[:22]
        n_rev = len(e.righe_in_revisione)
        print(f"{doc:32} {forn:22} {e.totale_righe:>6} {e.righe_risolte:>4} {n_rev:>4}")
        tot_righe += e.totale_righe
        tot_ok += e.righe_risolte
        tot_rev += n_rev
    print("-" * 78)
    print(f"{'TOTALE':32} {len(esiti):>3} bolle{'':13} {tot_righe:>6} {tot_ok:>4} {tot_rev:>4}")
    for doc in errori:
        print(f"  ERRORE: {doc}")
    print("=" * 78)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bolle": [
            {
                "documento_id": e.documento_id,
                "fornitore": e.fornitore,
                "numero_ordine": e.numero_ordine,
                "numero_ordine_fornitore": e.numero_ordine_fornitore,
                "totale_righe": e.totale_righe,
                "righe_risolte": e.righe_risolte,
                "righe_in_revisione": len(e.righe_in_revisione),
                "proposte": len(e.proposte),
            }
            for e in esiti
        ],
        "errori": errori,
        "totali": {
            "bolle": len(esiti),
            "righe": tot_righe,
            "risolte": tot_ok,
            "in_revisione": tot_rev,
        },
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"riepilogo salvato in {out_path}")


def _parse_pages(spec: str | None) -> list[int] | None:
    """'1', '1-3', '1,5,7' (1-based) -> lista di indici 0-based; None se non dato."""
    if not spec:
        return None
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [p - 1 for p in out]  # 1-based -> 0-based


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline bolle fornitori (locale)")
    parser.add_argument("documenti", nargs="+", help="file da elaborare (PDF/immagini/XML)")
    parser.add_argument("--config", default=None, help="path a settings.yaml")
    parser.add_argument(
        "--pages",
        default=None,
        help="pagine da elaborare, 1-based (es. '1', '1-3', '1,5,7'). Default: tutte",
    )
    parser.add_argument(
        "--no-split",
        action="store_true",
        help="non eseguire lo splitter multi-bolla: il file viene processato "
             "come una singola bolla. Utile quando passi PDF gia' divisi.",
    )
    parser.add_argument(
        "--reuse-ocr",
        action="store_true",
        help="non rieseguire l'OCR: ricarica il Markdown gia' prodotto in "
             "work/ocr_raw/<stem>.md e parte dal parser. Modalita' sviluppo "
             "per iterare sul parser/validazione in secondi invece di minuti.",
    )
    parser.add_argument(
        "--fornitore",
        default=None,
        help="forza il nome del fornitore in testata (override); utile quando "
             "l'OCR non lo trascrive e nessun pattern noto matcha.",
    )
    parser.add_argument(
        "--split-only",
        action="store_true",
        help="esegue SOLO lo splitter (divide il PDF e scrive i sidecar col "
             "fornitore), senza OCR ne' parsing. Veloce: OCR solo degli header.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    pages = _parse_pages(args.pages)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = Config.load(args.config)
    api = build_api(cfg.api)
    pipeline = Pipeline(cfg, api)

    # Modalita' "solo split": divide il PDF e scrive i sidecar (fornitore),
    # senza OCR/parsing. Utile per (ri)generare i collegamenti fornitore.
    if args.split_only:
        exit_code = 0
        for doc in args.documenti:
            try:
                parti = split_pdf(Path(doc), cfg.ocr, work_dir=cfg.paths.work / "split")
            except Exception as exc:  # noqa: BLE001 - un file non blocca il lotto
                logging.getLogger("bolle.cli").exception("split fallito su %s: %s", doc, exc)
                exit_code = 1
                continue
            for p in parti:
                meta = p.with_suffix(".meta.json")
                forn = ""
                if meta.exists():
                    forn = json.loads(meta.read_text(encoding="utf-8")).get("fornitore", "")
                print(f"  {p}  ->  fornitore: {forn or '(non rilevato)'}")
        return exit_code

    if pages is not None and args.reuse_ocr:
        # Il .md riusato contiene le pagine dell'OCR precedente: --pages non
        # puo' filtrarle a posteriori. Meglio dirlo che ignorarlo in silenzio.
        logging.getLogger("bolle.cli").warning(
            "--pages viene ignorato con --reuse-ocr: il .md riusato contiene "
            "le pagine dell'OCR originale"
        )

    exit_code = 0
    esiti: list[EsitoRiconciliazione] = []
    errori: list[str] = []
    for doc in args.documenti:
        # Splitter: saltiamo se l'utente ha passato --no-split o --reuse-ocr
        # (in entrambi i casi sta lavorando su un file gia' singolo), o --pages
        # (sta lavorando su pagine specifiche). Altrimenti proviamo a splittare
        # i PDF multi-bolla. E' un no-op se il PDF ha 1 pagina o se troviamo
        # un solo marker "Pagina 1/N".
        sub_documenti = [Path(doc)]
        salta_splitter = pages is not None or args.no_split or args.reuse_ocr
        if not salta_splitter and cfg.ocr.splitter_enabled:
            try:
                sub_documenti = split_pdf(Path(doc), cfg.ocr, work_dir=cfg.paths.work / "split")
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("bolle.cli").exception(
                    "splitter fallito su %s, procedo con il file intero: %s", doc, exc
                )

        for sub in sub_documenti:
            try:
                esito = pipeline.process(
                    sub,
                    pages=pages,
                    reuse_ocr=args.reuse_ocr,
                    fornitore_override=args.fornitore,
                )
                print(
                    f"\n=== {sub} (fornitore {esito.fornitore or '-'} | "
                    f"ordine cliente {esito.numero_ordine} | "
                    f"ordine fornitore {esito.numero_ordine_fornitore}) ==="
                )
                for p in esito.proposte:
                    print(f"  [{p.tipo.value}] {p.codice_interno or ''} {p.dettaglio}")
                if esito.righe_in_revisione:
                    print(f"  righe in revisione: {len(esito.righe_in_revisione)}")
                esiti.append(esito)
            except Exception as exc:  # noqa: BLE001 - un documento non deve bloccare il flusso
                logging.getLogger("bolle.cli").exception("errore su %s: %s", sub, exc)
                errori.append(str(sub))
                exit_code = 1

    # Riepilogo di lotto: sempre se c'e' piu' di una bolla o ci sono errori.
    if len(esiti) > 1 or errori:
        _stampa_riepilogo(esiti, errori, cfg.paths.work / "riepilogo.json")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
