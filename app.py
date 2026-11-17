import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import date

from engine import (
    load_universe, download_market_data, analyze_symbol, build_scan,
    make_backtest, pattern_summary
)

st.set_page_config(page_title="FTSE MIB Technical Trading Lab", page_icon="📈", layout="wide")
st.title("FTSE MIB · Technical Trading Lab")
st.caption("Analisi giornaliera quantitativa · PSAR · medie mobili · volumi · pattern · correlazione con FTSE MIB")

with st.sidebar:
    st.header("Impostazioni")
    period = st.selectbox("Storico", ["2y", "3y", "5y", "10y"], index=2)
    corr_window = st.slider("Finestra correlazione (giorni)", 20, 120, 60, 5)
    vol_window = st.slider("Media volumi (giorni)", 10, 60, 20, 5)
    min_score = st.slider("Punteggio minimo segnale", 1, 8, 4)
    selected_list = st.selectbox("Universo", ["FTSE MIB – componenti configurati", "Tutti i ticker del file"])
    st.caption("I dati vengono scaricati da Yahoo Finance tramite yfinance.")

universe = load_universe()
if selected_list == "FTSE MIB – componenti configurati":
    symbols = universe.loc[universe["active"].astype(str).str.lower().isin(["true", "1", "yes"]), "ticker"].tolist()
else:
    symbols = universe["ticker"].tolist()

@st.cache_data(ttl=3600, show_spinner=False)
def get_data(symbols_tuple, period):
    return download_market_data(list(symbols_tuple), period)

with st.spinner("Scaricamento quotazioni giornaliere..."):
    data = get_data(tuple(symbols), period)

if "FTSEMIB" not in data or data["FTSEMIB"].empty:
    st.error("Non è stato possibile scaricare il benchmark FTSEMIB. Verifica la connessione o il ticker Yahoo Finance.")
    st.stop()

benchmark = data["FTSEMIB"]
available = [s for s in symbols if s in data and not data[s].empty]
if not available:
    st.error("Nessun titolo disponibile. Controlla i ticker nel file ftsemib_constituents.csv.")
    st.stop()

scan = build_scan(data, available, benchmark, corr_window, vol_window)
scan = scan.sort_values(["score", "relative_strength_20d"], ascending=False)

last_date = benchmark.index.max().date()
m1, m2, m3, m4 = st.columns(4)
m1.metric("Titoli analizzati", len(available))
m2.metric("Ultima seduta disponibile", str(last_date))
m3.metric("Segnali BUY", int((scan["signal"] == "BUY").sum()))
m4.metric("Segnali SELL", int((scan["signal"] == "SELL").sum()))

tab1, tab2, tab3, tab4 = st.tabs(["Scanner FTSE MIB", "Analisi titolo", "Backtest", "Metodo e limiti"])

with tab1:
    st.subheader("Classifica tecnica dei componenti")
    filtered = scan[(scan["score"] >= min_score) | (scan["signal"] != "NEUTRAL")]
    st.dataframe(
        filtered[["name", "ticker", "close", "daily_change_pct", "signal", "score",
                  "psar_state", "ma_state", "volume_ratio", "correlation",
                  "relative_strength_20d", "patterns"]]
        .rename(columns={
            "name":"Società", "ticker":"Ticker", "close":"Ultimo prezzo",
            "daily_change_pct":"Var. giornaliera %", "signal":"Segnale",
            "score":"Score / 8", "psar_state":"PSAR", "ma_state":"Medie",
            "volume_ratio":"Volumi / media", "correlation":"Corr. 60g",
            "relative_strength_20d":"Forza relativa 20g", "patterns":"Figure"
        }),
        use_container_width=True, hide_index=True
    )
    st.download_button("Scarica scanner CSV", scan.to_csv(index=False).encode("utf-8"),
                       "ftsemib_scanner.csv", "text/csv")

with tab2:
    names = universe.set_index("ticker")["name"].to_dict()
    choice = st.selectbox("Seleziona titolo", available,
                          format_func=lambda x: f"{names.get(x, x)} ({x})")
    df = data[choice].copy()
    result = analyze_symbol(df, benchmark, corr_window, vol_window)
    st.subheader(f"{names.get(choice, choice)} · {choice}")
    latest = result.iloc[-1]
    a,b,c,d,e = st.columns(5)
    a.metric("Chiusura", f"{latest['Close']:.2f}")
    b.metric("Segnale", str(latest["signal"]))
    c.metric("Score", f"{int(latest['score'])}/8")
    d.metric("Correlazione", f"{latest['correlation']:.2f}" if pd.notna(latest["correlation"]) else "n.d.")
    e.metric("Volumi / media", f"{latest['volume_ratio']:.2f}x" if pd.notna(latest["volume_ratio"]) else "n.d.")

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        row_heights=[0.72, 0.28])
    fig.add_trace(go.Candlestick(x=result.index, open=result["Open"], high=result["High"],
                                 low=result["Low"], close=result["Close"], name="Prezzo"), row=1, col=1)
    for col, label in [("SMA20","SMA 20"),("SMA50","SMA 50"),("SMA200","SMA 200"),("EMA20","EMA 20")]:
        fig.add_trace(go.Scatter(x=result.index, y=result[col], name=label, mode="lines"), row=1, col=1)
    bull = result[result["PSAR"] < result["Close"]]
    bear = result[result["PSAR"] >= result["Close"]]
    fig.add_trace(go.Scatter(x=bull.index, y=bull["PSAR"], mode="markers", name="PSAR rialzista",
                             marker=dict(color="#16a34a", size=4)), row=1, col=1)
    fig.add_trace(go.Scatter(x=bear.index, y=bear["PSAR"], mode="markers", name="PSAR ribassista",
                             marker=dict(color="#dc2626", size=4)), row=1, col=1)
    fig.add_trace(go.Bar(x=result.index, y=result["Volume"], name="Volumi"), row=2, col=1)
    fig.add_trace(go.Scatter(x=result.index, y=result["VolumeSMA"], name="Media volumi", mode="lines"), row=2, col=1)
    fig.update_layout(height=720, xaxis_rangeslider_visible=False, legend_orientation="h",
                      margin=dict(l=10,r=10,t=35,b=10))
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Figure tecniche individuate**")
    patterns = pattern_summary(result)
    if patterns:
        for p in patterns[-8:]:
            st.write(f"- {p}")
    else:
        st.write("Nessuna figura maggiore rilevata con i criteri attuali.")
    st.markdown("**Indicatori recenti**")
    st.dataframe(result.tail(10)[["Close","PSAR","SMA20","SMA50","SMA200","Volume","VolumeSMA",
                                  "volume_ratio","correlation","relative_strength_20d","score","signal"]],
                 use_container_width=True)

with tab3:
    st.subheader("Backtest dimostrativo della strategia")
    st.write("Segnale calcolato alla chiusura; posizione applicata dalla seduta successiva. Long-only, senza leva.")
    bt_symbol = st.selectbox("Titolo per il backtest", available, key="bt")
    bt = make_backtest(data[bt_symbol], benchmark, corr_window, vol_window, min_score)
    if bt is not None and len(bt) > 5:
        total = (1 + bt["strategy_return"].fillna(0)).prod() - 1
        bh = (1 + bt["buy_hold_return"].fillna(0)).prod() - 1
        ann = (1 + total) ** (252 / max(len(bt),1)) - 1 if total > -1 else -1
        dd = (bt["equity"] / bt["equity"].cummax() - 1).min()
        x1,x2,x3,x4 = st.columns(4)
        x1.metric("Strategia", f"{total:.1%}")
        x2.metric("Buy & hold", f"{bh:.1%}")
        x3.metric("CAGR indicativo", f"{ann:.1%}")
        x4.metric("Max drawdown", f"{dd:.1%}")
        chart = go.Figure()
        chart.add_trace(go.Scatter(x=bt.index, y=bt["equity"], name="Strategia"))
        chart.add_trace(go.Scatter(x=bt.index, y=bt["buy_hold_equity"], name="Buy & hold"))
        chart.update_layout(height=420, yaxis_title="Capitale iniziale = 1")
        st.plotly_chart(chart, use_container_width=True)
        st.caption("Backtest grezzo: non include commissioni, slippage, imposte, spread, dividendi o impatto di mercato. Non costituisce una simulazione eseguibile.")
    else:
        st.info("Dati insufficienti per il backtest.")

with tab4:
    st.subheader("Regole della versione iniziale")
    st.markdown("""
**Indicatori**
- Parabolic SAR (0,02 / 0,02 / 0,20), calcolato sui massimi/minimi.
- SMA 20, 50 e 200; EMA 20.
- Volume relativo: volume seduta / media mobile dei volumi a 20 sedute.
- Correlazione Pearson mobile tra rendimenti giornalieri del titolo e del FTSE MIB.
- Forza relativa: rendimento del titolo meno rendimento dell'indice sugli ultimi 20 giorni.

**Figure**
- Doppio massimo / doppio minimo.
- Testa e spalle / testa e spalle rovesciato.
- Triangolo ascendente / discendente / simmetrico.
- Rilevazione euristica su pivot di prezzo: le figure sono indicazioni da verificare, non riconoscimento visivo certificato.

**Score**
- Un punto per ciascuna condizione: prezzo sopra SMA20, SMA20 sopra SMA50, SMA50 sopra SMA200,
  PSAR rialzista, volume relativo almeno 1,20, forza relativa positiva, benchmark sopra SMA50,
  figura rialzista recente.
- BUY da score 6; SELL da score 2 o meno con PSAR ribassista; altrimenti NEUTRAL.
- Lo scanner mostra anche lo score grezzo e i singoli indicatori.

**Dati e limiti**
- Yahoo Finance via yfinance è una fonte gratuita non ufficiale, senza garanzia di completezza, continuità o licenza per uso professionale.
- I ticker sono configurati nel CSV e vanno aggiornati quando cambia la composizione FTSE MIB.
- Il benchmark usa `FTSEMIB`; se Yahoo modifica il simbolo, va aggiornato nel codice.
- I segnali sono strumenti di screening e non raccomandazioni personalizzate d'investimento.
""")
    st.markdown("Fonte per la composizione: [Borsa Italiana – FTSE MIB](https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html).")
    st.markdown("Hosting: [Streamlit Community Cloud](https://share.streamlit.io/), gratuito, collegato a GitHub.")
