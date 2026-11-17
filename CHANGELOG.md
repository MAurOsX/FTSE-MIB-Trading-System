# Changelog v2

- Benchmark corretto a `FTSEMIB.MI` (non `^FTMIB`).
- Aggiornato `yfinance` a 1.8.0 e aggiunto `curl_cffi` come dipendenza esplicita.
- Download giornaliero a 5 anni, sequenziale e senza thread paralleli.
- Fallback al chart endpoint Yahoo via `curl_cffi` quando il percorso yfinance fallisce; questo percorso non richiede il crumb.
- Cache Streamlit 12 ore e cache CSV locale di emergenza.
- Log dettagliati e diagnostica in dashboard; interruzione anticipata dello scanner in caso di HTTP 429/401.
- Aggiornati i 40 componenti del paniere e corretti i simboli Brunello Cucinelli (`BC.MI`) e Lottomatica (`LTMC.MI`).
- Aggiunta suite di smoke test per indicatori, paniere e backtest.
