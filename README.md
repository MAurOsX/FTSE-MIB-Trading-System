# FTSE MIB Technical Trading Lab

Dashboard Streamlit per analisi tecnica giornaliera dei componenti FTSE MIB e confronto con il benchmark `^FTMIB`.

## Funzioni
- Parabolic SAR (parametri 0,02 / 0,02 / 0,20)
- SMA 20/50/200 ed EMA 20
- Volumi e rapporto volume/media mobile
- Correlazione mobile dei rendimenti giornalieri con FTSE MIB
- Forza relativa a 20 sedute rispetto all'indice
- Rilevazione euristica di doppio/triplo massimo/minimo, testa e spalle e triangoli
- Scanner con score tecnico, BUY / SELL / NEUTRAL
- Grafico candlestick e volumi
- Backtest dimostrativo long-only con segnale applicato dalla seduta successiva

## Avvio locale
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Pubblicazione gratuita su Streamlit Community Cloud
1. Crea un repository GitHub e carica tutti i file di questa cartella.
2. Accedi a https://share.streamlit.io/ con GitHub.
3. Seleziona il repository, branch `main`, file `app.py`.
4. Premi Deploy. Streamlit costruirà l'app usando `requirements.txt`.

## Aggiornamento componenti
La composizione FTSE MIB è soggetta a revisione. Aggiorna `ftsemib_constituents.csv` quando Borsa Italiana/FTSE Russell modifica l'indice. La pagina ufficiale è:
https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html

## Dati e avvertenze
La versione iniziale usa Yahoo Finance tramite `yfinance` (dati gratuiti non ufficiali, senza SLA e potenzialmente ritardati o incompleti). I ticker .MI e il simbolo indice `^FTMIB` vanno verificati in caso di errori di download. Non è un feed professionale e non va usato come unica fonte per decisioni operative.

Le figure sono riconosciute tramite regole numeriche sui pivot, quindi possono produrre falsi positivi/negativi. Il backtest è didattico e non considera commissioni, slippage, tasse, spread, dividendi, corporate actions o survivorship bias. Lo scanner non invia ordini e non fornisce consulenza finanziaria.
