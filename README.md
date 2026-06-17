# Bolle Fornitori — riconoscimento automatico e riconciliazione con gli ordini

Pipeline **locale (on-premise, CPU)** che acquisisce le bolle dei fornitori,
ne estrae le righe e le riconcilia con gli ordini di acquisto, così da
anticipare a tavolino ammanchi, eccedenze e ri-datazioni **prima** dell'arrivo
fisico della merce.

Principio architetturale di fondo: **l'AI vive solo nello strato percettivo**
(carta/PDF → righe strutturate). Tutto ciò che riguarda matching e decisioni
(riconciliazione) è **logica deterministica e verificabile**, non AI.

> Stato attuale: pipeline end-to-end funzionante con PaddleOCR-VL su CPU AVX.
> Su una bolla reale di test (5 pagine, 18 articoli): **17 righe estratte
> correttamente** con codice fornitore, codice commerciale, descrizione pulita
> e quantità. **86 test verdi.** Manca il collegamento alle API Oracle reali
> (oggi backend `memory`): è il prossimo passo per chiudere la riconciliazione.

## Branch
- **`claude/paddleocr-avx`** — branch attivo. Macchina con AVX, motore
  PaddleOCR-VL in-process. È quello che si usa in sviluppo.
- `claude/tender-meitner-DV4PC` — fallback per CPU/VM senza AVX (motore
  `dots_ocr` via llama.cpp + GLM-OCR-GGUF). Congelato, funzionante.

## Architettura

```
sorgenti (email/PEC/portale)
        │
        ▼
   router documenti ── PDF nativo ─► estrazione testo (PyMuPDF) ─┐
        │                                                        │
        └──── scansione ─► splitter multi-bolla ─► OCR ──────────┤
                                                                 ▼
                              estrazione testata + parsing righe (deterministico)
                                                                 │
                                                                 ▼
                       validazione + risoluzione codici (anagrafica/cross-ref + aritmetica)
                                                                 │
                                                                 ▼
                                                API REST aziendali ──► Oracle
                                                                 │
                                                                 ▼
                          riconciliazione deterministica (ammanchi/eccedenze/ridatazioni)
                                                                 │
                                                                 ▼
                                            proposte  +  coda di revisione
```

### Moduli (`src/bolle/`)

| Modulo | Ruolo |
|---|---|
| `router.py` | Classifica il documento: PDF nativo vs scansione vs XML |
| `splitter.py` | Divide PDF multi-bolla (rileva marker `Pagina N/M` + impronta fornitore). Saltato con `--no-split` |
| `ocr/pdf_text.py` | Estrazione PDF con layer di testo (niente OCR) |
| `ocr/paddleocr_vl.py` | **Adapter primario**: PaddleOCR-VL in-process; tabelle HTML→pipe |
| `ocr/dots_ocr.py` | Adapter llama.cpp (no-AVX) + parser Markdown condiviso |
| `ocr/glm_ocr.py` | Adapter alternativo |
| `fatturapa.py` | Parser XML FatturaPA |
| `header_extractor.py` | Testata: ordini, bolla, data, fornitore (via `fornitori_noti`) |
| `parsing.py` | Parser righe generico (motori non-Markdown) |
| `validation.py` | Risoluzione codice (commerciale → anagrafica, fallback cross-ref) + verifica aritmetica |
| `api_client.py` | Client API REST aziendali; backend `memory` per dev/test offline |
| `reconciliation.py` | Motore di riconciliazione **deterministico** |
| `review_queue.py` | Coda di revisione (persistenza JSON) |
| `pipeline.py` | Orchestratore per singolo documento |
| `cli.py` | Entry point (`python -m bolle.cli`) |

## Cosa installare sulla macchina

Hardware di riferimento: Xeon con AVX, 160 GB RAM, nessuna GPU. Tutto gira
in locale; nessun dato esce dall'infrastruttura.

### 1. Base — Python e pipeline

```cmd
git clone https://github.com/rontini/BolleFornitori.git
cd BolleFornitori
git checkout claude/paddleocr-avx
py -m venv .venv
.venv\Scripts\activate.bat
pip install -e ".[pdf,dev]"
pytest                                  :: atteso: 86 passed
```

### 2. OCR per le scansioni — PaddleOCR-VL

```cmd
pip install paddlepaddle paddleocr
:: il modello PaddleOCR-VL si scarica al primo utilizzo (~1 GB)
python -c "import paddle; print('paddle', paddle.__version__)"
```

> Senza AVX, PaddlePaddle non parte: usare il branch `tender-meitner-DV4PC`
> con `dots_ocr` (llama.cpp + GLM-OCR-GGUF). Vedi `docs/INSTALL.md`.

### 3. Configurazione

```cmd
copy config\settings.example.yaml config\settings.yaml
notepad config\settings.yaml
```

Le impostazioni chiave (già nei valori di default dell'esempio):
- `ocr.engine: paddleocr_vl`
- `api.backend: memory` (finché non ci sono le API Oracle)
- `ocr.fornitori_noti` — mappa di pattern → nome canonico del fornitore.

### 4. API aziendali (verso Oracle) — pendente

La pipeline **non** parla mai con Oracle direttamente: solo via API REST
aziendali. Va definito il contratto (endpoint, payload JSON, autenticazione)
— vedi `src/bolle/api_client.py` per le firme attese. In dev si usa
`backend: memory` (in-memory) che lascia tutte le righe in revisione.

## Uso

### Comando base
```cmd
python -m bolle.cli --config config\settings.yaml path\bolla.pdf
```

### Flag utili

| Flag | A cosa serve |
|---|---|
| `--no-split` | PDF già singolo, salta lo splitter (~30 s/pagina in meno) |
| `--pages 1` / `1-3` / `1,5,7` | Elabora solo le pagine indicate |
| `--reuse-ocr` | **Dev**: ricarica `work/ocr_raw/<stem>.md` esistente, parte dal parser (secondi invece di minuti) |
| `--fornitore "NOME"` | Forza il fornitore in testata (override esplicito) |

Esempio sviluppo (dopo aver fatto OCR una volta):
```cmd
python -m bolle.cli --config config\settings.yaml --reuse-ocr work\split\bolla01.pdf
```

### Output
La pipeline scrive tre file per ogni documento:
- `work\ocr_raw\<stem>.md` — Markdown grezzo dell'OCR (ispezionabile).
- `work\output\<stem>.json` — esito completo: testata + tutte le righe
  (risolte e non risolte). È quello che ti serve di solito.
- `work\revisione\<stem>.json` — solo le righe non risolte (per la coda di
  revisione manuale).

## Test
```cmd
pytest
```

## Licenze
Codice: Apache-2.0. PaddleOCR-VL è Apache-2.0 (uso commerciale libero). La
licenza di **ogni** modello scaricato va verificata prima del rilascio in
produzione: "gratis" non equivale sempre a "uso commerciale libero".

## Stato e prossimi passi

### ✅ Fatto
- Scheletro pipeline a moduli, additivo.
- PaddleOCR-VL come motore primario (branch AVX); `dots_ocr` come fallback.
- Splitter PDF multi-bolla (marker pagina + impronta fornitore).
- Parser Markdown robusto: tabelle, freetext, formato Camozzi (Vs.CODICE),
  estrazione codice commerciale, dedup, anti-falsi-positivi.
- Riconoscimento fornitore via pattern noti configurabili.
- Modalità sviluppo `--reuse-ocr` per iterare in secondi.
- 86 test verdi (compresi test su Markdown reali di tutti i casi visti).

### 🔜 Prossimi
1. **Definire il contratto API Oracle** (endpoint + payload) — sblocca la
   riconciliazione vera.
2. Validazione su flusso reale: bolle singole di fornitori diversi.
3. Formati SOFT / Zinc-Crom, se rilevanti nei volumi.

Per dettagli sullo stato, decisioni prese e come ripartire da zero in una
nuova chat, vedi **`docs/DECISIONI.md`**.
