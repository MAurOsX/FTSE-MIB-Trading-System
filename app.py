from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine import (
    BENCHMARK,
    YahooDataError,
    add_indicators,
    fetch_history,
    load_constituents,
    load_history_local,
    parabolic_sar,
    run_backtest,
    scanner_row,
    score_latest,
    save_history_local,
    detect_chart_patterns,
)

st.set_page_config(page_title="FTSE MIB Technical Lab", page_icon="📈", layout="wide")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger("ftsemib.app")

CACHE_TTL_SECONDS = 12 * 60 * 60
PERIOD = "5y"


@st.cache_data(ttl=CACHE_TTL_SECONDS, max_entries=60, show_spinner=False)
def cached_fetch(ticker: str, period: str = PERIOD) -> pd.DataFrame:
    return fetch_history(ticker, period=period, interval="1d")


def get_history(ticker: str, force: bool = False) -> tuple[pd.DataFrame, str]:
    if force:
        cached_fetch.clear()
    try:
        data = cached_fetch(ticker, PERIOD)
        try:
            save_history_local(ticker, data)
        except Exception as cache_exc:
            LOGGER.warning("Cache CSV locale non scrivibile per %s: %s", ticker, cache_exc)
        return data, "Yahoo Finance · cache Streamlit / download"
    except Exception as exc:
        LOGGER.exception("Download fallito per %s", ticker)
        local = load_history_local(ticker)
        if not local.empty:
            return local, f"Cache locale · download Yahoo non riuscito ({type(exc).__name__})"
        raise YahooDataError(str(exc)) from exc


def euro(value, decimals=2):
    if pd.isna(value):
        return "n.d."
    return f"€ {value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(value, decimals=2):
    if pd.isna(value):
        return "n.d."
    return f"{value * 100:.{decimals}f}%".replace(".", ",")


def make_price_chart(data: pd.DataFrame, title: str):
    plotted = detect_chart_patterns(data.tail(300))
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=plotted.index, open=plotted["Open"], high=plotted["High"], low=plotted["Low"], close=plotted["Close"],
        name="Prezzo", increasing_line_color="#2878B5", increasing_fillcolor="#2878B5",
        decreasing_line_color="#D94B4B", decreasing_fillcolor="#D94B4B",
    ))
    for col, label, color in [
        ("EMA20", "EMA 20", "#F39C12"),
        ("SMA20", "SMA 20", "#17A2B8"),
        ("SMA50", "SMA 50", "#8E44AD"),
        ("SMA200", "SMA 200", "#566573"),
    ]:
        if col in plotted.columns:
            fig.add_trace(go.Scatter(x=plotted.index, y=plotted[col], mode="lines", name=label, line={"width": 1.5, "color": color}))
    if "PSAR" in plotted.columns:
        fig.add_trace(go.Scatter(x=plotted.index, y=plotted["PSAR"], mode="markers", name="Parabolic SAR", marker={"size": 4, "color": "#222222", "symbol": "circle"}))
    pattern_rows = plotted[plotted["Pattern"].astype(str).str.len() > 0]
    if not pattern_rows.empty:
        bullish_words = ("Bullish", "Hammer", "Breakout", "inverso")
        for idx, row in pattern_rows.iterrows():
            label = row["Pattern"]
            bullish = any(word.lower() in label.lower() for word in bullish_words)
            y = row["Low"] * 0.985 if bullish else row["High"] * 1.015
            fig.add_trace(go.Scatter(
                x=[idx], y=[y], mode="markers+text", name="Pattern rilevati",
                text=[label], textposition="bottom center" if bullish else "top center",
                marker={"size": 10, "symbol": "triangle-up" if bullish else "triangle-down", "color": "#168A52" if bullish else "#C0392B"},
                showlegend=False, hovertemplate="%{text}<extra></extra>"))
    fig.update_layout(
        title=title, height=650, xaxis_rangeslider_visible=False,
        margin={"l": 10, "r": 10, "t": 50, "b": 10}, legend={"orientation": "h", "y": 1.02},
        hovermode="x unified",
    )
    return fig


def render_indicator_guide():
    with st.expander("Guida alla lettura del grafico e degli indicatori", expanded=False):
        st.markdown("""
        **Candele blu e rosse**
        - **Blu:** chiusura superiore all'apertura (seduta positiva).
        - **Rosso:** chiusura inferiore all'apertura (seduta negativa).
        - Il rettangolo è il corpo (apertura/chiusura); le ombre mostrano massimo e minimo della seduta.

        **Medie mobili**
        - **EMA20:** media esponenziale a 20 sedute. Pesa maggiormente i prezzi recenti e reagisce più velocemente della SMA20. Prezzo sopra EMA20: momentum di breve più favorevole; sotto: debolezza di breve. Non è un segnale autonomo.
        - **SMA20 / SMA50 / SMA200:** medie semplici degli ultimi 20, 50 e 200 giorni di Borsa. Servono a leggere rispettivamente breve, medio e lungo periodo. La sequenza SMA20 > SMA50 > SMA200 indica allineamento rialzista; l'opposto indica allineamento ribassista.
        - **Parabolic SAR:** puntini sotto il prezzo indicano fase rialzista; sopra il prezzo fase ribassista. Può generare falsi cambi di direzione nei mercati laterali.

        **Volumi e forza relativa**
        - **Volume ratio:** volume della seduta diviso per la media degli ultimi 20 giorni. 1,0x = in linea; 1,5x = 50% sopra la media; 0,5x = metà della media. Un breakout con volumi elevati è generalmente più credibile.
        - **RS 20g / 60g:** rendimento del titolo meno rendimento del FTSE MIB nello stesso periodo. Positivo = titolo che sovraperforma l'indice; negativo = sottoperforma.
        - **Correlazione 60g:** da -1 a +1; misura quanto i rendimenti giornalieri si sono mossi insieme all'indice. Non misura la qualità del titolo né la direzione futura.

        **Livelli e pattern**
        - **Breakout 20g:** chiusura sopra il massimo delle 20 sedute precedenti (la seduta corrente è esclusa dal livello). Possibile rottura rialzista; attenzione ai falsi breakout.
        - **Breakdown 20g:** chiusura sotto il minimo delle 20 sedute precedenti.
        - **Doji:** apertura e chiusura molto vicine; equilibrio/indecisione. Conta soprattutto dopo un trend esteso e vicino a supporti/resistenze.
        - **Hammer (martello):** corpo piccolo nella parte alta e lunga ombra inferiore; possibile rifiuto dei prezzi bassi, potenzialmente rialzista dopo una discesa.
        - **Shooting star:** corpo piccolo nella parte bassa e lunga ombra superiore; possibile rifiuto dei prezzi alti, potenzialmente ribassista dopo una salita.
        - **Bullish engulfing:** candela positiva che avvolge il corpo della precedente candela negativa; possibile inversione rialzista.
        - **Bearish engulfing:** candela negativa che avvolge il corpo della precedente positiva; possibile inversione ribassista.
        - **Testa e spalle / inverso:** pattern di inversione formato da tre massimi/minimi, con il centrale più pronunciato. Il segnale è considerato confermato solo alla rottura della neckline. Il rilevamento automatico è euristico e può sbagliare: verificare sempre la figura sul grafico.

        I marker verdi sono pattern/rotture potenzialmente rialzisti; quelli rossi potenzialmente ribassisti. Il doji è neutrale. Nessun pattern garantisce un movimento successivo.
        """)


def show_fetch_error(ticker: str, exc: Exception):
    st.error(f"Non è stato possibile scaricare {ticker} da Yahoo Finance.")
    st.code(str(exc))
    st.info(
        "Il sistema non ripete richieste in rapida successione. Se Yahoo restituisce HTTP 429/401, "
        "attendi prima di riprovare. Se esiste uno storico locale valido, viene utilizzato e identificato come cache."
    )


st.title("FTSE MIB · Technical Trading Lab")
st.caption("Analisi tecnica giornaliera · storico 5 anni · dati Yahoo Finance ritardati · nessuna esecuzione ordini")

with st.sidebar:
    st.subheader("Configurazione dati")
    st.write(f"Benchmark: **{BENCHMARK}**")
    st.write("Timeframe: **1 giorno**")
    st.write("Storico: **5 anni**")
    st.write("Cache: **12 ore**")
    refresh = st.button("Forza aggiornamento dati", use_container_width=True)
    if refresh:
        cached_fetch.clear()
        st.cache_data.clear()
        st.session_state["force_refresh_once"] = True
        st.rerun()
    st.caption("L'aggiornamento forzato svuota la cache e genera nuove richieste a Yahoo. Usalo solo quando necessario.")

pages = ["Scanner FTSE MIB", "Analisi titolo", "Backtest", "Metodo e diagnostica"]
page = st.radio("Sezione", pages, horizontal=True, label_visibility="collapsed")
force_now = bool(st.session_state.pop("force_refresh_once", False))

# Benchmark is downloaded for all analysis pages, but the full universe is never
# downloaded automatically when the app opens.
try:
    with st.spinner(f"Caricamento benchmark {BENCHMARK}…"):
        benchmark_raw, benchmark_source = get_history(BENCHMARK, force=force_now)
    benchmark_ind = add_indicators(benchmark_raw)
    last_b = benchmark_raw.iloc[-1]
    prev_b = benchmark_raw["Close"].iloc[-2] if len(benchmark_raw) > 1 else np.nan
    b_change = (last_b["Close"] / prev_b - 1) if pd.notna(prev_b) and prev_b else np.nan
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("FTSE MIB", f"{last_b['Close']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    c2.metric("Variazione ultima seduta", pct(b_change))
    c3.metric("Ultima barra", benchmark_raw.index[-1].strftime("%d/%m/%Y"))
    c4.metric("Fonte", "Yahoo / cache")
    st.caption(f"Fonte dati: {benchmark_source}. Ultimo aggiornamento disponibile: {benchmark_raw.index[-1].strftime('%d/%m/%Y')}.")
except Exception as exc:
    benchmark_raw = pd.DataFrame()
    benchmark_ind = pd.DataFrame()
    show_fetch_error(BENCHMARK, exc)
    st.stop()

if page == "Scanner FTSE MIB":
    st.subheader("Scanner dei 40 componenti")
    st.write("Lo scanner scarica i titoli soltanto su richiesta. I dati già acquisiti vengono riutilizzati dalla cache per 12 ore.")
    constituents = load_constituents()
    st.caption(f"Universo configurato: {len(constituents)} titoli · benchmark {BENCHMARK}")
    if st.button("Avvia / aggiorna scansione FTSE MIB", type="primary"):
        rows = []
        failures = []
        progress = st.progress(0, text="Preparazione scansione…")
        stop_for_rate_limit = False
        for i, item in constituents.iterrows():
            ticker = item["ticker"]
            progress.progress((i + 1) / len(constituents), text=f"Analisi {ticker} ({i + 1}/{len(constituents)})")
            try:
                raw, _source = get_history(ticker)
                rows.append(scanner_row(ticker, item["company"], raw, benchmark_raw))
            except Exception as exc:
                failures.append({"Ticker": ticker, "Errore": str(exc)[:250]})
                err_text = str(exc).lower()
                if "429" in err_text or "401" in err_text or "rate limit" in err_text or "unauthorized" in err_text:
                    stop_for_rate_limit = True
                    LOGGER.warning("Interrompo scansione: Yahoo ha limitato le richieste su %s", ticker)
                    break
            # Deliberate pacing for uncached symbols; cached reads are fast.
            time.sleep(0.35)
        progress.empty()
        st.session_state["scanner_rows"] = rows
        st.session_state["scanner_failures"] = failures
        st.session_state["scanner_rate_limited"] = stop_for_rate_limit

    if "scanner_rows" in st.session_state:
        scan = pd.DataFrame(st.session_state["scanner_rows"])
        if not scan.empty:
            if "Score ordinamento" in scan.columns:
                scan = scan.sort_values(["Score ordinamento", "Var %"], ascending=[False, False], na_position="last")
                scan = scan.drop(columns=["Score ordinamento"])
            st.dataframe(
                scan,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Prezzo": st.column_config.NumberColumn(format="€ %.2f"),
                    "Var %": st.column_config.NumberColumn(format="%.2f%%"),
                    "PSAR": st.column_config.NumberColumn(format="€ %.2f"),
                    "Vol/Media20": st.column_config.NumberColumn(format="%.2fx"),
                    "RS 20g %": st.column_config.NumberColumn(format="%.2f%%"),
                    "Corr. 60g": st.column_config.NumberColumn(format="%.2f"),
                },
            )
            st.download_button("Scarica scanner CSV", scan.to_csv(index=False).encode("utf-8-sig"), "ftsemib_scanner.csv", "text/csv")
        else:
            st.warning("Nessun titolo analizzato. Verifica i messaggi di errore qui sotto.")
        failures = st.session_state.get("scanner_failures", [])
        if failures:
            st.warning(f"Titoli non scaricati: {len(failures)}")
            st.dataframe(pd.DataFrame(failures), use_container_width=True, hide_index=True)
        if st.session_state.get("scanner_rate_limited"):
            st.error("Scansione interrotta: Yahoo ha restituito un errore di rate limit/autorizzazione. Non vengono inviate altre richieste per evitare di aggravare il blocco.")
    else:
        st.info("Premi ‘Avvia / aggiorna scansione FTSE MIB’ per caricare il paniere. Il benchmark è già stato verificato.")

elif page == "Analisi titolo":
    st.subheader("Analisi tecnica di un singolo titolo")
    constituents = load_constituents()
    options = {f"{r.company} ({r.ticker})": r.ticker for r in constituents.itertuples()}
    selected_label = st.selectbox("Titolo", list(options.keys()))
    ticker = options[selected_label]
    try:
        with st.spinner(f"Caricamento {ticker}…"):
            raw, source = get_history(ticker)
        data = add_indicators(raw, benchmark_raw)
        row = data.iloc[-1]
        signal = score_latest(data)
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Ultimo prezzo", euro(row["Close"]))
        m2.metric("Segnale", signal["signal"])
        m3.metric("Score", f"{signal['score']}/6")
        m4.metric("Volume / media 20", f"{row['VolumeRatio']:.2f}x" if pd.notna(row["VolumeRatio"]) else "n.d.")
        m5.metric("RS 20 sedute", pct(row["RelativeStrength20D"]))
        st.plotly_chart(make_price_chart(data.tail(300), f"{ticker} · ultime 300 sedute"), use_container_width=True)
        st.write(f"**Trend:** {signal['trend']} · **Ultima barra:** {data.index[-1]:%d/%m/%Y} · **Fonte:** {source}")
        st.markdown("**Composizione del punteggio (6 condizioni, un punto ciascuna)**")
        score_cols = st.columns(3)
        for i, item in enumerate(signal["checks"]):
            score_cols[i % 3].write(("✓ " if item["superato"] else "○ ") + item["criterio"])
        pattern_data = detect_chart_patterns(data)
        latest_patterns = pattern_data[pattern_data["Pattern"].astype(str).str.len() > 0].tail(12)
        if latest_patterns.empty:
            st.info("Nessun pattern automatico rilevato nelle ultime 100 sedute.")
        else:
            st.markdown("**Figure e segnali individuati**")
            for dt, item in latest_patterns.iterrows():
                st.write(f"{dt:%d/%m/%Y}: {item['Pattern']}")
        cols = ["Close", "EMA20", "SMA20", "SMA50", "SMA200", "PSAR", "Volume", "VolumeRatio", "Correlation60D", "RelativeStrength20D", "RelativeStrength60D", "Breakout20", "Breakdown20", "Doji", "Hammer", "ShootingStar", "BullishEngulfing", "BearishEngulfing"]
        st.dataframe(data[cols].tail(15).sort_index(ascending=False), use_container_width=True)
        render_indicator_guide()
    except Exception as exc:
        show_fetch_error(ticker, exc)

elif page == "Backtest":
    st.subheader("Backtest giornaliero")
    st.markdown("""
    Il backtest simula una strategia **long-only Parabolic SAR + SMA50** sul titolo selezionato. Non usa il punteggio scanner 0–6.

    **Regole:** entra quando, alla chiusura della seduta precedente, il prezzo è sopra PSAR e SMA50; esce quando è sotto PSAR oppure sotto SMA50. Il segnale viene applicato dalla seduta successiva per evitare di usare informazioni future. Non apre posizioni short.

    **Come usarlo:** scegli un titolo, imposta il costo percentuale stimato per ogni acquisto/vendita (0,10% significa 0,10% per ciascun cambio di posizione), quindi premi *Esegui backtest*. Confronta la curva strategia con Buy & Hold e valuta rendimento, drawdown, Sharpe, esposizione e numero di cambi di posizione.
    """)
    constituents = load_constituents()
    options = {f"{r.company} ({r.ticker})": r.ticker for r in constituents.itertuples()}
    selected_label = st.selectbox("Titolo da testare", list(options.keys()), key="backtest_ticker")
    ticker = options[selected_label]
    fee = st.number_input("Costo stimato per ogni acquisto/vendita (%)", min_value=0.0, max_value=1.0, value=0.10, step=0.01)
    if st.button("Esegui backtest", type="primary"):
        try:
            with st.spinner(f"Caricamento storico {ticker}…"):
                raw, source = get_history(ticker)
            result, stats = run_backtest(raw, fee_pct=fee)
            st.session_state["bt_result"] = result
            st.session_state["bt_stats"] = stats
            st.session_state["bt_source"] = source
            st.session_state["bt_ticker"] = ticker
        except Exception as exc:
            show_fetch_error(ticker, exc)
    if "bt_result" in st.session_state and st.session_state.get("bt_ticker") == ticker:
        stats = st.session_state["bt_stats"]
        a, b, c, d, e, f = st.columns(6)
        a.metric("Rendimento totale", pct(stats["Rendimento totale"]))
        b.metric("CAGR", pct(stats["CAGR"]))
        c.metric("Max drawdown", pct(stats["Max drawdown"]))
        d.metric("Sharpe", f"{stats['Sharpe']:.2f}" if pd.notna(stats["Sharpe"]) else "n.d.")
        e.metric("Esposizione", pct(stats["Esposizione %"]))
        f.metric("Operazioni", str(stats["Operazioni"]))
        result = st.session_state["bt_result"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=result.index, y=result["Equity strategia"], name="Strategia PSAR + SMA50"))
        fig.add_trace(go.Scatter(x=result.index, y=result["Equity buy&hold"], name="Buy & Hold"))
        fig.update_layout(height=450, title=f"Equity curve · {ticker}", margin={"l": 10, "r": 10, "t": 50, "b": 10})
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Fonte: {st.session_state['bt_source']}. Costi applicati a ogni variazione di posizione.")
        with st.expander("Come interpretare i risultati del backtest"):
            st.markdown("""
            - **Rendimento totale:** variazione complessiva del capitale simulato nel periodo.
            - **CAGR:** rendimento annualizzato composto; consente confronti tra periodi di durata diversa.
            - **Max drawdown:** peggior perdita percentuale dal precedente massimo della curva; più è negativo, più profonda è stata la perdita.
            - **Sharpe:** rendimento medio rapportato alla volatilità annualizzata (qui senza tasso privo di rischio). Più alto può indicare un miglior rapporto rendimento/volatilità, ma non basta da solo.
            - **Esposizione:** percentuale delle sedute in cui la strategia è investita.
            - **Operazioni:** numero di cambi di posizione (entrate e uscite), non numero di singole azioni/contratti.
            - **Equity curve:** capitale teorico nel tempo; confrontare la linea strategia con Buy & Hold.

            Il costo inserito è applicato a ogni entrata e uscita. Il modello è didattico: non include slippage, imposte, dividendi, corporate actions, spread, liquidità, né survivorship bias. I risultati passati non predicono quelli futuri.
            """)
        st.warning("Backtest didattico: non include slippage, fiscalità, dividendi, corporate actions, vincoli di liquidità o survivorship bias. Non è una previsione né una raccomandazione d'investimento.")

else:
    st.subheader("Metodo, fonte dati e diagnostica")
    st.markdown(
        """
        **Impostazioni dati**
        - Benchmark Yahoo Finance: `FTSEMIB.MI`.
        - Timeframe: giornaliero (`1d`), storico richiesto: 5 anni.
        - Cache Streamlit: 12 ore per ticker; cache CSV locale di emergenza nel runtime.
        - Download: `yfinance` senza parallelismo; in caso di errore, tentativo alternativo sul chart endpoint Yahoo tramite `curl_cffi`, senza passaggio crumb.
        - Gestione errori: attese progressive su errori temporanei; se Yahoo restituisce 429/401 lo scanner si ferma e non continua a bombardare il provider.

        **Indicatori**
        - Parabolic SAR (step 0,02; massimo 0,20).
        - SMA 20/50/200, EMA 20, volume relativo alla media a 20 sedute.
        - Correlazione rolling a 60 sedute con il FTSE MIB.
        - Forza relativa a 20 e 60 sedute rispetto al benchmark.
        - Breakout/breakdown a 20 sedute e pattern engulfing elementari.

        **Punteggio scanner (x/6)**
        Lo scanner mostra il punteggio nel formato x/6. Un punto per ciascuna condizione: close sopra SMA20; SMA20 sopra SMA50; SMA50 sopra SMA200; close sopra PSAR; forza relativa 20 sedute positiva; volume sopra 1,2 volte la media a 20 sedute. BUY da 5, WATCH da 3, altrimenti SELL/AVOID. I valori mancanti non assegnano punti.

        **Limiti**
        Yahoo Finance è una fonte non ufficiale per l'accesso automatizzato e può limitare o modificare gli endpoint. Il ritardo delle quotazioni non evita gli errori 429/401. Il filesystem di Streamlit Community Cloud non è storage persistente garantito: la cache CSV locale può andare persa al riavvio. Il backtest è semplificato e non va usato da solo per decisioni operative.
        """
    )
    render_indicator_guide()
    st.markdown(f"**Benchmark attualmente caricato:** `{BENCHMARK}` · **Ultima osservazione:** {benchmark_raw.index[-1]:%d/%m/%Y} · **Fonte:** {benchmark_source}")
    st.markdown("**Composizione paniere:** lista di partenza aggiornata al 17/11/2026. Verificare periodicamente le revisioni trimestrali FTSE Russell/Borsa Italiana.")
    st.markdown("**Riferimenti:** [Yahoo Finance – FTSE MIB](https://finance.yahoo.com/quote/FTSEMIB.MI/) · [Borsa Italiana – componenti FTSE MIB](https://www.borsaitaliana.it/borsa/azioni/ftse-mib/lista.html)")
