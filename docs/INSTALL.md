# Macchina con AVX — PaddleOCR-VL (motore primario, branch claude/paddleocr-avx)

Sulla macchina con AVX abilitato si usa PaddleOCR-VL in-process: **niente
llama-server**, niente download GGUF. Stessa pipeline, stesso parser.

```cmd
:: 1. ambiente (come al solito)
py -m venv .venv
.venv\Scripts\activate.bat
pip install -e ".[pdf,dev]"
pytest                                  :: tutti verdi

:: 2. runtime PaddleOCR-VL (pesante, una tantum; il modello si scarica al 1o uso)
pip install paddlepaddle paddleocr

:: 3. verifica che la CPU sia vista con AVX
python -c "import paddle; print('paddle', paddle.__version__)"

:: 4. config: copia l'esempio (engine e' gia' paddleocr_vl) e attiva l'offline
copy config\settings.example.yaml config\settings.yaml
:: in settings.yaml: api.backend: memory  (finche' non ci sono le API Oracle)

:: 5. lancio (identico a prima)
python -m bolle.cli --config config\settings.yaml --pages 1 "C:\percorso\bolla.pdf"
```

Lo splitter multi-bolla funziona anche qui (legge gli header con PaddleOCR-VL
in-process). L'output resta in `work\ocr_raw` / `work\output` / `work\revisione`.

> Nota: l'output Markdown di PaddleOCR-VL puo' differire leggermente da quello
> di GLM-OCR/llama. La mappatura e' isolata in `_markdown_da_risultato()` in
> `src/bolle/ocr/paddleocr_vl.py`: se il primo run su una bolla reale mostra
> uno schema diverso, si adatta SOLO quella funzione.

---

# Step 1 — Validazione OCR sulle bolle reali

Obiettivo (analisi, sez. 6.1 e 13): confrontare **PaddleOCR-VL** e **GLM-OCR** su
20-30 bolle reali — incluse alcune **degradate** ed **estere** — misurando solo
**quante volte leggono correttamente codice e quantità**. Vince il modello sul dato
reale, non sui benchmark di layout.

Tutto gira in locale sulla macchina di produzione (Xeon Gold 6134, AVX-512, 160 GB
RAM, no GPU). Niente esce dall'infrastruttura.

## Cosa devi fare

### 1. Ambiente Python + pacchetto base

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv git
git clone https://github.com/rontini/BolleFornitori.git
cd BolleFornitori
git checkout claude/tender-meitner-DV4PC
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[pdf]"        # core + PyMuPDF (PDF nativi, niente OCR)
```

### 2. Installa i motori OCR da confrontare

PaddleOCR-VL (motore primario, Apache 2.0):

```bash
pip install -e ".[ocr]"        # paddlepaddle (build CPU) + paddleocr + pillow
# Il modello PaddleOCR-VL viene scaricato al primo utilizzo.
# Se la macchina non ha accesso a internet, scaricalo su una macchina connessa
# e copia la cartella del modello (di norma sotto ~/.paddlex o ~/.paddleocr).
```

GLM-OCR (motore di confronto):

```bash
# Segui le istruzioni del runtime GLM-OCR per la tua piattaforma e verifica che
# l'import Python funzioni. L'adapter si trova in src/bolle/ocr/glm_ocr.py:
# se l'API/risposta del runtime differiscono, adatta SOLO _to_ocr_result().
```

> Suggerimento AVX-512: la build CPU di PaddlePaddle sfrutta AVX-512 sul Xeon 6134.
> Tieni le immagini a ~1024px (già impostato in `OcrConfig.resize_px`): è lo sweet
> spot per l'inferenza su CPU.

### 3. Prepara i documenti di prova

Metti **20-30 bolle reali** nella cartella `samples/` (PDF nativi, scansioni
PNG/JPG, e qualche XML FatturaPA se ne hai). Includi di proposito:
- alcune **scansioni degradate** (sbiadite, storte, fotocopiate),
- alcune bolle **estere** (lingua straniera).

> `samples/` è in `.gitignore`: i documenti dei fornitori NON vanno mai versionati.

### 4. Crea il ground-truth (la "verità" da confrontare)

Per ogni file, elenca le righe attese con **codice** e **quantità** corretti
(letti a mano). Parti dall'esempio:

```bash
cp samples/ground_truth.example.json samples/ground_truth.json
# poi compila samples/ground_truth.json con i tuoi file e le righe reali
```

Formato:

```json
{
  "bolla_01.pdf": [
    { "codice": "ABC123", "quantita": "10" },
    { "codice": "XYZ-9",  "quantita": "2.5" }
  ]
}
```

L'ordine delle righe nel JSON deve seguire l'ordine sul documento (l'allineamento
è per posizione). Bastano i campi `codice` e `quantita`.

### 5. Lancia il benchmark

```bash
python scripts/benchmark_ocr.py samples/ \
    --ground-truth samples/ground_truth.json \
    --engines paddleocr_vl glm_ocr
```

Output per ciascun motore:

```
=== paddleocr_vl ===
  righe valutate : 60
  codice esatto  : 57/60  (95.0%)
  quantita esatta: 59/60  (98.3%)
  CER codice     : 0.012  (0 = perfetto)
```

### 6. Come leggere i risultati

- **codice esatto** e **quantità esatta**: sono le metriche decisive. Più alte, meglio.
- **CER codice**: errore medio carattere-per-carattere sui codici (0 = perfetto);
  utile per capire *quanto* sbaglia quando sbaglia.
- Guarda in particolare le **bolle degradate/estere**: è lì che i modelli si
  differenziano.
- Vince il modello con codice/quantità più accurati sul **tuo** dato → lo imposti
  in `config/settings.yaml` (`ocr.engine`).

## Cosa serve da te per procedere

1. Conferma quale **runtime GLM-OCR** vuoi usare (così alliniamo l'adapter, se serve).
2. I **20-30 documenti** in `samples/` + il `ground_truth.json` compilato.
3. Gli output del benchmark: me li incolli e decidiamo insieme il motore e le
   eventuali soglie di confidenza per la coda di revisione.

---

# Alternativa OCR senza AVX — dots.ocr via llama.cpp (Windows)

Da usare quando PaddleOCR-VL non parte perché la CPU/VM non espone l'**AVX**
(errore `DLL load failed while importing libpaddle`). dots.ocr è un modello
vision-language (licenza MIT) che gira con **llama.cpp**, il quale funziona anche
su CPU senza AVX. Nessuna installazione di sistema, nessun diritto admin: solo
binari portabili e file di modello. Niente esce dalla macchina.

> Nota: senza AVX l'inferenza è più lenta (l'accuratezza **non** cambia). Ottimo
> per validare; per la produzione a pieno volume resta preferibile abilitare l'AVX.

## 1. Scarica llama.cpp (binario portabile)

Dalle release ufficiali https://github.com/ggml-org/llama.cpp/releases scarica lo
zip Windows CPU (es. `llama-<versione>-bin-win-cpu-x64.zip`) e scompattalo in una
cartella, es. `C:\llama`. Contiene `llama-server.exe`. La build CPU include più
varianti ggml e sceglie a runtime quella compatibile, quindi parte anche senza AVX.

## 2. Scarica il modello dots.ocr (GGUF)

Servono due file GGUF da Hugging Face: il **modello** e il **proiettore vision**
(`mmproj`). Cerca un repo GGUF di `dots.ocr` e scarica, ad esempio:
- `dots.ocr-Q4_K_M.gguf` (il modello quantizzato)
- `mmproj-dots.ocr-f16.gguf` (la parte visione)

Mettili in `C:\llama\models\`.

## 3. Avvia llama-server (lascialo aperto in una finestra)

```cmd
cd C:\llama
llama-server.exe -m models\dots.ocr-Q4_K_M.gguf --mmproj models\mmproj-dots.ocr-f16.gguf --port 8080 -c 4096
```

Quando vedi `server listening on http://127.0.0.1:8080` è pronto. **Non chiudere
questa finestra**: è il servizio OCR.

## 4. Configura il progetto per usare dots.ocr

In `config\settings.yaml` imposta:

```yaml
ocr:
  engine: dots_ocr
  dots_server_url: http://localhost:8080
  dots_model: dots.ocr
```

## 5. Lancia la pipeline sulla bolla

In un **secondo** prompt dei comandi (con la venv attiva, lasciando il server
acceso nell'altro):

```cmd
.venv\Scripts\activate.bat
bolle --config config\settings.yaml "C:\Users\RONTINIM\Downloads\scan_2026-05-28-12-25-06.pdf"
```

La prima pagina ci mette un po' (CPU senza AVX). Vedrai le righe estratte; codici
e quantità non verranno "risolti" finché non colleghiamo le API Oracle, ma
confermerà che dots.ocr legge il documento.

> Suggerimento: per *vedere* cosa legge il modello a crudo prima di passare dalla
> pipeline, puoi anche caricare l'immagine nella web UI di llama-server
> (http://localhost:8080) e incollare il prompt di estrazione.

### Provare su una sola pagina (consigliato per la taratura)

Un PDF con molte pagine richiede ore su CPU senza AVX. Per tarare prompt e
configurazione conviene lavorare su **una pagina alla volta** con `--pages`:

```cmd
:: solo pagina 1
python -m bolle.cli --config config\settings.yaml --pages 1 "C:\...\scan.pdf"
:: intervallo o lista
python -m bolle.cli --config config\settings.yaml --pages 1-3 "C:\...\scan.pdf"
python -m bolle.cli --config config\settings.yaml --pages 1,5,7 "C:\...\scan.pdf"
```

Le pagine sono **1-based**. Durante l'OCR vedrai l'avanzamento
`pagina N · token letti: ...` grazie allo streaming.
