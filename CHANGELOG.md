# Changelog v2

- Benchmark corretto a `FTSEMIB.MI` (non `^FTMIB`).
- Aggiornato `yfinance` a 1.8.0 e aggiunto `curl_cffi` come dipendenza esplicita.
- Download giornaliero a 5 anni, sequenziale e senza thread paralleli.
- Fallback al chart endpoint Yahoo via `curl_cffi` quando il percorso yfinance fallisce; questo percorso non richiede il crumb.
- Cache Streamlit 12 ore e cache CSV locale di emergenza.
- Log dettagliati e diagnostica in dashboard; interruzione anticipata dello scanner in caso di HTTP 429/401.
- Aggiornati i 40 componenti del paniere e corretti i simboli Brunello Cucinelli (`BC.MI`) e Lottomatica (`LTMC.MI`).
- Aggiunta suite di smoke test per indicatori, paniere e backtest.


## v2.1 – Scanner, grafici e guida operativa

- Score scanner visualizzato come `x/6`, mantenendo ordinamento numerico.
- Candele blu per sedute positive e rosse per sedute negative.
- EMA20 aggiunta al grafico insieme a SMA20/50/200 e PSAR.
- Marker sul grafico per doji, hammer, shooting star, engulfing, breakout/breakdown e possibili figure testa-e-spalle/inverse.
- Dettaglio dei sei criteri del punteggio nella scheda Analisi titolo.
- Guida integrata a indicatori, volumi, forza relativa, correlazione e pattern.
- Istruzioni per eseguire e interpretare il backtest, con definizione delle metriche.
- Il riconoscimento delle figure è euristico e deve essere verificato visivamente: non costituisce un segnale garantito.
