"""Adapter dots.ocr / GLM-OCR tramite llama.cpp (motore OCR per CPU senza AVX).

Si appoggia a un `llama-server` locale con API OpenAI-compatibile e supporto
immagini (--mmproj). Funziona con qualsiasi modello OCR multimodale servito da
llama-server (GLM-OCR, dots.ocr, ...). Nessun dato esce dalla macchina.

Le pagine PDF vengono rasterizzate (PyMuPDF) e ridimensionate a ~resize_px; ogni
pagina viene inviata al modello che restituisce il documento in Markdown. Da li'
si ricavano testo (per la testata) e righe (dalle tabelle Markdown).

La risposta viene letta in STREAMING (Server-Sent Events): i token arrivano in
continuazione, quindi non si incappa nel timeout di lettura su CPU lente, e si
puo' mostrare l'avanzamento per pagina.

NB: import di requests/fitz pigro -> lo skeleton resta importabile senza runtime.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import OcrConfig
from .base import OcrEngine, OcrResult, TableCell, TableRow

log = logging.getLogger("bolle.ocr.dots")

_PROMPT = (
    "Trascrivi la tabella articoli di questa bolla come Markdown a pipe.\n"
    "\n"
    "Usa ESATTAMENTE le intestazioni presenti sulla pagina (es. | Nr. | "
    "Descrizione | Quantita | U.d.M. |). Inserisci sotto le intestazioni la "
    "riga separatrice | --- | --- | --- | --- |.\n"
    "\n"
    "Una riga per ogni articolo, copiando fedelmente codice (prima colonna), "
    "descrizione e quantita esattamente come scritti sulla pagina. La colonna "
    "Quantita contiene il NUMERO ordinato (es. 18, 3, 32, 19), eventualmente "
    "seguito dall'unita' di misura (es. '18 NR'). NON e' un codice articolo. "
    "Non saltare righe. Non riassumere.\n"
    "\n"
    "Niente testo prima o dopo la tabella. Niente sezioni colli/peso/firme/vettore."
)


class DotsOcrEngine(OcrEngine):
    def __init__(self, cfg: OcrConfig) -> None:
        self.cfg = cfg

    def recognize(self, path: str | Path, pages: list[int] | None = None) -> OcrResult:
        rendered = _render_pages(Path(path), self.cfg.resize_px, pages)
        total = len(rendered)
        text_parts: list[str] = []
        rows: list[TableRow] = []
        for i, (page_no, png) in enumerate(rendered, start=1):
            log.info("OCR pagina %d (%d/%d selezionate)", page_no, i, total)
            # Singola chiamata focalizzata sulla tabella: due chiamate (testata
            # + tabella) hanno spinto il modello in modalita' prosa e abbiamo
            # perso le quantita'. La singola chiamata con questo prompt e' stata
            # gia' dimostrata in grado di produrre una tabella pulita con
            # quantita' nei test di ieri.
            markdown = self._call_server(
                png, page_no, "ocr", _PROMPT, max_tokens=self.cfg.dots_max_tokens
            )

            # Recupero quantita' mancanti: se troviamo codici ma il modello
            # ha omesso le quantita', facciamo una seconda chiamata mirata
            # sulla stessa immagine elencandogli i codici e chiedendo solo
            # i numeri. Su CPU senza AVX e' l'unico modo affidabile di
            # estrarre quel dato che il modello tende a saltare.
            codici = _codici_in_markdown(markdown)
            if codici and not _ha_quantita(markdown):
                log.info("OCR pagina %d: 2a passata mirata sulle quantita'", page_no)
                qta_md = self._call_server(
                    png,
                    page_no,
                    "qta",
                    _build_prompt_quantita(codici),
                    max_tokens=400,
                )
                markdown = markdown + "\n\n" + qta_md

            text_parts.append(markdown)
            rows.extend(_markdown_to_rows(markdown))
        # \f (form feed) separa le pagine: parse_articoli processa ogni pagina
        # come segmento autonomo (tabelle e patch quantita' restano locali alla
        # pagina, invece di mescolarsi su documenti multi-pagina).
        return OcrResult(rows=rows, full_text="\n\f\n".join(text_parts))

    def _call_server(
        self, png_bytes: bytes, page_no: int, fase: str, prompt: str, max_tokens: int
    ) -> str:
        import requests  # type: ignore

        b64 = base64.b64encode(png_bytes).decode("ascii")
        payload = {
            "model": self.cfg.dots_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            # Anti-loop: senza penalita' il modello quantizzato su CPU tende a
            # ripetere la stessa riga di tabella centinaia di volte fino a
            # saturare max_tokens (visto sulle pagine SOFT/Camozzi del PDF di
            # prova). 1.2 rompe i loop senza penalizzare le ripetizioni
            # legittime della struttura tabellare (pipe, unita' 'NR').
            "repeat_penalty": 1.2,
            "stream": True,
        }
        url = f"{self.cfg.dots_server_url.rstrip('/')}/v1/chat/completions"
        parts: list[str] = []
        n_tok = 0
        # connect-timeout breve (server giu' -> errore rapido); read-timeout per
        # singolo chunk: in streaming i token arrivano di continuo.
        with requests.post(
            url, json=payload, stream=True, timeout=(10, self.cfg.request_timeout_s)
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines(decode_unicode=False):
                delta = _delta_from_sse_line(raw)
                if delta is None:
                    continue
                parts.append(delta)
                n_tok += 1
                _progress(page_no, fase, n_tok)
        _progress_end()
        return "".join(parts)


def _delta_from_sse_line(raw: bytes) -> str | None:
    """Estrae il pezzo di testo da una riga SSE `data: {...}`; None se non pertinente."""
    if not raw or not raw.startswith(b"data:"):
        return None
    data = raw[len(b"data:"):].strip()
    if not data or data == b"[DONE]":
        return None
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None
    choices = obj.get("choices") or [{}]
    return choices[0].get("delta", {}).get("content") or None


def _progress(page_no: int, fase: str, n_tok: int) -> None:
    sys.stderr.write(f"\r  pagina {page_no} [{fase}] · token letti: {n_tok}   ")
    sys.stderr.flush()


def _progress_end() -> None:
    sys.stderr.write("\n")
    sys.stderr.flush()


def _render_pages(
    path: Path, target_px: int, pages: list[int] | None = None
) -> list[tuple[int, bytes]]:
    """Restituisce (numero_pagina_1based, PNG). pages: indici 0-based, None = tutte."""
    if path.suffix.lower() == ".pdf":
        import fitz  # PyMuPDF

        out: list[tuple[int, bytes]] = []
        with fitz.open(path) as doc:
            if pages is None:
                indici = range(len(doc))
            else:
                indici = [i for i in pages if 0 <= i < len(doc)]
            for i in indici:
                page = doc[i]
                longest = max(page.rect.width, page.rect.height) or 1
                zoom = target_px / longest
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                out.append((i + 1, pix.tobytes("png")))
        return out

    from io import BytesIO

    from PIL import Image  # type: ignore

    img = Image.open(path).convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > target_px:
        scale = target_px / longest
        img = img.resize((int(w * scale), int(h * scale)))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return [(1, buf.getvalue())]


_SEP_CELL = re.compile(r"^:?-{2,}:?$")

# Sinonimi di intestazione che il modello puo' produrre, per colonna logica.
_HEADER_ALIASES = {
    "codice": ("codice", "nr.", "nr", "codice articolo", "articolo", "n.", "n"),
    "descrizione": ("descrizione", "denominazione"),
    "quantita": ("quantita", "quantità", "qta", "q.ta", "qty"),
    "udm": ("udm", "u.d.m.", "u.m.", "um"),
    "prezzo": ("prezzo", "prezzo unitario", "prezzo unit."),
    "totale": ("totale", "importo", "totale riga"),
}


def _normalize_header(text: str) -> str | None:
    """Mappa un testo di intestazione a uno dei nomi logici di colonna (o None)."""
    t = text.strip().lower().rstrip(".:")
    for key, aliases in _HEADER_ALIASES.items():
        if t in aliases:
            return key
    return None


@dataclass
class _Table:
    columns: list[str | None]                # nome logico per colonna (None = ignota)
    rows: list[list[str]]                    # celle delle sole righe dati


def _extract_articoli_table(markdown: str) -> _Table | None:
    """Trova la PRIMA tabella articoli (compatibilita': vedi _extract_all_tables)."""
    tables = _extract_all_tables(markdown)
    return tables[0] if tables else None


def _extract_all_tables(markdown: str) -> list[_Table]:
    """Estrae TUTTE le tabelle Markdown del testo, ognuna con il suo header.

    Le pagine reali contengono spesso piu' tabelle (una fasulla con dati di
    testata e una vera con gli articoli): fermarsi alla prima fa perdere le
    righe buone. I blocchi sono sequenze di righe `|...|` consecutive; l'header
    e' segnato dalla riga separatrice `| --- |` o, se manca, dalla prima riga
    con almeno un'intestazione conosciuta.
    """
    tables: list[_Table] = []
    block: list[str] = []
    for line in markdown.splitlines() + [""]:
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            block.append(s)
            continue
        if len(block) >= 2:
            t = _table_from_block(block)
            if t is not None:
                tables.append(t)
        block = []
    return tables


def _table_from_block(block: list[str]) -> _Table | None:
    rows_cells = [[c.strip() for c in b.strip("|").split("|")] for b in block]

    header_idx: int | None = None
    data_start = 0
    for i, cells in enumerate(rows_cells):
        if cells and all(_SEP_CELL.match(c) for c in cells if c) and any(cells):
            header_idx = i - 1
            data_start = i + 1
            break
    if header_idx is None or header_idx < 0:
        # Senza riga separatrice: la prima riga e' header solo se riconoscibile.
        if any(_normalize_header(c) for c in rows_cells[0]):
            header_idx, data_start = 0, 1
        else:
            return None

    columns = [_normalize_header(c) for c in rows_cells[header_idx]]
    n_cols = len(rows_cells[header_idx])

    rows: list[list[str]] = []
    for cells in rows_cells[data_start:]:
        if len(cells) != n_cols:
            break  # struttura cambiata: tabella finita
        if _is_non_articolo(cells, columns):
            break
        if all(not c for c in cells):
            continue
        rows.append(cells)
    return _Table(columns=columns, rows=rows)


_NON_ARTICOLO_HINTS = (
    "colli",
    "peso",
    "vettore",
    "trasporto",
    "destinatario",
    "firma",
    "asporto",
    "spediz",
    "località",
    "localita",
    "volume",
)


def _is_non_articolo(cells: list[str], columns: list[str | None]) -> bool:
    """True se la riga e' chiaramente del blocco logistico, non un articolo."""
    joined = " ".join(c.lower() for c in cells)
    return any(h in joined for h in _NON_ARTICOLO_HINTS)


_RE_NUMERO = re.compile(r"-?\d{1,3}(?:[.\s]?\d{3})*(?:[.,]\d+)?")


def _decimale(text: str) -> "Decimal | None":
    from decimal import Decimal, InvalidOperation

    if not text:
        return None
    m = _RE_NUMERO.search(text)
    if not m:
        return None
    norm = m.group(0).replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return Decimal(norm)
    except InvalidOperation:
        return None


# Codice articolo plausibile: solo i pattern reali osservati nelle bolle:
# - dddddd.dddd       (es. 088578.0163: codice fornitore)
# - ddddddXXX.dddd    (es. 088552RIP.0077: variante con suffisso lettere, riparazioni)
# - dddddddd          (es. 99951827: codice commerciale/cliente)
# Esclude alfanumerici generici (numeri DDT, ordini) E P.IVA/codice fiscale
# italiani (11 cifre), che altrimenti finiscono come "articoli" dalle finte
# tabelle di testata prodotte dal modello.
_RE_CODICE_ARTICOLO = re.compile(r"^(?:\d{6}[A-Z]{0,4}\.\d{4}|\d{8})$", re.IGNORECASE)


def _is_codice_articolo(text: str) -> bool:
    return bool(_RE_CODICE_ARTICOLO.match(text.strip()))


# Linee dalla 2a passata mirata sulle quantita'. Due formati accettati:
#  - quello che chiediamo nel prompt: "QTA:codice=numero"
#  - quello che il modello a volte preferisce: "<codice> <numero> [unita]"
#    (es. "088578.0163 18 NR")
_RE_QTA_KEY = re.compile(r"QTA\s*:\s*(?P<codice>\S+?)\s*=\s*(?P<qta>\d+(?:[.,]\d+)?)", re.I)
_RE_QTA_FREEFORM = re.compile(
    r"^\s*(?P<codice>\d{6}\.\d{4}|\d{8})\s+(?P<qta>\d+(?:[.,]\d+)?)\s*(?:NR|PZ|N|KG|MT)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _codici_in_markdown(markdown: str) -> list[str]:
    """Estrae i codici articolo dalle righe del markdown (dotted o 8 cifre, a inizio riga)."""
    visti: set[str] = set()
    out: list[str] = []
    for line in markdown.splitlines():
        s = line.strip().strip("|").strip()
        m = re.match(r"^\s*(\d{8}|\d{6}\.\d{4})(?!\d)", s)
        if m:
            cod = m.group(1)
            if cod not in visti:
                visti.add(cod)
                out.append(cod)
    return out


def _ha_quantita(markdown: str) -> bool:
    """Heuristic: il modello ha trascritto quantita' se troviamo un numero piccolo
    in una cella pipe (es. '| 18 | NR |') o seguito da un'unita' in testo libero
    (es. '18 NR' a fine riga)."""
    if re.search(r"\|\s*\d{1,4}\s*\|\s*(?:NR|PZ|N|KG|MT)\b", markdown, re.IGNORECASE):
        return True
    if re.search(r"\b\d{1,4}\s+(?:NR|PZ|N|KG|MT)\b", markdown):
        return True
    return False


def _build_prompt_quantita(codici: list[str]) -> str:
    elenco = "\n".join(codici)
    return (
        "Guarda la tabella articoli di questa bolla. Per ciascuno dei codici "
        "qui sotto, leggi il NUMERO presente nella colonna 'Quantita' (a destra "
        "della descrizione). Ignora eventuali spunte (es. '√') e considera solo "
        "il numero, anche se seguito da unita' di misura tipo 'NR'.\n"
        "\n"
        "Rispondi UNA riga per codice nel formato ESATTO:\n"
        "QTA:<codice>=<numero>\n"
        "\n"
        "Niente altro testo. Codici:\n"
        f"{elenco}"
    )


def _apply_qta_patches(rows: list["RigaBolla"], markdown: str) -> None:
    """Applica le quantita' lette dalla 2a passata mirata.

    Strategia in due tempi:
      1. Match esatto per codice (per quando 1a e 2a passata usano lo stesso
         codice articolo).
      2. Fallback per POSIZIONE (per quando la 2a passata legge codici diversi
         da quelli della 1a, ma in stesso numero e ordine: e' il caso comune
         delle bolle con due codici per articolo).
    """
    if not rows:
        return

    # Preferenza: formato 'QTA:codice=N'. Se assente, usa "codice numero unit".
    sources = list(_RE_QTA_KEY.finditer(markdown))
    if not sources:
        sources = list(_RE_QTA_FREEFORM.finditer(markdown))
    if not sources:
        return

    qta_by_codice: dict[str, str] = {}
    qta_in_order: list[str] = []
    for m in sources:
        qta_by_codice.setdefault(m.group("codice"), m.group("qta"))
        qta_in_order.append(m.group("qta"))

    # 1) match per codice esatto
    matched = 0
    for r in rows:
        if r.quantita is None and r.codice_letto in qta_by_codice:
            r.quantita = _decimale(qta_by_codice[r.codice_letto])
            matched += 1

    # 2) fallback per posizione (solo se non abbiamo matchato nulla per codice
    #    e il conteggio delle righe combacia perfettamente)
    if matched == 0:
        senza_qta = [r for r in rows if r.quantita is None]
        if senza_qta and len(senza_qta) == len(qta_in_order):
            log.info(
                "qta-patch: codici 2a passata diversi, mappo per posizione (%d righe)",
                len(senza_qta),
            )
            for r, q in zip(senza_qta, qta_in_order):
                r.quantita = _decimale(q)


def parse_articoli(markdown: str) -> list["RigaBolla"]:
    """Converte l'output Markdown in RigaBolla, una PAGINA (segmento) alla volta.

    Le pagine sono separate da \\f (inserito dall'engine): ogni segmento viene
    parsato in autonomia - tabelle, fallback freetext e patch quantita' restano
    locali alla pagina. Questo evita che, su bolle multi-pagina, la tabella
    fasulla di una pagina mandi in freetext anche le pagine con tabelle buone,
    o che le patch quantita' di una pagina vengano spalmate su righe di altre.

    Robusto rispetto a:
      - colonne in ordine diverso o sotto-insiemi (DDT senza prezzo/totale),
      - quantita scritte con unita' nella stessa cella ('18 NR' -> 18),
      - tabelle di colli/peso/firme dopo quella articoli (ignorate),
      - PIU' tabelle nella stessa pagina (es. testata fasulla + articoli veri),
      - assenza della riga separatrice `| --- |`,
      - output in testo libero (fallback regex), incluso il formato Camozzi
        con colonna 'Vs. CODICE' (codice interno gia' risolto).
    """
    out: list["RigaBolla"] = []
    for segment in markdown.split("\f"):
        rows = _parse_segment(segment)
        _apply_qta_patches(rows, segment)
        out.extend(rows)
    for i, r in enumerate(out, start=1):
        r.numero_riga = i
    return out


def _parse_segment(segment: str) -> list["RigaBolla"]:
    """Parsa una singola pagina: prima TUTTE le tabelle, poi fallback freetext."""
    from ..models import RigaBolla

    out: list[RigaBolla] = []
    visti: set[str] = set()
    for table in _extract_all_tables(segment):
        def cell(row: list[str], col: str, table: _Table = table) -> str:
            try:
                return row[table.columns.index(col)]
            except ValueError:
                return ""

        for row in table.rows:
            codice = cell(row, "codice").strip()
            if not _is_codice_articolo(codice):
                continue  # scarta righe di testata travestite da articoli
            if codice in visti:
                continue  # righe-metadati ripetono il codice della riga merce
            visti.add(codice)
            out.append(
                RigaBolla(
                    numero_riga=len(out) + 1,
                    codice_letto=codice,
                    descrizione=cell(row, "descrizione") or None,
                    quantita=_decimale(cell(row, "quantita")),
                    prezzo_unitario=_decimale(cell(row, "prezzo")),
                    totale_riga=_decimale(cell(row, "totale")),
                )
            )
    if out:
        return out
    return _parse_articoli_freetext(segment)


# Fallback per quando il modello scivola in testo libero senza tabella a pipe.
# Entrambi i pattern richiedono che il codice sia a INIZIO RIGA: cosi' non
# catturiamo per sbaglio cifre dentro frasi tipo "Vs. Ordine Nr. OC/26404399"
# o "Rf. Vs DDT 26450414 del 09/02/26" (codici di ordini/DDT, non articoli).
_SENTINELS_FINE_DESC = r"(?:Nr\.|Rf\.|Ordine\s|Vs\.)"
_RE_FREETEXT_RIGA = re.compile(
    r"^\s*(?P<codice>\d{6}[A-Z]{0,4}\.\d{4})\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<qta>\d+(?:[.,]\d+)?)\s*(?:NR|PZ|N|KG)?\b",
    re.IGNORECASE,
)
_RE_FREETEXT_RIGA_NO_QTA = re.compile(
    r"^\s*(?P<codice>\d{8})(?!\d)\s+"
    # (?!OP\b): un numero a 8 cifre seguito da 'OP' e' un ordine di produzione
    # (formato SOFT '26421479 OP U97003102 ...'), non un articolo.
    r"(?P<desc>(?!OP\b)[A-Z][^\n]*?)"
    r"(?=\s+" + _SENTINELS_FINE_DESC + r"|\s*$)",
    re.IGNORECASE,
)

# Formato Camozzi, riga singola: "<modello/descrizione> <Vs.CODICE 8 cifre>
# [annotazione] <UM> <quantita>[segno di spunta]". Il Vs. CODICE e' il codice
# interno del cliente GIA' RISOLTO: niente cross-reference per queste righe.
# Es: "1463 5/3-SM-S01/K01 RACORDI RAPIDI 97270158 PZ 200" oppure
#     "N08-F03/K01 FILTRO PER ACQUA 97290116 KANBAN CERT PZ 60".
_RE_CAMOZZI_INLINE = re.compile(
    r"^\s*(?P<desc>\S.{2,}?)\s+"
    r"(?<![A-Za-z0-9.])(?P<vscod>\d{8})(?!\d)\s+"
    # Annotazione opzionale fra codice e UM: 'KANBAN CERT', 'CON CERTIFI',
    # 'CERT.KTW/W2', ... (ammette cifre, punti, slash e trattini).
    r"(?:[A-Z][A-Z0-9 ./\-]{1,24}\s+)?"
    r"(?P<um>PZ|NR|KG|MT)\s+"
    r"(?P<qta>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

# Formato Camozzi multi-riga (celle in verticale):
#   <codice modello>            es. 40-1028-130007
#   <descrizione>               es. N08-F04/K01 FILTRO PER ARIA
#   Orig: IT Comb.nom: ...      (ignorata)
#   <Vs.CODICE>                 es. 97290115
#   [annotazione]               es. KANBAN CERT (0-2 righe)
#   <UM>                        es. PZ
#   <quantita>                  es. 20
_RE_CAMOZZI_BLOCK = re.compile(
    r"^(?P<vscod>\d{8})\s*$\n"
    r"(?:^[A-Z][A-Z .]{2,30}\s*$\n){0,2}"
    r"^(?P<um>PZ|NR|KG|MT)\s*$\n"
    r"^(?P<qta>\d+(?:[.,]\d+)?)",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_articoli_freetext(markdown: str) -> list["RigaBolla"]:
    from ..models import RigaBolla

    out: list[RigaBolla] = []
    visti: set[str] = set()

    def aggiungi(codice: str, desc: str | None, qta: str | None, interno: str | None = None) -> None:
        if codice in visti:
            return
        visti.add(codice)
        # Ripulisce i residui di righe pipe finite nel freetext ('| DESC |').
        desc_pulita = (desc or "").strip().strip("|").strip()
        out.append(
            RigaBolla(
                numero_riga=len(out) + 1,
                codice_letto=codice,
                descrizione=desc_pulita or None,
                quantita=_decimale(qta) if qta else None,
                codice_interno=interno,
            )
        )

    for line in markdown.splitlines():
        s = line.strip().strip("|").strip()
        if m := _RE_FREETEXT_RIGA.search(s):
            aggiungi(m.group("codice"), m.group("desc"), m.group("qta"))
            continue
        if (m := _RE_CAMOZZI_INLINE.search(s)) and "comb.nom" not in s.lower():
            # codice_interno = Vs. CODICE pre-risolto dalla bolla.
            # Le righe con 'Comb.nom' contengono il codice doganale (nomenclatura
            # combinata, es. 74122000): NON e' un Vs. CODICE.
            aggiungi(m.group("vscod"), m.group("desc"), m.group("qta"), interno=m.group("vscod"))
            continue
        if m := _RE_FREETEXT_RIGA_NO_QTA.search(s):
            aggiungi(m.group("codice"), m.group("desc"), None)

    # Formato Camozzi multi-riga: scandiamo i blocchi sull'intero segmento.
    for m in _RE_CAMOZZI_BLOCK.finditer(markdown):
        desc = _descrizione_prima_del_blocco(markdown, m.start())
        aggiungi(m.group("vscod"), desc, m.group("qta"), interno=m.group("vscod"))

    return out


def _descrizione_prima_del_blocco(markdown: str, pos: int) -> str | None:
    """Risale dalle righe sopra un blocco Camozzi alla descrizione articolo,
    saltando le righe 'Orig: ...' e le righe vuote."""
    for prev in reversed(markdown[:pos].rstrip().splitlines()[-3:]):
        p = prev.strip()
        if not p or p.lower().startswith("orig:"):
            continue
        return p
    return None


def _markdown_to_rows(markdown: str) -> list[TableRow]:
    """Fallback generico: rappresentazione "celle grezze" per il parser legacy.

    Usato dagli adapter OCR diversi da dots/llama (PaddleOCR-VL/GLM-OCR), che
    passano per src/bolle/parsing.py. Per dots/llama si usa parse_articoli().
    """
    table = _extract_articoli_table(markdown)
    if table is not None:
        return [
            TableRow(cells=[TableCell(text=c, confidence=1.0) for c in row])
            for row in table.rows
        ]
    # Nessuna tabella Markdown: ripiega separando le colonne sugli spazi multipli.
    rows: list[TableRow] = []
    for line in markdown.splitlines():
        s = line.strip()
        if not s:
            continue
        cells = re.split(r"\s{2,}|\t", s)
        if len(cells) >= 2:
            rows.append(TableRow(cells=[TableCell(text=c.strip(), confidence=1.0) for c in cells]))
    return rows
