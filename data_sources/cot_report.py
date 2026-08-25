"""Adaptateur CFTC - Commitment of Traders (positionnement des grands speculateurs).

Source : https://publicreporting.cftc.gov (Socrata Open Data API), dataset
"Legacy Futures Only" (6dca-aqww). Gratuit, sans cle API. Publie chaque
vendredi avec les positions arretees au mardi precedent.

On suit les positions "Non-Commercial" (grands speculateurs institutionnels :
hedge funds, CTA...) sur les futures de devises - c'est la lecture standard
utilisee par les analystes de flux ("positionnement net des specs").

Le fetch reutilise horiizon.cot_index_lw (projet HORIIZON, meme dataset CFTC
Legacy, meme categorie Non-Commercial) au lieu de requeter le CFTC en double -
un seul point de verite pour ce calcul, deja verifie independamment. 2026-08-25.
"""

import sys
from pathlib import Path

import pandas as pd

_HORIIZON_DIR = Path(__file__).resolve().parents[2] / "HORIIZON"
if str(_HORIIZON_DIR) not in sys.path:
    sys.path.insert(0, str(_HORIIZON_DIR))

from horiizon.cot_index_lw import fetch_legacy_history  # noqa: E402
from horiizon.cot_strength import CURRENCY_CODES  # noqa: E402


def fetch_cot_history(currency: str, weeks_back: int = 26) -> pd.DataFrame:
    """Historique hebdomadaire du positionnement Non-Commercial pour une devise."""
    weeks = fetch_legacy_history(CURRENCY_CODES[currency], history_weeks=weeks_back + 10)
    if not weeks:
        return pd.DataFrame()

    weeks = sorted(weeks, key=lambda w: w.report_date)[-weeks_back:]

    df = pd.DataFrame({
        "date": [pd.Timestamp(w.report_date) for w in weeks],
        "noncomm_long": [w.noncomm_long for w in weeks],
        "noncomm_short": [w.noncomm_short for w in weeks],
        "open_interest": [w.open_interest for w in weeks],
    })
    df["net_position"] = df["noncomm_long"] - df["noncomm_short"]
    df["net_pct_oi"] = (df["net_position"] / df["open_interest"] * 100).round(2)
    df["currency"] = currency

    return df.reset_index(drop=True)


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
