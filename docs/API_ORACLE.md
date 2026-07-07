# Contratto API aziendali (verso Oracle) — bozza per l'IT

> Documento da condividere con chi implementa le API REST aziendali.
> La pipeline bolle **non parla mai con Oracle direttamente**: tutte le
> letture (anagrafica, cross-reference, ordini) e le scritture (bolla
> riconciliata) passano da queste API. Il client e' gia' implementato in
> `src/bolle/api_client.py` (`HttpAziendaApi`) con questi endpoint: se l'IT
> preferisce path o nomi campi diversi, va aggiornato SOLO quel file.

## Requisiti generali

| Aspetto | Richiesta |
|---|---|
| Protocollo | HTTP/1.1 REST, JSON UTF-8. Rete interna (nessun dato esce) |
| Autenticazione | Header `Authorization: Bearer <token>` statico (configurato via env `BOLLE_API_TOKEN`) |
| Base URL | Configurabile (env `BOLLE_API_BASE_URL`), es. `http://oracle-api.azienda.local:8080` |
| Timeout | La pipeline attende max 30 s per chiamata (configurabile) |
| Errori | Status HTTP standard; 4xx/5xx con body `{"errore": "..."}` |
| Volumi attesi | ~100 bolle/giorno, ~100 righe/bolla → ~10.000 lookup/giorno, distribuiti |

## 1. GET /anagrafica — esistenza articolo

Verifica se un codice articolo interno esiste in anagrafica. E' la chiamata
piu' frequente: il **codice commerciale** letto dalla bolla (es. `99928399`)
viene validato qui; se esiste, la riga e' risolta senza cross-reference.

**Request**
```
GET /anagrafica?codice=99928399
```

**Response 200**
```json
{ "esiste": true }
```
(`false` se il codice non e' in anagrafica; sempre 200, mai 404)

## 2. GET /crossref — cross reference codice fornitore → interno

Traduzione dal codice articolo del fornitore al nostro codice interno.
Usata come fallback quando il codice commerciale non e' leggibile in bolla.

**Request**
```
GET /crossref?fornitore=VERNICIATURA%20BOLOGNESE%20S.R.L.&codice=088608.0401
```
`fornitore` puo' essere assente (in quel caso match solo sul codice).

**Response 200**
```json
{ "codice_interno": "99928399" }
```
```json
{ "codice_interno": null }
```
(null se nessuna corrispondenza; sempre 200)

## 3. GET /ordini/righe — righe di un ordine di acquisto

Righe attese dell'ordine indicato in bolla ("Vs. Ordine"). Servono per la
riconciliazione: confronto tra quantita' attese e quantita' dichiarate.

**Request**
```
GET /ordini/righe?numero_ordine=25402153-OC-00040
```

**Response 200** — array (vuoto se ordine sconosciuto):
```json
[
  {
    "numero_ordine": "25402153-OC-00040",
    "codice_interno": "99951827",
    "quantita_attesa": "18",
    "data_consegna": "2026-06-05",
    "fornitore": "VERNICIATURA BOLOGNESE S.R.L."
  }
]
```
- `quantita_attesa`: stringa decimale (evita problemi di float).
- `data_consegna`: ISO `YYYY-MM-DD`, puo' essere null.

## 4. GET /ordini/aperti — ordini aperti per fornitore+articolo

Per le **eccedenze**: se la bolla dichiara piu' del previsto, la pipeline
cerca ordini aperti dello stesso fornitore/articolo con consegna successiva
per proporre anticipo/spostamento.

**Request**
```
GET /ordini/aperti?fornitore=VERNICIATURA%20BOLOGNESE%20S.R.L.&codice=99951827
```

**Response 200** — stesso schema di `/ordini/righe` (solo righe di ordini
aperti, escluso l'ordine gia' in esame).

## 5. POST /bolle — invio bolla riconosciuta

Scrittura della bolla estratta (testata + righe risolte) sulle tabelle di
ribaltamento. La pipeline invia SOLO le righe risolte; le non risolte restano
nella coda di revisione locale.

**Request**
```json
POST /bolle
{
  "documento_id": "scan_2026-05-28-12-25-06_bolla01",
  "testata": {
    "numero_ordine": "25402153-OC-00040",
    "fornitore": "VERNICIATURA BOLOGNESE S.R.L.",
    "numero_bolla": "26DT-01995",
    "data_bolla": "2026-05-27"
  },
  "righe": [
    {
      "numero_riga": 1,
      "codice_interno": "99951827",
      "codice_letto": "99951827",
      "quantita": "18",
      "prezzo_unitario": null,
      "totale_riga": null
    }
  ]
}
```

**Response 200**
```json
{ "id": "identificativo-assegnato-dal-gestionale" }
```

**Idempotenza richiesta**: un secondo POST con lo stesso `documento_id` non
deve creare un duplicato (upsert su `documento_id` o rifiuto con 409).

## Note per l'implementazione

- **Performance**: `/anagrafica` e `/crossref` sono lookup puntuali su chiave;
  con indici adeguati non servono cache lato API.
- **Sicurezza**: il token e' letto dalla pipeline via variabile d'ambiente
  (`BOLLE_API_TOKEN`), mai salvato nel repository o nei file di config.
- **Ambienti**: utile un endpoint di test/staging con dati copia: la pipeline
  si punta all'uno o all'altro cambiando `BOLLE_API_BASE_URL`.
- **Rollout**: finche' le API non sono pronte la pipeline gira con
  `api.backend: memory` (nessuna chiamata esterna, righe in revisione).
  Il passaggio e': `settings.yaml -> backend: http` + le due variabili d'ambiente.

## Domande aperte per l'IT

1. Il numero d'ordine in bolla arriva in formati diversi
   (`25402153-OC-00040`, `OC/26404399`, `26439577 SU`): la GET
   `/ordini/righe` deve normalizzare lato server o preferite che la pipeline
   invii un formato canonico? (da definire insieme su casi reali)
2. Una bolla puo' riferirsi a **piu' ordini** (visto su DDT reali con 5
   ordini): oggi la pipeline usa l'ordine di testata; l'associazione
   ordine→riga e' un'evoluzione prevista. Il POST /bolle deve gia' accettare
   un `numero_ordine` opzionale per riga?
3. Autenticazione: va bene un Bearer statico o serve OAuth2/rotazione?
