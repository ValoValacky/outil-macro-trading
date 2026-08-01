"""Volume Spread Analysis (VSA) sur les futures CME de devises.

Le forex spot est un marche OTC sans volume centralise fiable (voir
`technical.py`). Les futures de devises du CME, eux, ont un vrai volume
publie. On les utilise comme PROXY du volume institutionnel - le prix des
futures suit tres etroitement le spot (arbitrage), donc la lecture reste
pertinente meme si tu trades le spot.

Limite assumee : la VSA est une methode de lecture fondamentalement
discretionnaire (Wyckoff / Tom Williams). Les regles ci-dessous formalisent
les patterns les plus objectivables (climax, no demand/supply, effort sans
resultat, spring, up-thrust) en criteres calculables, mais ca reste une
AIDE A LA LECTURE, pas un signal automatique fiable a 100%.
"""

import pandas as pd
import yfinance as yf

# Contrat CME correspondant a chaque devise (toujours cote contre USD -
# pas de future direct pour l'USD lui-meme, ni de proxy volume fiable trouve).
FUTURES_TICKER = {
    "EUR": "6E=F",
    "GBP": "6B=F",
    "JPY": "6J=F",
    "AUD": "6A=F",
    "NZD": "6N=F",
    "CAD": "6C=F",
    "CHF": "6S=F",
}

# Polarite du signal pour la devise elle-meme (le future est cote devise/USD) :
# sert au code couleur du dashboard.
FLAG_POLARITY = {
    "up-thrust (piege haussier)": "bearish",
    "spring (piege baissier)": "bullish",
    "climax haussier (essoufflement possible)": "bearish",
    "climax baissier (essoufflement possible)": "bullish",
    "no demand (faiblesse acheteuse)": "bearish",
    "no supply (faiblesse vendeuse)": "bullish",
    "effort sans resultat (absorption possible)": "neutral",
}

LOOKBACK_STATS = 20  # fenetre pour la moyenne volume/spread de reference
FLAG_WINDOW = 20  # nb de bougies recentes scannees pour detecter un pattern
BREAKOUT_WINDOW = 10  # nb de bougies pour verifier un nouveau plus haut/bas

HIGH_VOL, VERY_HIGH_VOL, LOW_VOL = 1.5, 2.0, 0.7
WIDE_SPREAD, NARROW_SPREAD = 1.3, 0.7


def fetch_futures_history(currency: str, period: str = "6mo") -> pd.DataFrame:
    if currency not in FUTURES_TICKER:
        return pd.DataFrame()

    df = yf.download(FUTURES_TICKER[currency], period=period, interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[["Open", "High", "Low", "Close", "Volume"]].reset_index()
    df.columns = ["date", "open", "high", "low", "close", "volume"]
    return df.dropna(subset=["volume"])


def _classify_bar(row, avg_volume: float, avg_spread: float, prior_high: float, prior_low: float, trend_up: bool, trend_down: bool) -> str | None:
    if avg_volume <= 0 or avg_spread <= 0:
        return None

    spread = row["high"] - row["low"]
    if spread <= 0:
        return None

    volume_ratio = row["volume"] / avg_volume
    spread_ratio = spread / avg_spread
    close_pos = (row["close"] - row["low"]) / spread
    up_bar = row["close"] > row["open"]

    if row["high"] > prior_high and close_pos < 0.3 and volume_ratio > HIGH_VOL:
        return "up-thrust (piege haussier)"
    if row["low"] < prior_low and close_pos > 0.7 and volume_ratio > HIGH_VOL:
        return "spring (piege baissier)"
    if up_bar and trend_up and volume_ratio > VERY_HIGH_VOL and spread_ratio > WIDE_SPREAD and close_pos < 0.5:
        return "climax haussier (essoufflement possible)"
    if not up_bar and trend_down and volume_ratio > VERY_HIGH_VOL and spread_ratio > WIDE_SPREAD and close_pos > 0.5:
        return "climax baissier (essoufflement possible)"
    if up_bar and volume_ratio < LOW_VOL and spread_ratio < NARROW_SPREAD:
        return "no demand (faiblesse acheteuse)"
    if not up_bar and volume_ratio < LOW_VOL and spread_ratio < NARROW_SPREAD:
        return "no supply (faiblesse vendeuse)"
    if volume_ratio > HIGH_VOL and spread_ratio < NARROW_SPREAD:
        return "effort sans resultat (absorption possible)"

    return None


def scan_vsa_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Renvoie les bougies recentes (fenetre FLAG_WINDOW) qui matchent un pattern VSA."""
    if len(df) < LOOKBACK_STATS + BREAKOUT_WINDOW + 1:
        return pd.DataFrame(columns=["date", "flag", "close"])

    rows = []
    start = max(LOOKBACK_STATS, len(df) - FLAG_WINDOW)
    for i in range(start, len(df)):
        window = df.iloc[i - LOOKBACK_STATS : i]
        avg_volume = window["volume"].mean()
        avg_spread = (window["high"] - window["low"]).mean()
        prior = df.iloc[max(0, i - BREAKOUT_WINDOW) : i]
        prior_high = prior["high"].max()
        prior_low = prior["low"].min()
        trend_up = df.iloc[i]["close"] > prior["close"].iloc[0] if not prior.empty else False
        trend_down = df.iloc[i]["close"] < prior["close"].iloc[0] if not prior.empty else False

        flag = _classify_bar(df.iloc[i], avg_volume, avg_spread, prior_high, prior_low, trend_up, trend_down)
        if flag:
            rows.append({"date": df.iloc[i]["date"], "flag": flag, "close": df.iloc[i]["close"]})

    return pd.DataFrame(rows)


def summarize_vsa(currency: str, period: str = "6mo") -> dict:
    df = fetch_futures_history(currency, period=period)
    if df.empty:
        return {"available": False}

    flags = scan_vsa_flags(df)
    latest_flag = None
    latest_flag_date = None
    if not flags.empty:
        latest_flag = flags.iloc[-1]["flag"]
        latest_flag_date = flags.iloc[-1]["date"].date().isoformat()

    return {
        "available": True,
        "currency": currency,
        "last_volume": int(df["volume"].iloc[-1]),
        "avg_volume_20": round(df["volume"].tail(LOOKBACK_STATS).mean(), 0),
        "latest_flag": latest_flag,
        "latest_flag_date": latest_flag_date,
        "recent_flags": flags,
        "price_history": df,
    }
