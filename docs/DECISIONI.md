# Diario delle decisioni — Bolle Fornitori

> Stato del progetto, scelte fatte e problemi aperti. Da aggiornare a ogni
> milestone, cosi' chiunque riparta (anche in una nuova chat) ha il contesto
> completo senza dover ricostruire la storia.

Ultimo aggiornamento: estrazione codice commerciale, riconoscimento fornitore,
modalita' di sviluppo `--reuse-ocr`. Branch `claude/paddleocr-avx`, 86 test verdi.

## 0. Branch
- `claude/paddleocr-avx` — **branch attivo**. Macchina con AVX, motore
  PaddleOCR-VL in-process. Stesso parser/splitter di dots_ocr, output Markdown
  con tabelle pipe (conversione automatica HTML→pipe).
- `claude/tender-meitner-DV4PC` — fallback per CPU/VM senza AVX (motore
  `dots_ocr` via llama.cpp + GLM-OCR-GGUF). Congelato, funzionante.
- `claude/checkpoint-deterministico` — punto di ripristino storico.

## 1. Obiettivo del progetto
Acquisire automaticamente le bolle dei fornitori e riconciliarle con gli
ordini di acquisto **prima** dell'arrivo fisico della merce, cosi' da
anticipare ammanchi, eccedenze e ridatazioni. Tutto in locale (on-premise),
zero dati verso l'esterno.

Riferimento: documento di analisi `Analisi_bolle_fornitori.docx`.

## 2. Architettura in una riga
Strato percettivo (OCR) → strato deterministico (parsing, validazione,
riconciliazione). **L'AI vive solo nello strato percettivo**, tutto il resto
e' deterministico e verificabile.

Moduli sotto `src/bolle/`:
- `router.py` — classifica PDF nativo vs scansione vs XML
- `ocr/pdf_text.py` — estrazione PDF nativi (PyMuPDF, niente OCR)
- `ocr/paddleocr_vl.py` — **motore primario** (branch AVX); converte le
  tabelle HTML di Paddle in pipe-Markdown
- `ocr/dots_ocr.py` — adapter llama.cpp (branch no-AVX); contiene anche il
  parser Markdown usato da entrambi gli adapter
- `ocr/glm_ocr.py` — adapter alternativo via paddlex
- `splitter.py` — divide i PDF multi-bolla; saltato con `--no-split`
- `fatturapa.py` — parser XML FatturaPA
- `header_extractor.py` — testata via regex + pattern fornitori_noti
- `parsing.py` — parser righe generico (motori non-Markdown)
- `validation.py` — risoluzione codice + verifica aritmetica
- `api_client.py` — client API aziendali (`http` reale o `memory` offline)
- `reconciliation.py` — motore di riconciliazione deterministico
- `review_queue.py` — coda di revisione (persistenza JSON)
- `pipeline.py` — orchestratore per singolo documento
- `cli.py` — entry point (`python -m bolle.cli`)

## 3. Configurazione macchina attuale (con AVX)
- **Windows** con CPU che espone AVX (la VM senza AVX e' il piano B sul
  branch `tender-meitner-DV4PC`).
- Python 3.11+, PaddlePaddle + PaddleOCR installati: il modello PaddleOCR-VL
  parte in-process (niente llama-server).
- `engine: paddleocr_vl` in `settings.yaml`; `api.backend: memory` finche'
  non ci sono le API Oracle.

## 4. Decisioni chiave
1. **Pipeline a moduli, additiva**: ogni nuovo motore OCR si aggiunge dietro
   un'interfaccia `OcrEngine` senza toccare il resto.
2. **API selezionabile** (`backend: http | memory`): `memory` permette di
   validare l'OCR senza l'Oracle aziendale.
3. **Conversione HTML→pipe** all'uscita da PaddleOCR-VL: tutto il parser
   esistente (per nome colonna) funziona identico, indipendente dal motore.
4. **Parser articoli con piu' livelli**:
   - tabella Markdown con mapping per nome di colonna (preferito);
   - fallback regex su testo libero (`dddddd.dddd` / `dddddddd` a inizio riga);
   - per il formato Camozzi: codice in cella + commerciale in descrizione +
     pattern multi-riga.
5. **Filtro "codice articolo plausibile"**: accetta SOLO `dddddd.dddd`
   (anche con suffisso `RIP`, `-F`, `_F`) o `dddddddd` puri. Esclude P.IVA
   (11 cifre), numeri DDT, ordini.
6. **Distinzione ordine cliente vs fornitore**: `testata.numero_ordine` =
   "Vs. Ordine" (cliente, chiave per match Oracle); `numero_ordine_fornitore`
   = "Ordine" / "Ns. Ordine" (riferimento interno fornitore).
7. **DECISO — Chiave di match = codice commerciale `99xxxxxx`**: e' il
   NOSTRO codice interno, spesso stampato in descrizione accanto al nome
   articolo. Estratto in `RigaBolla.codice_commerciale`; la validazione lo
   valida in anagrafica e lo usa come `codice_interno` (salta cross-reference).
   Il codice fornitore (088xxx) resta in `codice_letto` come riferimento.
8. **Raffinamento descrizione**: dopo aver estratto il commerciale, la
   descrizione viene ripulita al solo nome articolo (toglie metadati come
   "Nr. commessa", "Rif. Vs DDT", "Ordine ...").
9. **Riconoscimento fornitore in cascata** (nessun input richiesto nei casi
   normali):
   1. `--fornitore "NOME"` (override CLI esplicito);
   2. `fornitori_noti` configurati in `settings.yaml` (nome canonico esatto);
   3. **regex generica** sul `.md`: cerca "NOME + s.r.l./S.p.A./srl/spa/...",
      con lista di scarti per non beccare il cliente (CEFLA) o gli
      spedizionieri ricorrenti;
   4. **sidecar dello splitter** (`<stem>.meta.json` accanto al PDF
      splittato): lo splitter, mentre legge l'header di ogni pagina per
      dividere, salva anche la ragione sociale rilevata; la pipeline la usa
      automaticamente. Risolve il caso in cui il `.md` del documento intero
      non contiene la ragione sociale (es. bolla01 Verniciatura, dove Paddle
      legge solo il destinatario CEFLA).
10. **Modalita' di sviluppo `--reuse-ocr`**: ricarica il Markdown gia' salvato
    in `work/ocr_raw/<stem>.md` e parte dal parser. Iterazione in **secondi**
    invece di minuti, ideale per tarare parser/validazione.

## 5. Stato di funzionamento (branch AVX)

### Funziona bene
- 86 test verdi.
- PDF nativi (router → PyMuPDF) e XML FatturaPA: completi.
- **PaddleOCR-VL su una bolla reale di 5 pagine: 17 righe su 18 estratte
  correttamente** con codice fornitore + codice commerciale + descrizione
  pulita + quantita'. Solo 1 riga "blob" non recuperabile (l'OCR non ha
  trascritto il commerciale per quella riga).
- Splitter multi-bolla con rilevamento via marker `Pagina N/M` + impronta
  fornitore. Si salta con `--no-split`.
- Formato Camozzi (`Vs. CODICE` pre-risolto) gestito.
- Riconoscimento fornitore via `fornitori_noti` (regex sul `.md`) e override
  CLI.
- Recupero quantita' anche quando finiscono nella colonna sbagliata
  (es. `✓ 1 NR` nella cella U.d.M.).

### Limiti aperti
- **Formati SOFT e Zinc-Crom**: parser dedicato non ancora scritto (0 o
  poche righe estratte). Da implementare solo se questi fornitori contano
  nei volumi di produzione.
- **Riga "blob" residua**: quando l'OCR non trascrive il commerciale, la
  riga finisce in revisione manuale. E' il comportamento corretto: il
  sistema non inventa.

## 6. Modalita' di lavoro

| Flag | A cosa serve |
|---|---|
| (nessuno) | Splitter + OCR + parser + validazione (caso produzione "tutto") |
| `--no-split` | PDF gia' singolo, salta lo splitter (~30 s/pagina in meno) |
| `--pages 1` / `1-3` / `1,5,7` | Elabora solo le pagine indicate |
| `--reuse-ocr` | **Dev**: ricarica `.md` esistente, parte dal parser (secondi) |
| `--fornitore "NOME"` | Forza il fornitore in testata (override) |

Esempio dev su una bolla gia' OCRata:
```
python -m bolle.cli --config config\settings.yaml --reuse-ocr work\split\bolla.pdf
```

## 7. Prossimi passi (in ordine consigliato)
1. **Contratto API Oracle**: endpoint, payload, autenticazione (oggi
   `backend: memory`). E' il vero sblocco: senza, le righe restano in
   revisione anche se i dati sono giusti. Le API dovranno esporre l'anagrafica
   (per validare i codici commerciali) e gli ordini (per la riconciliazione).
2. **Validazione sul flusso reale di produzione**: bolle singole di
   fornitori diversi, una alla volta. E' il caso "facile" per il modello
   (molto piu' stabile delle 17 pagine multi-bolla del test).
3. **Formati SOFT / Zinc-Crom**, solo se rilevanti nei volumi.

## 8. Come ripartire da zero (anche da una chat nuova)
```bash
# 1. Clona il repo, branch AVX
git clone https://github.com/rontini/BolleFornitori.git
cd BolleFornitori
git checkout claude/paddleocr-avx

# 2. Ambiente Python (Windows: py -m venv .venv && .venv\Scripts\activate.bat)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[pdf,dev]"
pytest                       # atteso: 86 passed

# 3. Runtime OCR (solo per le scansioni)
pip install paddlepaddle paddleocr
python -c "import paddle; print('paddle', paddle.__version__)"

# 4. Config
cp config/settings.example.yaml config/settings.yaml
# verifica: engine: paddleocr_vl, api.backend: memory, fornitori_noti presente

# 5. Prima prova su una bolla
python -m bolle.cli --config config/settings.yaml --no-split path/to/bolla.pdf

# Sviluppo iterativo (dopo il primo OCR): rilancia istantaneamente il parser
python -m bolle.cli --config config/settings.yaml --reuse-ocr path/to/bolla.pdf
```

## 9. File chiave da leggere per il contesto
- `README.md` — architettura, install, comandi base.
- `docs/INSTALL.md` — guida operativa Windows (PaddleOCR-VL + alternativa
  llama.cpp).
- `config/settings.example.yaml` — riferimento per tutte le opzioni
  (engine, fornitori_noti, api, ecc.).
- `src/bolle/ocr/paddleocr_vl.py` — adapter Paddle + conversione HTML→pipe.
- `src/bolle/ocr/dots_ocr.py` — parser Markdown (usato da entrambi gli
  adapter); contiene la logica di estrazione codice commerciale, dedup,
  formato Camozzi.
- `src/bolle/header_extractor.py` — testata + riconoscimento fornitore.
- `src/bolle/validation.py` — risoluzione codice (anagrafica → cross-ref).
- `tests/` — ogni decisione importante ha un test sui Markdown reali; per
  capire i casi gia' visti, leggere i test e' la via piu' veloce.
