"""Entry point CLI.

Esempi:
  python -m bolle.cli --config config/settings.yaml inbox/bolla.pdf
  python -m bolle.cli inbox/*.pdf            # elaborazione "man mano" a flusso
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .api_client import HttpAziendaApi
from .config import Config
from .pipeline import Pipeline


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
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    pages = _parse_pages(args.pages)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = Config.load(args.config)
    api = HttpAziendaApi(
        base_url=cfg.api.base_url,
        token=cfg.api.token,
        timeout_s=cfg.api.timeout_s,
        dry_run=cfg.api.dry_run,
    )
    pipeline = Pipeline(cfg, api)

    exit_code = 0
    for doc in args.documenti:
        try:
            esito = pipeline.process(Path(doc), pages=pages)
            print(f"\n=== {doc} (ordine {esito.numero_ordine}) ===")
            for p in esito.proposte:
                print(f"  [{p.tipo.value}] {p.codice_interno or ''} {p.dettaglio}")
            if esito.righe_in_revisione:
                print(f"  righe in revisione: {len(esito.righe_in_revisione)}")
        except Exception as exc:  # noqa: BLE001 - un documento non deve bloccare il flusso
            logging.getLogger("bolle.cli").exception("errore su %s: %s", doc, exc)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
