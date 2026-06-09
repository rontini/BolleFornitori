# Diario delle decisioni — Bolle Fornitori

> Stato del progetto, scelte fatte e problemi aperti. Da aggiornare a ogni
> milestone, cosi' chiunque riparta (anche in una nuova chat) ha il contesto
> completo senza dover ricostruire la storia.

Ultimo aggiornamento: validazione OCR su scansioni reali (CPU senza AVX).

## 1. Obiettivo del progetto
Acquisire automaticamente le bolle dei fornitori e riconciliarle con gli
ordini di acquisto **prima** dell'arrivo fisico della merce, cosi' da
anticipare ammanchi, eccedenze e ridatazioni. Tutto in locale (on-premise),
zero dati verso l'esterno.

Riferimento: documento di analisi `Analisi_bolle_fornitori.docx`.

## 2. Architettura in una riga
Strato percettivo (OCR/LLM) → strato deterministico (parsing, validazione,
riconciliazione). **L'AI vive solo nello strato percettivo**, tutto il resto
e' deterministico e verificabile.

Moduli sotto `src/bolle/`:
- `router.py`: classifica PDF nativo vs scansione vs XML
- `ocr/pdf_text.py`: estrazione PDF nativi (PyMuPDF, niente OCR)
- `ocr/paddleocr_vl.py`: adapter PaddleOCR-VL (richiede AVX, ad oggi inutilizzato)
- `ocr/glm_ocr.py`: adapter GLM-OCR via paddlex (richiede AVX)
- `ocr/dots_ocr.py`: adapter llama.cpp (funziona su CPU senza AVX, oggi usato
  con il modello `ggml-org/GLM-OCR-GGUF`)
- `fatturapa.py`: parser XML FatturaPA
- `header_extractor.py`: estrazione testata (Ollama + fallback regex)
- `parsing.py`: parsing righe generico (per gli OCR diversi da dots/llama)
- `validation.py`: risoluzione codice + verifica aritmetica
- `api_client.py`: client API aziendali (`http` reale o `memory` offline)
- `reconciliation.py`: motore di riconciliazione deterministica
- `review_queue.py`: persistenza coda di revisione (JSON)
- `pipeline.py`: orchestratore per singolo documento
- `cli.py`: entry point (`python -m bolle.cli`)

## 3. Configurazione macchina utente
- **Windows Server 2016** su VMware (VM con Xeon Gold 6134, 160 GB RAM, no GPU).
- **AVX mascherato dall'hypervisor**: PaddlePaddle non parte (`libpaddle.pyd`
  fallisce). Per sbloccare PaddleOCR-VL servirebbe abilitare EVC=Skylake o
  superiore sulla VM (richiede admin VMware).
- Workaround attuale: **llama.cpp + GGUF**, che gira su CPU senza AVX.
- Modello: **GLM-OCR-GGUF** (`ggml-org/GLM-OCR-GGUF`), servito da `llama-server`
  su `localhost:8080`, comando: `llama-server.exe -hf ggml-org/GLM-OCR-GGUF
  --flash-attn off -c 24000 --port 8080`.
- Settings: `engine: dots_ocr`, `dots_server_url: http://localhost:8080`,
  `dots_max_tokens: 6000`, `request_timeout_s: 3600`. `api.backend: memory`
  (nessuna API Oracle ancora collegata).

## 4. Decisioni chiave gia' prese
1. **Pipeline a moduli, additiva**: ogni nuovo motore OCR si aggiunge dietro
   un'interfaccia `OcrEngine` senza toccare il resto.
2. **API selezionabile** (`backend: http | memory`): `memory` permette di
   validare l'OCR senza l'Oracle aziendale.
3. **Streaming SSE verso llama-server**: niente timeout di lettura su CPU
   lente; l'avanzamento `pagina N [fase] · token letti` aiuta a vedere la run.
4. **Due chiamate OCR per pagina** (`testata` + `tabella`): output instabile
   con un solo prompt; il vision-encode si paga due volte ma le risposte sono
   piu' affidabili.
5. **Parser articoli con due livelli**:
   (a) tabella Markdown con mapping per nome di colonna,
   (b) fallback regex su testo libero (`\\d{6}\\.\\d{4}` o `\\d{8}` a inizio riga).
6. **Filtro "codice articolo plausibile"**: scarta righe la cui prima cella
   contiene lettere/trattini (es. `26DGT-01995`): erano pezzi di testata
   travestiti da articoli da tabelle Markdown fasulle.
7. **Distinzione ordine cliente vs fornitore**: `numero_ordine` = "Vs. Ordine"
   (cliente, usato per il match Oracle); `numero_ordine_fornitore` = "Ordine"
   bare (rif. interno del fornitore).
8. **Sviluppo via ZIP**: l'utente non ha git installato; ogni sync = scaricare
   ZIP da GitHub e fare `Copy-Item` sopra la cartella di lavoro. Il branch di
   lavoro e' `claude/tender-meitner-DV4PC`. Backup di sicurezza su
   `claude/checkpoint-deterministico`.

## 5. Stato di funzionamento

### Va bene
- Test suite verde (28+ test).
- PDF nativi: lettura riga per riga e validazione aritmetica funzionano.
- FatturaPA: parser dedicato funzionante.
- Su una **bolla singola/pagina singola con tabella pulita** GLM-OCR legge
  codici e quantita correttamente (es. pagina 1 della scansione di prova:
  4/4 codici corretti).
- Distinzione dei due ordini (cliente/fornitore) funziona.

### Va male / aperto
- **Hallucinazione** del modello su PDF lunghi: tabelle con righe ripetute
  centinaia di volte (es. `U97003123 SED SEG ST 123 VERDE POLINESIA` x300).
- **Quantita** non sempre trascritte dal modello; in alcuni casi sono finite
  troncate nel parser (es. `99937761` → `999377`) perche' il parser ha
  scambiato il codice commerciale per quantita.
- **PDF multi-bolla**: il file di prova contiene **almeno 5 documenti diversi**
  (DDT Verniciatura Bolognese 26DGT-01995, 26DT-01997, 26DT-01961; documento
  Centro Distributivo Palazzolo; DDT Camozzi). La pipeline oggi tratta tutto
  come una sola bolla.
- **Formato Camozzi (Vs. CODICE)**: c'e' una colonna con il codice interno del
  cliente gia' pre-risolto. Non e' ancora gestita dal parser. Importante perche'
  evita la cross-reference per quelle righe.
- **AVX assente** sulla VM: PaddleOCR-VL e GLM-OCR via paddlex non possono
  essere usati. La soluzione "tecnicamente migliore" dell'analisi e' bloccata
  fino a quando un admin VMware non abilita EVC Skylake+ (testo richiesta
  pronto, vedi cronologia chat).

## 6. Prossimi passi (in ordine consigliato)
1. **PDF splitter**: separare il PDF multi-bolla in singole bolle prima
   dell'OCR. Marker: `Pagina 1/N`, intestazione "DOCUMENTO DI TRASPORTO",
   cambio fornitore. Riduce hallucinazione + abilita la riconciliazione per
   bolla.
2. **Parser formato Camozzi** (Vs. CODICE): mapping colonne nuovo, e quando
   c'e' "Vs. CODICE" il `codice_interno` e' gia' risolto -> salta la
   cross-reference.
3. **Fix quantita "999377"**: il table-parser deve avere precedenza sul
   freetext quando trova una tabella valida; oggi il freetext interviene
   troppo spesso e sbaglia colonna.
4. **`repetition_penalty`** su llama-server per ridurre i loop.
5. **Contratto API Oracle**: endpoint, payload, autenticazione (oggi
   `backend: memory`).
6. **Abilitazione AVX sulla VM** (in parallelo, via richiesta a chi gestisce
   VMware): sblocca PaddleOCR-VL/GLM-OCR e accelera l'inferenza.

## 7. Come ripartire da zero (anche da una chat nuova)
```bash
# 1. Clona il repo (o scarica lo ZIP del branch claude/tender-meitner-DV4PC)
git clone https://github.com/rontini/BolleFornitori.git
cd BolleFornitori
git checkout claude/tender-meitner-DV4PC

# 2. Ambiente Python (Windows: usa 'py -m venv .venv' e .venv\Scripts\activate.bat)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[pdf,dev]"
pytest                       # tutti i test devono passare

# 3. Per testare l'OCR su scansioni servono:
#    a. llama-server in esecuzione su localhost:8080 con GLM-OCR-GGUF
#    b. config/settings.yaml con engine=dots_ocr, api.backend=memory
#    c. una scansione di prova in samples/

# 4. Lancia su una sola pagina (per tarare):
python -m bolle.cli --config config/settings.yaml --pages 1 path/to/scan.pdf

# 5. Ispeziona work/ocr_raw/<id>.md (output grezzo OCR) e
#    work/revisione/<id>.json (righe estratte).
```

## 8. File chiave da leggere per il contesto
- `README.md` — architettura, install, prossimi passi.
- `docs/INSTALL.md` — guida operativa Windows (PaddleOCR + alternativa
  llama.cpp).
- `tests/test_dots_ocr.py` — esempi di Markdown reali con cui il parser deve
  funzionare (utile per capire i casi gia' visti).
- `src/bolle/ocr/dots_ocr.py` — prompt OCR e parser. E' il file piu' rumoroso
  perche' inseguiamo le bizze del modello su CPU.
