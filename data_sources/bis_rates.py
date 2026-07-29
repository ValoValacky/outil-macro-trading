"""Adaptateur BIS (Bank for International Settlements) - taux directeurs des banques centrales.

Source : https://data.bis.org - dataset WS_CBPOL (Central bank policy rates), gratuit, sans cle API.
"""

import io
from datetime import date, timedelta

import pandas as pd

from .common import BIS_AREA, http_get

BIS_BASE = "https://stats.bis.org/api/v1/data/BIS,WS_CBPOL,1.0"


def fetch_policy_rate_history(currency: str, lookback_days: int = 730) -> pd.DataFrame:
    """Retourne l'historique du taux directeur (quotidien) pour une devise donnee."""
    area = BIS_AREA[currency]
    start_period = (date.today() - timedelta(days=lookback_days)).isoformat()
    url = f"{BIS_BASE}/D.{area}"
    resp = http_get(url, params={"startPeriod": start_period, "format": "csv"}, headers={"Accept": "text/csv"})
    df = pd.read_csv(io.StringIO(resp.text))
    df = df[["TIME_PERIOD", "OBS_VALUE"]].rename(columns={"TIME_PERIOD": "date", "OBS_VALUE": "policy_rate"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["policy_rate"])  # BIS laisse des trous (jours non reportes) dans la serie quotidienne
    df = df.sort_values("date").reset_index(drop=True)
    df["currency"] = currency
    return df


def fetch_all_policy_rates(currencies: list[str] | None = None, lookback_days: int = 730) -> pd.DataFrame:
    """Concatene l'historique des taux directeurs pour toutes les devises demandees."""
    currencies = currencies or list(BIS_AREA.keys())
    frames = [fetch_policy_rate_history(ccy, lookback_days) for ccy in currencies]
    return pd.concat(frames, ignore_index=True)


def summarize_policy_rate(history: pd.DataFrame, trend_lookback_days: int = 90) -> dict:
    """Resume l'historique d'une devise : niveau actuel + variation sur la fenetre de tendance."""
    if history.empty:
        return {"level": None, "change": None, "as_of": None}

    latest_row = history.iloc[-1]
    cutoff = latest_row["date"] - pd.Timedelta(days=trend_lookback_days)
    past = history[history["date"] <= cutoff]
    past_rate = past.iloc[-1]["policy_rate"] if not past.empty else history.iloc[0]["policy_rate"]

    return {
        "level": float(latest_row["policy_rate"]),
        "change": float(latest_row["policy_rate"] - past_rate),
        "as_of": latest_row["date"].date().isoformat(),
    }
