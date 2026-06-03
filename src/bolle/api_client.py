"""Client delle API REST aziendali.

Unico canale verso i sistemi aziendali (Oracle): ordini, cross reference,
anagrafica articoli, tabelle di ribaltamento. La pipeline NON parla mai con il DB
direttamente.

Sono definite:
  - AziendaApi: interfaccia (Protocol) usata dalla pipeline e dal resolver.
  - HttpAziendaApi: implementazione REST (lazy import di requests).
  - InMemoryAziendaApi: implementazione fittizia per sviluppo/test offline.

Il contratto preciso (endpoint, payload, auth) va concordato con l'IT: vedi
prossimi passi - "Definizione del contratto delle API Oracle".
"""

from __future__ import annotations

from typing import Protocol

from .config import ApiConfig
from .models import Bolla, RigaOrdine


class AziendaApi(Protocol):
    def cross_reference(self, fornitore: str | None, codice_fornitore: str) -> str | None: ...
    def esiste_articolo(self, codice_interno: str) -> bool: ...
    def righe_ordine(self, numero_ordine: str) -> list[RigaOrdine]: ...
    def ordini_aperti(self, fornitore: str | None, codice_interno: str) -> list[RigaOrdine]: ...
    def invia_bolla(self, bolla: Bolla) -> str: ...


class HttpAziendaApi:
    """Implementazione REST. Gli endpoint sono placeholder da allineare al contratto."""

    def __init__(self, base_url: str, token: str | None = None, timeout_s: int = 30, dry_run: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s
        self.dry_run = dry_run

    def _get(self, path: str, params: dict) -> dict | list:
        import requests  # type: ignore

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        resp = requests.get(f"{self.base_url}{path}", params=params, headers=headers, timeout=self.timeout_s)
        resp.raise_for_status()
        return resp.json()

    def cross_reference(self, fornitore: str | None, codice_fornitore: str) -> str | None:
        data = self._get("/crossref", {"fornitore": fornitore, "codice": codice_fornitore})
        return data.get("codice_interno") if isinstance(data, dict) else None

    def esiste_articolo(self, codice_interno: str) -> bool:
        data = self._get("/anagrafica", {"codice": codice_interno})
        return bool(data.get("esiste")) if isinstance(data, dict) else False

    def righe_ordine(self, numero_ordine: str) -> list[RigaOrdine]:
        data = self._get("/ordini/righe", {"numero_ordine": numero_ordine})
        return [_riga_ordine(d) for d in data] if isinstance(data, list) else []

    def ordini_aperti(self, fornitore: str | None, codice_interno: str) -> list[RigaOrdine]:
        data = self._get("/ordini/aperti", {"fornitore": fornitore, "codice": codice_interno})
        return [_riga_ordine(d) for d in data] if isinstance(data, list) else []

    def invia_bolla(self, bolla: Bolla) -> str:
        if self.dry_run:
            return f"DRY-RUN:{bolla.documento_id}"
        import requests  # type: ignore

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        resp = requests.post(
            f"{self.base_url}/bolle",
            json=_bolla_payload(bolla),
            headers=headers,
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        return resp.json().get("id", "")


class InMemoryAziendaApi:
    """Backend fittizio per sviluppo/test offline."""

    def __init__(
        self,
        crossref: dict[tuple[str | None, str], str] | None = None,
        anagrafica: set[str] | None = None,
        righe_ordine: dict[str, list[RigaOrdine]] | None = None,
        ordini_aperti: dict[tuple[str | None, str], list[RigaOrdine]] | None = None,
    ) -> None:
        self._crossref = crossref or {}
        self._anagrafica = anagrafica or set()
        self._righe_ordine = righe_ordine or {}
        self._ordini_aperti = ordini_aperti or {}
        self.inviate: list[Bolla] = []

    def cross_reference(self, fornitore: str | None, codice_fornitore: str) -> str | None:
        return self._crossref.get((fornitore, codice_fornitore)) or self._crossref.get((None, codice_fornitore))

    def esiste_articolo(self, codice_interno: str) -> bool:
        return codice_interno in self._anagrafica

    def righe_ordine(self, numero_ordine: str) -> list[RigaOrdine]:
        return self._righe_ordine.get(numero_ordine, [])

    def ordini_aperti(self, fornitore: str | None, codice_interno: str) -> list[RigaOrdine]:
        return self._ordini_aperti.get((fornitore, codice_interno), [])

    def invia_bolla(self, bolla: Bolla) -> str:
        self.inviate.append(bolla)
        return f"MEM:{bolla.documento_id}"


def build_api(cfg: ApiConfig) -> AziendaApi:
    """Seleziona il backend API in base alla configurazione.

    - "memory": backend in-memory, nessuna API esterna. I codici non si risolvono
      (tutto va in coda di revisione) e nulla viene scritto. Utile per validare
      l'OCR senza l'Oracle aziendale.
    - "http": API REST aziendali reali.
    """
    if cfg.backend == "memory":
        return InMemoryAziendaApi()
    if cfg.backend == "http":
        return HttpAziendaApi(
            base_url=cfg.base_url,
            token=cfg.token,
            timeout_s=cfg.timeout_s,
            dry_run=cfg.dry_run,
        )
    raise ValueError(f"backend API non supportato: {cfg.backend!r}")


def _riga_ordine(d: dict) -> RigaOrdine:
    from datetime import date
    from decimal import Decimal

    data_consegna = None
    if d.get("data_consegna"):
        data_consegna = date.fromisoformat(d["data_consegna"])
    return RigaOrdine(
        numero_ordine=str(d["numero_ordine"]),
        codice_interno=str(d["codice_interno"]),
        quantita_attesa=Decimal(str(d["quantita_attesa"])),
        data_consegna=data_consegna,
        fornitore=d.get("fornitore"),
    )


def _bolla_payload(bolla: Bolla) -> dict:
    return {
        "documento_id": bolla.documento_id,
        "testata": {
            "numero_ordine": bolla.testata.numero_ordine,
            "fornitore": bolla.testata.fornitore,
            "numero_bolla": bolla.testata.numero_bolla,
            "data_bolla": bolla.testata.data_bolla.isoformat() if bolla.testata.data_bolla else None,
        },
        "righe": [
            {
                "numero_riga": r.numero_riga,
                "codice_interno": r.codice_interno,
                "codice_letto": r.codice_letto,
                "quantita": str(r.quantita) if r.quantita is not None else None,
                "prezzo_unitario": str(r.prezzo_unitario) if r.prezzo_unitario is not None else None,
                "totale_riga": str(r.totale_riga) if r.totale_riga is not None else None,
            }
            for r in bolla.righe
            if r.risolto
        ],
    }
