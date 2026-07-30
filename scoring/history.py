"""Reconstruction de l'historique du score composite sans stockage persistant.

Toutes nos sources (BIS, OECD, CFTC) renvoient deja des series historiques
completes. Plutot que de stocker un instantane du score chaque jour (fragile
sur un hebergement gratuit dont le conteneur peut redemarrer), on recalcule
le score "tel qu'il aurait ete" a chaque date passee, en ne regardant que les
donnees disponibles jusqu'a cette date (logique "as of").

La grille temporelle utilisee est celle des dates de rapport COT (hebdo),
car c'est la source la plus contraignante et celle sur laquelle porte
l'essentiel de la demande (tendance sur les dernieres semaines).
"""

import pandas as pd

from data_sources.bis_rates import summarize_policy_rate
from data_sources.cot_report import summarize_cot_momentum
from data_sources.oecd_macro import summarize_series
from scoring.engine import score_currency


def _as_of(df: pd.DataFrame, as_of_date) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df["date"] <= as_of_date]


def build_score_history(
    currency: str,
    policy_history: pd.DataFrame,
    cpi_history: pd.DataFrame,
    gdp_history: pd.DataFrame,
    unemployment_history: pd.DataFrame,
    cot_history: pd.DataFrame,
    weeks_back: int = 12,
) -> pd.DataFrame:
    if cot_history.empty:
        return pd.DataFrame(columns=["date", "score"])

    report_dates = cot_history["date"].tail(weeks_back)
    rows = []
    for as_of_date in report_dates:
        cs = score_currency(
            currency,
            policy_rate_summary=summarize_policy_rate(_as_of(policy_history, as_of_date)),
            cpi_summary=summarize_series(_as_of(cpi_history, as_of_date)),
            gdp_summary=summarize_series(_as_of(gdp_history, as_of_date)),
            unemployment_summary=summarize_series(_as_of(unemployment_history, as_of_date)),
            cot_summary=summarize_cot_momentum(_as_of(cot_history, as_of_date)),
        )
        rows.append({"date": as_of_date, "score": cs.composite_score})

    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def build_multi_currency_history(currency_raw_data: dict[str, dict], weeks_back: int = 12) -> pd.DataFrame:
    """currency_raw_data : {currency: {"policy": df, "cpi": df, "gdp": df, "unemployment": df, "cot": df}}
    Renvoie un DataFrame large : une colonne par devise, une ligne par date."""
    series = {}
    for currency, raw in currency_raw_data.items():
        hist = build_score_history(
            currency,
            raw["policy"],
            raw["cpi"],
            raw["gdp"],
            raw["unemployment"],
            raw["cot"],
            weeks_back=weeks_back,
        )
        if not hist.empty:
            series[currency] = hist.set_index("date")["score"]

    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series)
