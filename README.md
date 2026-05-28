# Bolle Fornitori — riconoscimento automatico e riconciliazione con gli ordini

Pipeline **locale (on-premise, CPU)** che acquisisce le bolle dei fornitori,
ne estrae le righe e le riconcilia con gli ordini di acquisto, così da
anticipare a tavolino ammanchi, eccedenze e ri-datazioni **prima** dell'arrivo
fisico della merce.

Principio architetturale di fondo: **l'AI vive solo nello strato percettivo**
(carta/PDF → righe strutturate). Tutto ciò che riguarda matching e decisioni
(riconciliazione) è **logica deterministica e verificabile**, non AI.

> Stato: **scheletro a moduli** (vedi "Prossimi passi" dell'analisi). I percorsi
> deterministici (parsing, validazione, riconciliazione) sono completi e testati;
> i motori pesanti (OCR PaddleOCR-VL, LLM via Ollama) sono integrati dietro
> interfacce con import lazy e vanno installati/validati sui documenti reali.

## Architettura

```
sorgenti (email/PEC/portale)
        │
        ▼
   router documenti ── PDF nativo ─► estrazione testo (PyMuPDF)  ─┐
        │                                                         │
        └──────────── scansione ─► OCR tabella (PaddleOCR-VL) ────┤
                                                                  ▼
                          testata (LLM piccolo, semantica)   parsing righe
                                                                  │  (deterministico)
                                                                  ▼
                               validazione + risoluzione codici (cross-ref/anagrafica + aritmetica)
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
| `ocr/pdf_text.py` | Estrazione diretta da PDF con layer di testo (niente OCR) |
| `ocr/paddleocr_vl.py` | Adapter motore OCR primario (modalità tabella) |
| `header_extractor.py` | Testata via LLM piccolo (Ollama) + fallback regex |
| `parsing.py` | Parsing **deterministico** delle righe dalla tabella OCR |
| `validation.py` | Risoluzione codice (cross-ref/anagrafica) + verifica aritmetica |
| `api_client.py` | Client API REST aziendali (+ backend in-memory per i test) |
| `reconciliation.py` | Motore di riconciliazione **deterministico** |
| `review_queue.py` | Coda di revisione (persistenza JSON) |
| `pipeline.py` | Orchestratore per singola bolla |
| `cli.py` | Entry point a riga di comando |

## Cosa installare sulla macchina

Hardware di riferimento (già disponibile): Xeon Gold 6134 (AVX-512), 160 GB RAM,
nessuna GPU. Tutto gira in locale; nessun dato esce dall'infrastruttura.

### 1. Base — Python e pipeline (leggero)

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv git
git clone <questo-repo> BolleFornitori && cd BolleFornitori
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[pdf,dev]"        # core + PDF nativi + test
```

A questo punto la pipeline gira già sui **PDF nativi** (percorso senza OCR) e
tutti i test passano:

```bash
pytest
cp config/settings.example.yaml config/settings.yaml
bolle --config config/settings.yaml inbox/esempio.pdf
```

### 2. OCR per le scansioni — PaddleOCR-VL (pesante)

Serve solo per i PDF scansione/immagini. Installazione separata perché porta
PaddlePaddle:

```bash
pip install -e ".[ocr]"            # paddlepaddle + paddleocr + pillow
# build CPU di paddlepaddle ottimizzata per AVX-512; scaricare il modello
# PaddleOCR-VL al primo avvio o manualmente (vedi docs/INSTALL.md)
```

In fase di validazione (prossimi passi dell'analisi) confrontare **PaddleOCR-VL**
e **GLM-OCR** su 20-30 bolle reali misurando l'accuratezza su **codice** e
**quantità**: vince il modello sul dato reale.

### 3. LLM piccolo per testata e casi sporchi — Ollama

Usato **solo** per i campi di testata e il ~10% di casi sporchi (mai per le 100
righe). Da tenere marginale: il limite della macchina è il numero di core.

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b-instruct-q4_K_M
# l'endpoint locale http://localhost:11434 è già configurato in settings.yaml
```

Senza Ollama la pipeline non si blocca: usa un fallback a regex per la testata.

### 4. API aziendali (verso Oracle)

La pipeline **non** parla mai con Oracle direttamente: solo via API REST
aziendali. Va definito il contratto (endpoint, payload JSON, autenticazione) —
vedi `src/bolle/api_client.py` per le firme attese. In sviluppo si usa
`dry_run: true` (non scrive) e/o `InMemoryAziendaApi` nei test.

Configurare il token via variabile d'ambiente (mai nel repo):

```bash
export BOLLE_API_TOKEN=...        # oppure BOLLE_API_BASE_URL per l'endpoint
```

## Test

```bash
pytest            # logica deterministica: parsing, validazione, riconciliazione
```

## Licenze

Codice: Apache-2.0. PaddleOCR-VL è Apache-2.0 (uso commerciale libero). La
licenza di **ogni** modello scaricato va verificata prima del rilascio in
produzione: "gratis" non equivale sempre a "uso commerciale libero".

## Prossimi passi (dall'analisi)

1. Validazione modelli OCR su 20-30 bolle reali (codice + quantità).
2. Definizione del contratto delle API Oracle.
3. ✅ Scheletro pipeline a moduli (questo repo).
4. Test su un sottoinsieme di fornitori; taratura soglie e coda di revisione.
5. Roll-out progressivo.
