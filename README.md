# FTSE MIB Technical Trading Lab — v2

Dashboard Streamlit per analisi tecnica giornaliera dei componenti FTSE MIB.

## Caratteristiche

- Benchmark corretto: `FTSEMIB.MI`.
- Storico daily di 5 anni (`1d`).
- `yfinance` aggiornato e `curl_cffi` come backend HTTP preferenziale.
- Fallback diretto al chart endpoint Yahoo quando il percorso `yfinance` non restituisce dati.
- Download sequenziale, niente parallelismo, cache Streamlit 12 ore e cache CSV locale di emergenza nel runtime.
- Circuit breaker: se Yahoo restituisce 429/401 durante lo scanner, interrompe la scansione invece di continuare con altre richieste.
- Scanner FTSE MIB con punteggio leggibile `x/6` e ordinamento per score.
- Grafico con candele blu (rialziste) e rosse (ribassiste), EMA20, SMA20/50/200, PSAR e marker dei pattern.
- Rilevamento euristico di doji, hammer, shooting star, bullish/bearish engulfing, breakout/breakdown e possibili testa-e-spalle/inversi.
- Guida integrata agli indicatori e spiegazione operativa del backtest semplificato.
- Paniere iniziale aggiornato al 17 novembre 2026; controllare le revisioni successive.

## Avvio locale

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy su Streamlit Community Cloud

1. Scaricare ed estrarre lo ZIP.
2. Nel repository GitHub sostituire i file `app.py`, `engine.py`, `requirements.txt`, `ftsemib_constituents.csv` e `README.md` con quelli di questo pacchetto.
3. Effettuare il commit sul branch `main`.
4. In Streamlit Community Cloud aprire l'app e selezionare **Reboot app** se il deploy non parte automaticamente.
5. Attendere la reinstallazione delle dipendenze.

## Come sono gestiti i dati

- Il benchmark `FTSEMIB.MI` viene scaricato all'apertura dell'app.
- I 40 titoli vengono scaricati solo quando si avvia lo scanner o si apre l'analisi/backtest del singolo titolo.
- La cache per ticker dura 12 ore. Il pulsante di aggiornamento forzato svuota la cache e deve essere usato con parsimonia.
- In caso di errore Yahoo, l'app prova il chart endpoint tramite `curl_cffi` senza richiedere il crumb. Se anche questo fallisce, usa l'ultimo CSV locale disponibile nel runtime.
- La cache locale non è storage persistente garantito su Streamlit Community Cloud: può essere cancellata quando il container viene riavviato o ricreato.

## Note importanti

Yahoo Finance non offre un'API pubblica ufficiale per questo uso. L'accesso automatizzato può essere limitato (HTTP 429/401) e nessuna modifica al codice può garantire disponibilità continua. Il ritardo dei prezzi non elimina i rate limit. Se il blocco riguarda l'IP del server cloud, potrebbe essere necessario attendere o usare un provider con API e condizioni d'uso esplicite.

Il backtest è didattico e semplificato: non include slippage, fiscalità, dividendi, corporate actions, liquidità, survivorship bias o costi di mercato completi. Non costituisce consulenza finanziaria né segnale automatico di acquisto/vendita.

## Strategia singolo titolo v3
La sezione Analisi titolo mostra un'indicazione COMPRA/ATTENDI con filtro FTSE MIB (close sopra SMA200 e SMA200 crescente), trend del titolo (close sopra SMA200, SMA50 sopra SMA200 e SMA200 crescente), forza relativa a 60 sedute positiva e trigger su breakout 55 sedute oppure ritracciamento con recupero EMA20. Lo stop indicativo è 3 × ATR(14).

La sezione Backtest permette di confrontare Trend + Momentum + ATR con la precedente PSAR + SMA50 e Buy & Hold. I segnali di chiusura vengono applicati dalla seduta successiva; lo stop ATR viene verificato su Open/Low giornalieri. Il backtest non include slippage, tasse, dividendi o corporate actions.
