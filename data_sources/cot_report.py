"""Adaptateur CFTC - Commitment of Traders (positionnement des grands speculateurs).

Source : https://publicreporting.cftc.gov (Socrata Open Data API), dataset
"Legacy Futures Only" (6dca-aqww). Gratuit, sans cle API. Publie chaque
vendredi avec les positions arretees au mardi precedent.

On suit les positions "Non-Commercial" (grands speculateurs institutionnels :
hedge funds, CTA...) sur les futures de devises - c'est la lecture standard
utilisee par les analystes de flux ("positionnement net des specs").

Fetch autonome (pas d'import cross-projet vers HORIIZON) : Streamlit
Community Cloud ne deploie QUE ce depot GitHub (outil-macro-trading), le
dossier HORIIZON n'existe pas a cote sur la machine du cloud - un import
cross-dossier qui marche en local casse silencieusement en production
(ModuleNotFoundError constate le 2026-08-31). Les codes de contrats CFTC et
la logique de fetch sont donc dupliques ici plutot que partages.
"""

import requests
import pandas as pd

LEGACY_API_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# Codes de contrats CFTC (rapport Legacy "Futures Only"), identiques a ceux
# de horiizon/cot_strength.py et de l'indicateur MT5 COT_Strength_RSI.mq5.
CURRENCY_CODES: dict[str, str] = {
    "USD": "098662",
    "EUR": "099741",
    "GBP": "096742",
    "JPY": "097741",
    "CHF": "092741",
    "CAD": "090741",
    "AUD": "232741",
    "NZD": "112741",
}


def fetch_cot_history(currency: str, weeks_back: int = 26) -> pd.DataFrame:
    """Historique hebdomadaire du positionnement Non-Commercial pour une devise."""
    params = {
        "cftc_contract_market_code": CURRENCY_CODES[currency],
        "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,"
                   "noncomm_positions_short_all,open_interest_all",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(weeks_back + 10),
    }
    resp = requests.get(LEGACY_API_URL, params=params, timeout=15)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame({
        "date": [pd.Timestamp(row["report_date_as_yyyy_mm_dd"]) for row in rows],
        "noncomm_long": [float(row["noncomm_positions_long_all"]) for row in rows],
        "noncomm_short": [float(row["noncomm_positions_short_all"]) for row in rows],
        "open_interest": [float(row["open_interest_all"]) for row in rows],
    })
    df = df.sort_values("date").tail(weeks_back).reset_index(drop=True)
    df["net_position"] = df["noncomm_long"] - df["noncomm_short"]
    df["net_pct_oi"] = (df["net_position"] / df["open_interest"] * 100).round(2)
    df["currency"] = currency

    return df


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
