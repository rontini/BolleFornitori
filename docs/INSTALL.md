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
