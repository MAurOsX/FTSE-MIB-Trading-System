# Changelog

## v3.0 - Strategia su singolo titolo
- Aggiunta indicazione operativa COMPRA / ATTENDI basata su filtro di mercato, trend del titolo, forza relativa e trigger di ingresso.
- Nuovo backtest Trend + Momentum + ATR, confrontabile con Buy & Hold e con la precedente strategia PSAR + SMA50.
- Ingressi su breakout a 55 sedute o recupero EMA20 dopo ritracciamento; stop iniziale/trailing a 3 ATR(14), uscita su perdita SMA50 o filtro FTSE MIB.
- Segnali calcolati a chiusura e applicati dalla seduta successiva.
