"""Core engine for the FTSE MIB technical-analysis dashboard.

Data source: Yahoo Finance chart endpoint, accessed first through yfinance and,
when necessary, through curl_cffi without the crumb-based quoteSummary flow.
This is an unofficial data interface and can be rate-limited by Yahoo.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("ftsemib.engine")
BENCHMARK = "FTSEMIB.MI"
DEFAULT_PERIOD = "5y"
DEFAULT_INTERVAL = "1d"
CACHE_DIR = Path(__file__).resolve().parent / "runtime_cache"


class YahooDataError(RuntimeError):
    """Raised when Yahoo Finance does not return usable OHLCV data."""


def load_constituents(csv_path: str | Path | None = None) -> pd.DataFrame:
    path = Path(csv_path) if csv_path else Path(__file__).resolve().parent / "ftsemib_constituents.csv"
    df = pd.read_csv(path)
    required = {"ticker", "company"}
    if not required.issubset(df.columns):
        raise ValueError(f"Il CSV deve contenere le colonne {sorted(required)}")
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["company"] = df["company"].astype(str).str.strip()
    df = df[df["ticker"].ne("") & df["company"].ne("")]
    return df.drop_duplicates("ticker").reset_index(drop=True)


def _normalise_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    # yfinance can return a MultiIndex even for a single ticker.
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [c[0] if isinstance(c, tuple) else c for c in out.columns]
    rename = {str(c).lower(): str(c).title() for c in out.columns}
    out = out.rename(columns=rename)
    price_cols = ["Open", "High", "Low", "Close"]
    if not set(price_cols).issubset(out.columns):
        return pd.DataFrame()
    if "Volume" not in out.columns:
        out["Volume"] = 0
    wanted = ["Open", "High", "Low", "Close", "Volume"]
    out = out[wanted].copy()
    for col in wanted:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["Close"])
    idx = pd.to_datetime(out.index, errors="coerce")
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("Europe/Rome").tz_localize(None)
    out.index = idx
    out = out[~out.index.isna()]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index.name = "Date"
    return out


def _fetch_with_yfinance(ticker: str, period: str, interval: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except Exception as exc:  # pragma: no cover - depends on deployment
        raise YahooDataError(f"yfinance non disponibile: {exc}") from exc

    try:
        # No parallel workers: a single controlled request path is less likely
        # to trigger Yahoo's anti-bot/rate-limit rules.
        raw = yf.download(
            tickers=ticker,
            period=period,
            interval=interval,
            auto_adjust=False,
            actions=False,
            progress=False,
            threads=False,
            group_by="column",
            timeout=25,
            multi_level_index=False,
        )
    except TypeError:
        # Compatibility fallback for yfinance releases with an older signature.
        raw = yf.download(
            tickers=ticker,
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=25,
        )
    except Exception as exc:
        raise YahooDataError(f"yfinance: {type(exc).__name__}: {exc}") from exc

    data = _normalise_ohlcv(raw)
    if data.empty:
        raise YahooDataError("yfinance ha restituito un dataframe vuoto")
    return data


def _curl_session():
    try:
        from curl_cffi import requests as curl_requests
        return curl_requests.Session(impersonate="chrome"), "curl_cffi"
    except Exception:
        # Fallback only if curl_cffi cannot be imported/initialised.
        import requests
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
        })
        return session, "requests-fallback"


def _period_bounds(period: str) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    years = {"1y": 1, "2y": 2, "5y": 5, "10y": 10, "max": 25}.get(period, 5)
    start = now - timedelta(days=years * 365 + 20)
    return int(start.timestamp()), int((now + timedelta(days=1)).timestamp())


def _fetch_chart_endpoint(ticker: str, period: str, interval: str) -> pd.DataFrame:
    """Direct chart-endpoint fallback; this endpoint normally does not need a crumb."""
    session, backend = _curl_session()
    p1, p2 = _period_bounds(period)
    params = {
        "period1": p1,
        "period2": p2,
        "interval": interval,
        "events": "div,splits",
        "includeAdjustedClose": "true",
    }
    errors: list[str] = []
    hosts = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
    try:
        for host in hosts:
            url = f"https://{host}/v8/finance/chart/{ticker}"
            for attempt, wait_seconds in enumerate((0, 10)):
                if wait_seconds:
                    time.sleep(wait_seconds)
                try:
                    response = session.get(url, params=params, timeout=25)
                    if response.status_code == 429:
                        errors.append(f"{host}: HTTP 429 (rate limit)")
                        # Do not hammer a host that has explicitly rate-limited us.
                        break
                    if response.status_code in (500, 502, 503, 504):
                        errors.append(f"{host}: HTTP {response.status_code}")
                        continue
                    if response.status_code in (401, 403):
                        errors.append(f"{host}: HTTP {response.status_code} (accesso negato)")
                        break
                    response.raise_for_status()
                    payload = response.json()
                    chart = payload.get("chart", {})
                    if chart.get("error"):
                        err = chart["error"]
                        errors.append(f"{host}: {err.get('code', 'Yahoo error')} - {err.get('description', '')}")
                        break
                    results = chart.get("result") or []
                    if not results:
                        errors.append(f"{host}: risposta senza result")
                        break
                    result = results[0]
                    timestamps = result.get("timestamp") or []
                    indicators = result.get("indicators", {})
                    quote_list = indicators.get("quote") or []
                    if not timestamps or not quote_list:
                        errors.append(f"{host}: risposta senza prezzi OHLCV")
                        break
                    quote = quote_list[0]
                    def values_or_fill(key, fill_value=np.nan):
                        values = quote.get(key)
                        if values is None or len(values) != len(timestamps):
                            return [fill_value] * len(timestamps)
                        return values

                    frame = pd.DataFrame({
                        "Open": values_or_fill("open"),
                        "High": values_or_fill("high"),
                        "Low": values_or_fill("low"),
                        "Close": values_or_fill("close"),
                        "Volume": values_or_fill("volume", 0),
                    }, index=pd.to_datetime(timestamps, unit="s", utc=True))
                    data = _normalise_ohlcv(frame)
                    if data.empty:
                        errors.append(f"{host}: serie OHLCV vuota dopo la normalizzazione")
                        break
                    LOGGER.info("Download %s riuscito tramite chart endpoint (%s)", ticker, backend)
                    return data
                except Exception as exc:
                    errors.append(f"{host}: {type(exc).__name__}: {exc}")
                    # Network/HTTP errors on this host: try the other host.
                    break
    finally:
        try:
            session.close()
        except Exception:
            pass
    raise YahooDataError("; ".join(errors[-6:]) or "Yahoo non ha restituito dati")


def fetch_history(ticker: str, period: str = DEFAULT_PERIOD, interval: str = DEFAULT_INTERVAL) -> pd.DataFrame:
    """Fetch daily OHLCV; use yfinance first, then direct chart endpoint fallback."""
    ticker = str(ticker).strip().upper()
    if not ticker:
        raise ValueError("Ticker vuoto")
    if interval != "1d":
        raise ValueError("Questa versione del progetto è configurata per dati giornalieri (1d)")
    yf_error: Optional[Exception] = None
    try:
        data = _fetch_with_yfinance(ticker, period, interval)
        LOGGER.info("Download %s riuscito tramite yfinance (%s righe)", ticker, len(data))
        return data
    except Exception as exc:
        yf_error = exc
        LOGGER.warning("yfinance non riuscito per %s: %s. Provo chart endpoint diretto.", ticker, exc)
    try:
        return _fetch_chart_endpoint(ticker, period, interval)
    except Exception as direct_exc:
        raise YahooDataError(
            f"{ticker}: yfinance: {yf_error}; chart endpoint: {direct_exc}"
        ) from direct_exc


def cache_file(ticker: str) -> Path:
    safe = "".join(ch for ch in ticker.upper() if ch.isalnum() or ch in "._-")
    return CACHE_DIR / f"{safe}.csv"


def save_history_local(ticker: str, data: pd.DataFrame) -> None:
    if data is None or data.empty:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_file(ticker)
    temp_path = path.with_suffix(".tmp")
    data.to_csv(temp_path, index_label="Date")
    temp_path.replace(path)


def load_history_local(ticker: str) -> pd.DataFrame:
    path = cache_file(ticker)
    if not path.exists():
        return pd.DataFrame()
    try:
        data = pd.read_csv(path, index_col="Date", parse_dates=True)
        return _normalise_ohlcv(data)
    except Exception as exc:
        LOGGER.warning("Cache locale non leggibile per %s: %s", ticker, exc)
        return pd.DataFrame()


def load_history_with_fallback(ticker: str, period: str = DEFAULT_PERIOD) -> tuple[pd.DataFrame, str]:
    try:
        data = fetch_history(ticker, period=period, interval="1d")
        save_history_local(ticker, data)
        return data, "Yahoo Finance"
    except Exception as exc:
        cached = load_history_local(ticker)
        if not cached.empty:
            return cached, f"Cache locale (Yahoo non disponibile: {exc})"
        raise


def parabolic_sar(df: pd.DataFrame, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
    high = df["High"].astype(float).to_numpy()
    low = df["Low"].astype(float).to_numpy()
    n = len(df)
    if n == 0:
        return pd.Series(index=df.index, dtype=float, name="PSAR")
    if n < 2:
        return pd.Series([np.nan] * n, index=df.index, name="PSAR")

    psar = np.zeros(n, dtype=float)
    bull = high[1] >= high[0]
    af = step
    ep = high[0] if bull else low[0]
    psar[0] = low[0] if bull else high[0]

    for i in range(1, n):
        prior = psar[i - 1]
        value = prior + af * (ep - prior)
        if bull:
            value = min(value, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < value:
                bull = False
                value = ep
                ep = low[i]
                af = step
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + step, max_step)
        else:
            value = max(value, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > value:
                bull = True
                value = ep
                ep = high[i]
                af = step
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + step, max_step)
        psar[i] = value
    return pd.Series(psar, index=df.index, name="PSAR")


def add_indicators(df: pd.DataFrame, benchmark: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    out = _normalise_ohlcv(df)
    if out.empty:
        return out
    close = out["Close"]
    out["SMA20"] = close.rolling(20, min_periods=20).mean()
    out["SMA50"] = close.rolling(50, min_periods=50).mean()
    out["SMA200"] = close.rolling(200, min_periods=200).mean()
    out["EMA20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    out["PSAR"] = parabolic_sar(out)
    out["VolumeAvg20"] = out["Volume"].rolling(20, min_periods=10).mean()
    out["VolumeRatio"] = out["Volume"] / out["VolumeAvg20"].replace(0, np.nan)
    out["Return1D"] = close.pct_change()
    out["Return20D"] = close.pct_change(20)
    out["Return60D"] = close.pct_change(60)
    out["High20"] = out["High"].rolling(20, min_periods=20).max().shift(1)
    out["Low20"] = out["Low"].rolling(20, min_periods=20).min().shift(1)
    out["Breakout20"] = close > out["High20"]
    out["Breakdown20"] = close < out["Low20"]

    # Candlestick patterns: heuristic OHLC definitions, not predictive signals.
    candle_range = (out["High"] - out["Low"]).replace(0, np.nan)
    body = (out["Close"] - out["Open"]).abs()
    upper_shadow = out["High"] - out[["Open", "Close"]].max(axis=1)
    lower_shadow = out[["Open", "Close"]].min(axis=1) - out["Low"]
    out["Doji"] = body <= candle_range * 0.10
    out["Hammer"] = (lower_shadow >= body * 2.0) & (upper_shadow <= body * 0.5) & ((out[["Open", "Close"]].min(axis=1) - out["Low"]) / candle_range >= 0.55) & (body > 0)
    out["ShootingStar"] = (upper_shadow >= body * 2.0) & (lower_shadow <= body * 0.5) & ((out["High"] - out[["Open", "Close"]].max(axis=1)) / candle_range >= 0.55) & (body > 0)
    prev_bull = out["Close"].shift(1) > out["Open"].shift(1)
    prev_bear = out["Close"].shift(1) < out["Open"].shift(1)
    out["BullishEngulfing"] = (out["Close"] > out["Open"]) & prev_bear & (out["Close"] >= out["Open"].shift(1)) & (out["Open"] <= out["Close"].shift(1))
    out["BearishEngulfing"] = (out["Close"] < out["Open"]) & prev_bull & (out["Close"] <= out["Open"].shift(1)) & (out["Open"] >= out["Close"].shift(1))
    # Backward-compatible names used in earlier releases.
    out["BullishCandle"] = out["BullishEngulfing"]
    out["BearishCandle"] = out["BearishEngulfing"]

    if benchmark is not None and not benchmark.empty:
        bdata = _normalise_ohlcv(benchmark)
        b = bdata["Close"].pct_change().rename("BenchmarkReturn")
        joined = pd.concat([out["Return1D"], b], axis=1, join="inner")
        out["Correlation60D"] = joined["Return1D"].rolling(60, min_periods=30).corr(joined["BenchmarkReturn"]).reindex(out.index)
        bclose = bdata["Close"]
        out["RelativeStrength20D"] = out["Return20D"] - bclose.pct_change(20).reindex(out.index)
        out["RelativeStrength60D"] = out["Return60D"] - bclose.pct_change(60).reindex(out.index)
    else:
        out["Correlation60D"] = np.nan
        out["RelativeStrength20D"] = np.nan
        out["RelativeStrength60D"] = np.nan
    return out


def detect_chart_patterns(data: pd.DataFrame, lookback: int = 100) -> pd.DataFrame:
    """Return dated heuristic chart-pattern labels for plotting; patterns are approximate."""
    out = data.copy()
    out["Pattern"] = ""
    if len(out) < 5:
        return out
    recent = out.tail(lookback)
    labels: dict[pd.Timestamp, list[str]] = {}
    def add_label(idx, label):
        labels.setdefault(idx, []).append(label)
    for idx, row in recent.iterrows():
        if bool(row.get("Doji", False)):
            add_label(idx, "Doji")
        if bool(row.get("Hammer", False)):
            add_label(idx, "Hammer")
        if bool(row.get("ShootingStar", False)):
            add_label(idx, "Shooting star")
        if bool(row.get("BullishEngulfing", False)):
            add_label(idx, "Bullish engulfing")
        if bool(row.get("BearishEngulfing", False)):
            add_label(idx, "Bearish engulfing")
        if bool(row.get("Breakout20", False)):
            add_label(idx, "Breakout 20g")
        if bool(row.get("Breakdown20", False)):
            add_label(idx, "Breakdown 20g")

    # Three-pivot head-and-shoulders / inverse pattern. A neckline break is required.
    highs = recent["High"].to_numpy()
    lows = recent["Low"].to_numpy()
    ix = list(recent.index)
    peak_idx = [i for i in range(1, len(recent)-1) if highs[i] > highs[i-1] and highs[i] >= highs[i+1]]
    trough_idx = [i for i in range(1, len(recent)-1) if lows[i] < lows[i-1] and lows[i] <= lows[i+1]]
    if len(peak_idx) >= 3:
        a, b, c = peak_idx[-3:]
        left, head, right = highs[a], highs[b], highs[c]
        shoulders_close = abs(left-right) / max((left+right)/2, 1e-9) <= 0.06
        if a < b < c and shoulders_close and head >= max(left, right) * 1.025:
            neck_left = float(lows[a:b].min())
            neck_right = float(lows[b:c].min())
            neckline_now = (neck_left + neck_right) / 2
            if out["Close"].iloc[-1] < neckline_now:
                add_label(out.index[-1], "Head & shoulders · neckline rotta")
            elif len(out) and out.index[-1] == ix[c]:
                add_label(ix[c], "Possibile testa e spalle")
    if len(trough_idx) >= 3:
        a, b, c = trough_idx[-3:]
        left, head, right = lows[a], lows[b], lows[c]
        shoulders_close = abs(left-right) / max((left+right)/2, 1e-9) <= 0.06
        if a < b < c and shoulders_close and head <= min(left, right) * 0.975:
            neckline_now = (float(highs[a:b].max()) + float(highs[b:c].max())) / 2
            if out["Close"].iloc[-1] > neckline_now:
                add_label(out.index[-1], "Testa e spalle inverso · neckline rotta")
            elif out.index[-1] == ix[c]:
                add_label(ix[c], "Possibile testa e spalle inverso")
    for idx, values in labels.items():
        out.loc[idx, "Pattern"] = " · ".join(values)
    return out

def score_latest(data: pd.DataFrame) -> dict:
    if data is None or data.empty:
        return {"score": 0, "signal": "N/D", "trend": "N/D", "checks": []}
    row = data.iloc[-1]
    checks = [
        ("Prezzo > SMA20", row.get("Close", np.nan) > row.get("SMA20", np.nan)),
        ("SMA20 > SMA50", row.get("SMA20", np.nan) > row.get("SMA50", np.nan)),
        ("SMA50 > SMA200", row.get("SMA50", np.nan) > row.get("SMA200", np.nan)),
        ("Prezzo > Parabolic SAR", row.get("Close", np.nan) > row.get("PSAR", np.nan)),
        ("Forza relativa 20g > 0", row.get("RelativeStrength20D", np.nan) > 0),
        ("Volume > 1,2× media 20g", row.get("VolumeRatio", np.nan) > 1.2),
    ]
    passed = []
    for label, condition in checks:
        try:
            passed.append(bool(condition))
        except (TypeError, ValueError):
            passed.append(False)
    score = sum(passed)
    trend = "Rialzista" if score >= 4 else "Neutrale" if score >= 2 else "Ribassista"
    signal = "BUY" if score >= 5 else "WATCH" if score >= 3 else "SELL/AVOID"
    return {"score": score, "signal": signal, "trend": trend,
            "checks": [{"criterio": label, "superato": ok} for (label, _), ok in zip(checks, passed)]}

def scanner_row(ticker: str, company: str, raw: pd.DataFrame, benchmark: pd.DataFrame) -> dict:
    data = add_indicators(raw, benchmark)
    if data.empty:
        return {"Ticker": ticker, "Società": company, "Stato": "Dati mancanti"}
    row = data.iloc[-1]
    sig = score_latest(data)
    prev_close = data["Close"].iloc[-2] if len(data) > 1 else np.nan
    change = (row["Close"] / prev_close - 1) * 100 if pd.notna(prev_close) and prev_close else np.nan
    return {
        "Ticker": ticker,
        "Società": company,
        "Prezzo": row["Close"],
        "Var %": change,
        "Score": f"{sig['score']}/6",
        "Score ordinamento": sig["score"],
        "Segnale": sig["signal"],
        "Trend": sig["trend"],
        "PSAR": row.get("PSAR", np.nan),
        "Vol/Media20": row.get("VolumeRatio", np.nan),
        "RS 20g %": row.get("RelativeStrength20D", np.nan) * 100,
        "Corr. 60g": row.get("Correlation60D", np.nan),
        "Data": data.index[-1].strftime("%d/%m/%Y"),
    }


def run_backtest(raw: pd.DataFrame, fee_pct: float = 0.10) -> tuple[pd.DataFrame, dict]:
    """Daily long-only PSAR + SMA50 strategy; signal applied on next bar."""
    data = add_indicators(raw)
    if len(data) < 220:
        raise ValueError("Servono almeno 220 sedute per il backtest")
    close = data["Close"]
    entry_signal = (close > data["PSAR"]) & (close > data["SMA50"])
    exit_signal = (close < data["PSAR"]) | (close < data["SMA50"])
    position = pd.Series(0.0, index=data.index)
    in_market = False
    for i in range(1, len(data)):
        if not in_market and bool(entry_signal.iloc[i - 1]):
            in_market = True
        elif in_market and bool(exit_signal.iloc[i - 1]):
            in_market = False
        position.iloc[i] = 1.0 if in_market else 0.0
    # Apply cost on each change of exposure; return uses previous close to current close.
    asset_ret = close.pct_change().fillna(0.0)
    turnover = position.diff().abs().fillna(position.abs())
    costs = turnover * (fee_pct / 100.0)
    strategy_ret = position * asset_ret - costs
    equity = (1.0 + strategy_ret).cumprod()
    buy_hold = (1.0 + asset_ret).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    total_return = equity.iloc[-1] - 1.0
    years = max((data.index[-1] - data.index[0]).days / 365.25, 0.1)
    cagr = equity.iloc[-1] ** (1 / years) - 1 if equity.iloc[-1] > 0 else -1
    vol = strategy_ret.std() * np.sqrt(252)
    sharpe = (strategy_ret.mean() * 252) / vol if vol and np.isfinite(vol) else np.nan
    result = pd.DataFrame({
        "Prezzo": close,
        "Posizione": position,
        "Rendimento strategia": strategy_ret,
        "Equity strategia": equity,
        "Equity buy&hold": buy_hold,
        "Drawdown": drawdown,
    })
    stats = {
        "Rendimento totale": total_return,
        "CAGR": cagr,
        "Max drawdown": drawdown.min(),
        "Sharpe": sharpe,
        "Esposizione %": position.mean(),
        "Operazioni": int((turnover > 0).sum()),
    }
    return result, stats


def _add_atr(data: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    out = data.copy()
    prev_close = out["Close"].shift(1)
    tr = pd.concat([
        out["High"] - out["Low"],
        (out["High"] - prev_close).abs(),
        (out["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["ATR14"] = tr.rolling(period, min_periods=period).mean()
    out["ATR_pct"] = out["ATR14"] / out["Close"]
    out["High55"] = out["High"].rolling(55, min_periods=55).max().shift(1)
    out["EMA50"] = out["Close"].ewm(span=50, adjust=False, min_periods=50).mean()
    out["SMA200_slope"] = out["SMA200"] / out["SMA200"].shift(20) - 1
    return out


def single_stock_signal(raw: pd.DataFrame, benchmark: Optional[pd.DataFrame] = None) -> dict:
    """Medium-term single-stock setup: market regime, stock trend, RS and entry trigger."""
    data = _add_atr(add_indicators(raw, benchmark))
    if data.empty or len(data) < 220:
        return {"signal": "DATI INSUFFICIENTI", "checks": [], "entry": "Nessuno", "stop": np.nan}
    row = data.iloc[-1]
    market_ok = True
    market_text = "Non disponibile"
    if benchmark is not None and not benchmark.empty:
        bm = _add_atr(add_indicators(benchmark)).dropna(subset=["SMA200"])
        if not bm.empty:
            br = bm.iloc[-1]
            market_ok = bool(br["Close"] > br["SMA200"] and br["SMA200"] > bm["SMA200"].iloc[-21]) if len(bm) > 21 else bool(br["Close"] > br["SMA200"])
            market_text = "Rialzista" if market_ok else "Debole / ribassista"
    trend_ok = bool(row["Close"] > row["SMA200"] and row["SMA50"] > row["SMA200"] and row["SMA200_slope"] > 0)
    momentum_ok = bool(row.get("RelativeStrength60D", np.nan) > 0) if pd.notna(row.get("RelativeStrength60D", np.nan)) else False
    breakout = bool(row["Close"] > row["High55"]) if pd.notna(row["High55"]) else False
    recent_touch = bool((data["Low"].tail(6) <= data["EMA20"].tail(6) * 1.01).any())
    pullback_recovery = bool(recent_touch and row["Close"] > row["EMA20"] and row["Close"] > row["Open"])
    entry = "Breakout 55 sedute" if breakout else "Ritracciamento + recupero EMA20" if pullback_recovery else "Nessun trigger"
    ready = market_ok and trend_ok and momentum_ok and (breakout or pullback_recovery)
    if ready:
        signal = "COMPRA"
    elif not market_ok or not trend_ok:
        signal = "ATTENDI"
    elif not momentum_ok:
        signal = "ATTENDI"
    else:
        signal = "ATTENDI"
    stop = row["Close"] - 3.0 * row["ATR14"] if pd.notna(row["ATR14"]) else np.nan
    checks = [
        {"criterio": "FTSE MIB sopra SMA200 crescente", "superato": market_ok, "dettaglio": market_text},
        {"criterio": "Titolo sopra SMA200 e SMA50 > SMA200", "superato": trend_ok, "dettaglio": "Trend di lungo periodo"},
        {"criterio": "Forza relativa 60 sedute positiva", "superato": momentum_ok, "dettaglio": "Titolo meglio del FTSE MIB"},
        {"criterio": "Trigger di ingresso", "superato": breakout or pullback_recovery, "dettaglio": entry},
    ]
    return {"signal": signal, "checks": checks, "entry": entry, "stop": stop,
            "atr": row.get("ATR14", np.nan), "market": market_text, "data": data}


def run_momentum_backtest(raw: pd.DataFrame, benchmark: Optional[pd.DataFrame] = None,
                          fee_pct: float = 0.10, atr_multiple: float = 3.0) -> tuple[pd.DataFrame, dict]:
    """Long-only single-stock trend/momentum strategy, daily close signals and ATR stop."""
    data = _add_atr(add_indicators(raw, benchmark))
    if len(data) < 260:
        raise ValueError("Servono almeno 260 sedute per il backtest Trend + Momentum + ATR")
    if benchmark is not None and not benchmark.empty:
        bm = _add_atr(add_indicators(benchmark))
        market_filter = (bm["Close"] > bm["SMA200"]) & (bm["SMA200"] > bm["SMA200"].shift(20))
        data["MarketOK"] = market_filter.reindex(data.index).ffill().fillna(False)
    else:
        data["MarketOK"] = True
    trend = (data["Close"] > data["SMA200"]) & (data["SMA50"] > data["SMA200"]) & (data["SMA200"] > data["SMA200"].shift(20))
    relative = data["RelativeStrength60D"] > 0 if benchmark is not None and not benchmark.empty else pd.Series(True, index=data.index)
    breakout = data["Close"] > data["High55"]
    touch = (data["Low"].rolling(6, min_periods=1).min() <= data["EMA20"] * 1.01)
    pullback = touch & (data["Close"] > data["EMA20"]) & (data["Close"] > data["Open"])
    entry_signal = data["MarketOK"] & trend & relative & (breakout | pullback)
    exit_signal = (data["Close"] < data["SMA50"]) | (~data["MarketOK"])

    n = len(data)
    pos = np.zeros(n, dtype=float)
    strat_ret = np.zeros(n, dtype=float)
    stop_line = np.full(n, np.nan)
    in_pos = False
    highest = np.nan
    stop = np.nan
    fee = fee_pct / 100.0
    for i in range(1, n):
        prev_close = float(data["Close"].iloc[i-1])
        opened_today = False
        if not in_pos and bool(entry_signal.iloc[i-1]):
            in_pos = True
            opened_today = True
            highest = prev_close
            atr_prev = data["ATR14"].iloc[i-1]
            stop = prev_close - atr_multiple * atr_prev if pd.notna(atr_prev) else np.nan
            strat_ret[i] -= fee
        if in_pos:
            low = float(data["Low"].iloc[i])
            opn = float(data["Open"].iloc[i])
            close = float(data["Close"].iloc[i])
            exit_price = None
            if pd.notna(stop) and low <= stop:
                exit_price = min(opn, stop) if opn < stop else stop
            elif bool(exit_signal.iloc[i-1]) and not opened_today:
                # Previous close generated the exit; execute at today's open.
                exit_price = opn
            if exit_price is not None:
                strat_ret[i] += exit_price / prev_close - 1.0
                strat_ret[i] -= fee
                in_pos = False
                highest = np.nan
                stop = np.nan
            else:
                strat_ret[i] += close / prev_close - 1.0
                highest = max(highest, float(data["High"].iloc[i]))
                atr_now = data["ATR14"].iloc[i]
                if pd.notna(atr_now):
                    candidate = highest - atr_multiple * atr_now
                    stop = max(stop, candidate) if pd.notna(stop) else candidate
                pos[i] = 1.0
                stop_line[i] = stop
    returns = pd.Series(strat_ret, index=data.index)
    bh_ret = data["Close"].pct_change().fillna(0.0)
    equity = (1 + returns).cumprod()
    bh = (1 + bh_ret).cumprod()
    dd = equity / equity.cummax() - 1
    years = max((data.index[-1] - data.index[0]).days / 365.25, 0.1)
    vol = returns.std() * np.sqrt(252)
    stats = {
        "Rendimento totale": equity.iloc[-1] - 1,
        "CAGR": equity.iloc[-1] ** (1 / years) - 1 if equity.iloc[-1] > 0 else -1,
        "Max drawdown": dd.min(),
        "Sharpe": returns.mean() * 252 / vol if vol and np.isfinite(vol) else np.nan,
        "Esposizione %": float(np.mean(pos)),
        "Operazioni": int(np.sum(np.abs(np.diff(np.r_[0, pos])) > 0)),
    }
    result = pd.DataFrame({"Prezzo": data["Close"], "Posizione": pos, "Stop ATR": stop_line,
                           "Equity strategia": equity, "Equity buy&hold": bh, "Drawdown": dd,
                           "Segnale ingresso": entry_signal, "Segnale uscita": exit_signal}, index=data.index)
    return result, stats
