"""Adaptateur technique - structure de marche, moyennes mobiles, RSI, niveaux cles.

Source des prix : Yahoo Finance (`yfinance`), gratuit, sans cle API. Le forex
spot n'ayant pas de volume centralise (marche OTC), aucune donnee de volume
fiable n'est utilisee ici - voir README pour la piste "volume/VSA via futures".

Ceci est une confirmation technique LEGERE (structure + MM + RSI + niveaux),
pas un systeme de signaux d'entree automatique.
"""

import pandas as pd
import yfinance as yf

SWING_WINDOW = 5  # nb de bougies de part et d'autre pour valider un swing high/low
MA_FAST = 20
MA_SLOW = 50
RSI_PERIOD = 14
KEY_LEVEL_LOOKBACK = 60
KEY_LEVEL_PROXIMITY_PCT = 1.5  # en % : en-dessous, on considere le prix "proche" du niveau


def fetch_price_history(base: str, quote: str, period: str = "6mo") -> pd.DataFrame:
    """OHLC quotidien pour la paire BASE/QUOTE (ticker Yahoo Finance BASEQUOTE=X)."""
    ticker = f"{base}{quote}=X"
    df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[["Open", "High", "Low", "Close"]].reset_index()
    df.columns = ["date", "open", "high", "low", "close"]
    return df


def _find_swings(df: pd.DataFrame, window: int = SWING_WINDOW):
    span = window * 2 + 1
    is_swing_high = df["high"] == df["high"].rolling(span, center=True).max()
    is_swing_low = df["low"] == df["low"].rolling(span, center=True).min()
    swing_highs = df.loc[is_swing_high.fillna(False), ["date", "high"]]
    swing_lows = df.loc[is_swing_low.fillna(False), ["date", "low"]]
    return swing_highs, swing_lows


def _market_structure(df: pd.DataFrame) -> dict:
    swing_highs, swing_lows = _find_swings(df)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {"structure": "indeterminee", "structure_score": 0}

    last_high, prev_high = swing_highs["high"].iloc[-1], swing_highs["high"].iloc[-2]
    last_low, prev_low = swing_lows["low"].iloc[-1], swing_lows["low"].iloc[-2]

    higher_high = last_high > prev_high
    higher_low = last_low > prev_low
    lower_high = last_high < prev_high
    lower_low = last_low < prev_low

    if higher_high and higher_low:
        return {"structure": "haussiere (HH + HL)", "structure_score": 1}
    if lower_high and lower_low:
        return {"structure": "baissiere (LH + LL)", "structure_score": -1}
    return {"structure": "mixte / range", "structure_score": 0}


def _moving_averages(df: pd.DataFrame) -> dict:
    ma_fast = df["close"].rolling(MA_FAST).mean().iloc[-1]
    ma_slow = df["close"].rolling(MA_SLOW).mean().iloc[-1]
    price = df["close"].iloc[-1]

    if pd.isna(ma_fast) or pd.isna(ma_slow):
        return {"ma_fast": None, "ma_slow": None, "ma_alignment": "indisponible", "ma_score": 0}

    if price > ma_fast > ma_slow:
        alignment, score = "haussier (prix > MM20 > MM50)", 1
    elif price < ma_fast < ma_slow:
        alignment, score = "baissier (prix < MM20 < MM50)", -1
    else:
        alignment, score = "mixte", 0

    return {"ma_fast": round(float(ma_fast), 5), "ma_slow": round(float(ma_slow), 5), "ma_alignment": alignment, "ma_score": score}


def _rsi(df: pd.DataFrame, period: int = RSI_PERIOD) -> float | None:
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    value = rsi.iloc[-1]
    return None if pd.isna(value) else round(float(value), 1)


def _key_level_proximity(df: pd.DataFrame, lookback: int = KEY_LEVEL_LOOKBACK) -> dict:
    window = df.tail(lookback)
    high_n = window["high"].max()
    low_n = window["low"].min()
    price = df["close"].iloc[-1]

    dist_to_high_pct = (high_n - price) / price * 100
    dist_to_low_pct = (price - low_n) / price * 100

    near_high = dist_to_high_pct <= KEY_LEVEL_PROXIMITY_PCT
    near_low = dist_to_low_pct <= KEY_LEVEL_PROXIMITY_PCT

    note = None
    if near_high:
        note = f"proche du plus haut sur {lookback}j (a {dist_to_high_pct:.2f}%)"
    elif near_low:
        note = f"proche du plus bas sur {lookback}j (a {dist_to_low_pct:.2f}%)"

    return {
        "high_n": round(float(high_n), 5),
        "low_n": round(float(low_n), 5),
        "near_key_level": bool(near_high or near_low),
        "key_level_note": note,
    }


def build_technical_summary(base: str, quote: str, period: str = "6mo") -> dict:
    df = fetch_price_history(base, quote, period=period)
    if df.empty:
        return {"available": False}

    structure = _market_structure(df)
    ma = _moving_averages(df)
    rsi = _rsi(df)
    key_level = _key_level_proximity(df)

    combined_score = structure["structure_score"] + ma["ma_score"]
    if combined_score >= 2:
        bias = "aligne haussier fort"
    elif combined_score == 1:
        bias = "aligne haussier"
    elif combined_score == -1:
        bias = "aligne baissier"
    elif combined_score <= -2:
        bias = "aligne baissier fort"
    else:
        bias = "neutre / range"

    rsi_flag = None
    if rsi is not None:
        if rsi >= 70:
            rsi_flag = "surachat (RSI >= 70)"
        elif rsi <= 30:
            rsi_flag = "survente (RSI <= 30)"

    return {
        "available": True,
        "pair": f"{base}/{quote}",
        "last_price": round(float(df["close"].iloc[-1]), 5),
        "last_date": df["date"].iloc[-1].date().isoformat(),
        "structure": structure["structure"],
        "ma_alignment": ma["ma_alignment"],
        "ma_fast": ma["ma_fast"],
        "ma_slow": ma["ma_slow"],
        "rsi": rsi,
        "rsi_flag": rsi_flag,
        "technical_bias": bias,
        **key_level,
        "price_history": df,
    }
