from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

BASE = Path(__file__).resolve().parent
BENCHMARK = "^FTMIB"

def load_universe():
    return pd.read_csv(BASE / "ftsemib_constituents.csv")

def _normalise_ohlcv(df):
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance may return ticker as outer or inner level
        df.columns = df.columns.get_level_values(0)
    needed = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in df.columns for c in needed):
        return pd.DataFrame()
    out = df[needed].copy()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for c in needed:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.dropna(subset=["Open","High","Low","Close"])

def download_market_data(symbols, period="5y"):
    tickers = list(dict.fromkeys(symbols + [BENCHMARK]))
    raw = yf.download(tickers=tickers, period=period, interval="1d",
                      auto_adjust=True, group_by="ticker", progress=False,
                      threads=True)
    result = {}
    if raw is None or raw.empty:
        return result
    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = raw.columns.get_level_values(0)
        for ticker in tickers:
            if ticker in lvl0:
                result[ticker] = _normalise_ohlcv(raw[ticker].copy())
            else:
                # alternate yfinance column ordering
                try:
                    result[ticker] = _normalise_ohlcv(raw.xs(ticker, axis=1, level=1).copy())
                except Exception:
                    pass
    else:
        if len(tickers) == 1:
            result[tickers[0]] = _normalise_ohlcv(raw)
    return {k:v for k,v in result.items() if not v.empty}

def parabolic_sar(high, low, step=0.02, max_step=0.20):
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)
    n = len(h)
    out = np.full(n, np.nan)
    if n < 2:
        return pd.Series(out, index=high.index)
    bull = True if (h[1] + l[1]) >= (h[0] + l[0]) else False
    af = step
    ep = h[0] if bull else l[0]
    sar = l[0] if bull else h[0]
    out[0] = sar
    for i in range(1, n):
        prev_sar = sar
        sar = prev_sar + af * (ep - prev_sar)
        if bull:
            sar = min(sar, l[i-1], l[i-2] if i >= 2 else l[i-1])
            if l[i] < sar:
                bull = False
                sar = ep
                ep = l[i]
                af = step
            elif h[i] > ep:
                ep = h[i]
                af = min(af + step, max_step)
        else:
            sar = max(sar, h[i-1], h[i-2] if i >= 2 else h[i-1])
            if h[i] > sar:
                bull = True
                sar = ep
                ep = h[i]
                af = step
            elif l[i] < ep:
                ep = l[i]
                af = min(af + step, max_step)
        out[i] = sar
    return pd.Series(out, index=high.index, name="PSAR")

def _pivot_points(series, order=4, kind="high"):
    vals = series.to_numpy(dtype=float)
    idx = []
    for i in range(order, len(vals)-order):
        window = vals[i-order:i+order+1]
        if kind == "high" and vals[i] == np.nanmax(window):
            idx.append(i)
        if kind == "low" and vals[i] == np.nanmin(window):
            idx.append(i)
    return idx

def detect_patterns(df):
    """Euristiche su pivot; restituisce pattern recenti, non pattern vision-based."""
    if len(df) < 60:
        return []
    x = df.tail(180)
    hi = _pivot_points(x["High"], 4, "high")
    lo = _pivot_points(x["Low"], 4, "low")
    found = []
    tol = 0.035

    if len(hi) >= 3:
        a,b,c = hi[-3:]
        vals = x["High"].iloc[[a,b,c]].to_numpy()
        if abs(vals[0]-vals[2])/max(np.mean(vals[[0,2]]),1e-9) < tol and vals[1] > max(vals[0],vals[2])*1.025:
            found.append("Testa e spalle")
        if abs(vals[0]-vals[1])/max(np.mean(vals[:2]),1e-9) < tol and abs(vals[1]-vals[2])/max(np.mean(vals[1:]),1e-9) < tol:
            found.append("Doppio/triplo massimo")
    if len(lo) >= 3:
        a,b,c = lo[-3:]
        vals = x["Low"].iloc[[a,b,c]].to_numpy()
        if abs(vals[0]-vals[2])/max(np.mean(vals[[0,2]]),1e-9) < tol and vals[1] < min(vals[0],vals[2])*0.975:
            found.append("Testa e spalle rovesciato")
        if abs(vals[0]-vals[1])/max(np.mean(vals[:2]),1e-9) < tol and abs(vals[1]-vals[2])/max(np.mean(vals[1:]),1e-9) < tol:
            found.append("Doppio/triplo minimo")

    # Triangoli: compressione delle oscillazioni tra pivot recenti
    if len(hi) >= 3 and len(lo) >= 3:
        hs = x["High"].iloc[hi[-3:]].to_numpy()
        ls = x["Low"].iloc[lo[-3:]].to_numpy()
        if hs[-1] < hs[0] and ls[-1] > ls[0]:
            found.append("Triangolo simmetrico")
        elif abs(hs[-1]-hs[0])/max(hs[0],1e-9) < tol and ls[-1] > ls[0]:
            found.append("Triangolo ascendente")
        elif abs(ls[-1]-ls[0])/max(ls[0],1e-9) < tol and hs[-1] < hs[0]:
            found.append("Triangolo discendente")
    return list(dict.fromkeys(found))

def analyze_symbol(df, benchmark, corr_window=60, vol_window=20):
    d = _normalise_ohlcv(df).copy()
    b = _normalise_ohlcv(benchmark).copy()
    d["SMA20"] = d["Close"].rolling(20).mean()
    d["SMA50"] = d["Close"].rolling(50).mean()
    d["SMA200"] = d["Close"].rolling(200).mean()
    d["EMA20"] = d["Close"].ewm(span=20, adjust=False).mean()
    d["PSAR"] = parabolic_sar(d["High"], d["Low"])
    d["VolumeSMA"] = d["Volume"].rolling(vol_window).mean()
    d["volume_ratio"] = d["Volume"] / d["VolumeSMA"].replace(0, np.nan)

    stock_ret = d["Close"].pct_change()
    bench_ret = b["Close"].pct_change().reindex(d.index)
    d["correlation"] = stock_ret.rolling(corr_window).corr(bench_ret)
    d["return_20d"] = d["Close"].pct_change(20)
    bench_20 = b["Close"].pct_change(20).reindex(d.index)
    d["relative_strength_20d"] = d["return_20d"] - bench_20
    d["benchmark_close"] = b["Close"].reindex(d.index).ffill()
    d["benchmark_SMA50"] = d["benchmark_close"].rolling(50).mean()

    d["psar_bull"] = d["Close"] > d["PSAR"]
    conditions = pd.DataFrame(index=d.index)
    conditions["price_sma20"] = d["Close"] > d["SMA20"]
    conditions["sma20_sma50"] = d["SMA20"] > d["SMA50"]
    conditions["sma50_sma200"] = d["SMA50"] > d["SMA200"]
    conditions["psar"] = d["psar_bull"]
    conditions["volume"] = d["volume_ratio"] >= 1.20
    conditions["relative"] = d["relative_strength_20d"] > 0
    conditions["benchmark"] = d["benchmark_close"] > d["benchmark_SMA50"]
    patterns = detect_patterns(d)
    bullish_pattern = any(p in patterns for p in ["Doppio/triplo minimo","Testa e spalle rovesciato","Triangolo ascendente"])
    conditions["pattern"] = bullish_pattern
    d["score"] = conditions.fillna(False).sum(axis=1).astype(int)
    d["signal"] = np.where(d["score"] >= 6, "BUY",
                    np.where((d["score"] <= 2) & (~d["psar_bull"]), "SELL", "NEUTRAL"))
    d["patterns"] = ", ".join(patterns) if patterns else ""
    return d

def build_scan(data, symbols, benchmark, corr_window, vol_window):
    uni = load_universe().set_index("ticker")
    rows = []
    bench = _normalise_ohlcv(benchmark)
    bench_sma50 = bench["Close"].rolling(50).mean()
    for sym in symbols:
        try:
            d = analyze_symbol(data[sym], benchmark, corr_window, vol_window)
            if d.empty:
                continue
            z = d.iloc[-1]
            prev = d["Close"].iloc[-2] if len(d) > 1 else np.nan
            rows.append({
                "name": uni.loc[sym, "name"] if sym in uni.index else sym,
                "ticker": sym, "date": d.index[-1].date(),
                "close": float(z["Close"]),
                "daily_change_pct": (float(z["Close"])/float(prev)-1)*100 if pd.notna(prev) and prev else np.nan,
                "signal": z["signal"], "score": int(z["score"]),
                "psar_state": "Rialzista" if z["psar_bull"] else "Ribassista",
                "ma_state": "Bullish" if z["SMA20"] > z["SMA50"] else "Bearish",
                "volume_ratio": float(z["volume_ratio"]) if pd.notna(z["volume_ratio"]) else np.nan,
                "correlation": float(z["correlation"]) if pd.notna(z["correlation"]) else np.nan,
                "relative_strength_20d": float(z["relative_strength_20d"]) if pd.notna(z["relative_strength_20d"]) else np.nan,
                "patterns": z["patterns"]
            })
        except Exception:
            continue
    return pd.DataFrame(rows)

def pattern_summary(df):
    return detect_patterns(df)

def make_backtest(df, benchmark, corr_window, vol_window, min_score=4):
    d = analyze_symbol(df, benchmark, corr_window, vol_window).copy()
    # Conservative: score >= 6 enters; score <=2 and bearish PSAR exits.
    entry = d["score"] >= 6
    exit_ = (d["score"] <= 2) & (~d["psar_bull"])
    pos = pd.Series(index=d.index, dtype=float)
    state = 0
    for i, idx in enumerate(d.index):
        if entry.loc[idx]:
            state = 1
        elif exit_.loc[idx]:
            state = 0
        pos.loc[idx] = state
    d["position"] = pos.shift(1).fillna(0)  # next-session application
    d["asset_return"] = d["Close"].pct_change().fillna(0)
    d["strategy_return"] = d["position"] * d["asset_return"]
    d["buy_hold_return"] = d["asset_return"]
    d["equity"] = (1 + d["strategy_return"]).cumprod()
    d["buy_hold_equity"] = (1 + d["buy_hold_return"]).cumprod()
    return d
