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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline bolle fornitori (locale)")
    parser.add_argument("documenti", nargs="+", help="file da elaborare (PDF/immagini/XML)")
    parser.add_argument("--config", default=None, help="path a settings.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

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
            esito = pipeline.process(Path(doc))
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
