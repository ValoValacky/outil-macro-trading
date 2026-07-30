"""Adaptateur CFTC - Commitment of Traders (positionnement des grands speculateurs).

Source : https://publicreporting.cftc.gov (Socrata Open Data API), dataset
"Legacy Futures Only" (6dca-aqww). Gratuit, sans cle API. Publie chaque
vendredi avec les positions arretees au mardi precedent.

On suit les positions "Non-Commercial" (grands speculateurs institutionnels :
hedge funds, CTA...) sur les futures de devises - c'est la lecture standard
utilisee par les analystes de flux ("positionnement net des specs").
"""

from datetime import date, timedelta

import pandas as pd

from .common import http_get

COT_BASE = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# Nom exact du contrat cote CFTC pour chaque devise. Le dollar n'a pas de
# future "USD" direct dans ce dataset : le USD Index (DXY) sert de proxy
# (positionnement sur le dollar contre un panier de devises).
COT_CONTRACT_NAME = {
    "USD": "USD INDEX",
    "EUR": "EURO FX",
    "GBP": "BRITISH POUND",
    "JPY": "JAPANESE YEN",
    "AUD": "AUSTRALIAN DOLLAR",
    "NZD": "NZ DOLLAR",
    "CAD": "CANADIAN DOLLAR",
    "CHF": "SWISS FRANC",
}


def fetch_cot_history(currency: str, weeks_back: int = 26) -> pd.DataFrame:
    """Historique hebdomadaire du positionnement Non-Commercial pour une devise."""
    contract = COT_CONTRACT_NAME[currency]
    start_date = (date.today() - timedelta(weeks=weeks_back + 2)).isoformat()

    params = {
        "$limit": weeks_back + 10,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$where": (
            f"contract_market_name='{contract}' "
            f"AND report_date_as_yyyy_mm_dd >= '{start_date}'"
        ),
    }
    resp = http_get(COT_BASE, params=params)
    rows = resp.json()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)[
        ["report_date_as_yyyy_mm_dd", "noncomm_positions_long_all", "noncomm_positions_short_all", "open_interest_all"]
    ].rename(
        columns={
            "report_date_as_yyyy_mm_dd": "date",
            "noncomm_positions_long_all": "noncomm_long",
            "noncomm_positions_short_all": "noncomm_short",
            "open_interest_all": "open_interest",
        }
    )
    df["date"] = pd.to_datetime(df["date"])
    for col in ["noncomm_long", "noncomm_short", "open_interest"]:
        df[col] = df[col].astype(float)

    df["net_position"] = df["noncomm_long"] - df["noncomm_short"]
    df["net_pct_oi"] = (df["net_position"] / df["open_interest"] * 100).round(2)
    df["currency"] = currency

    return df.sort_values("date").reset_index(drop=True)


def summarize_cot_momentum(history: pd.DataFrame) -> dict:
    """Resume la dynamique du positionnement : niveau actuel + tendance 4/12 semaines
    + detection d'un franchissement du zero (retournement net long <-> net short)."""
    if history.empty or len(history) < 2:
        return {
            "level_pct_oi": None,
            "change_4w": None,
            "change_12w": None,
            "crossed_zero": False,
            "cross_direction": None,
            "as_of": None,
        }

    latest = history.iloc[-1]
    level = latest["net_pct_oi"]

    def _past_value(weeks: int) -> float:
        idx = max(0, len(history) - 1 - weeks)
        return history.iloc[idx]["net_pct_oi"]

    change_4w = round(level - _past_value(4), 2)
    change_12w = round(level - _past_value(12), 2)

    # Franchissement du zero : le signe du net a-t-il change sur la fenetre recente (12 sem) ?
    window = history.tail(13)["net_pct_oi"]
    crossed_zero = bool((window.iloc[0] < 0 < window.iloc[-1]) or (window.iloc[0] > 0 > window.iloc[-1]))
    cross_direction = None
    if crossed_zero:
        cross_direction = "bearish_to_bullish" if window.iloc[0] < 0 else "bullish_to_bearish"

    return {
        "level_pct_oi": float(level),
        "change_4w": change_4w,
        "change_12w": change_12w,
        "crossed_zero": crossed_zero,
        "cross_direction": cross_direction,
        "as_of": latest["date"].date().isoformat(),
    }


def classify_momentum(summary: dict) -> str:
    """Etiquette qualitative de la dynamique, pour affichage (4 cadrans classiques
    de lecture COT : le niveau seul ne suffit pas, c'est croise avec la tendance)."""
    if summary.get("level_pct_oi") is None:
        return "indisponible"

    if summary.get("crossed_zero"):
        return "retournement haussier" if summary.get("cross_direction") == "bearish_to_bullish" else "retournement baissier"

    level = summary["level_pct_oi"]
    trend = summary.get("change_4w") or 0

    if level >= 0:
        return "renforcement haussier" if trend > 0 else "affaiblissement haussier"
    return "affaiblissement baissier" if trend > 0 else "renforcement baissier"
