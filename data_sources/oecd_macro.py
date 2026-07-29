"""Adaptateur OECD (SDMX API) - inflation (CPI), chomage et croissance du PIB.

Source : https://sdmx.oecd.org - gratuit, sans cle API.

Les cles SDMX ci-dessous ont ete determinees empiriquement (exploration des
dataflows/codelists OECD) et validees par des requetes reelles pour les 8
devises majeures. Voir README.md, section "Notes techniques", pour le detail.
"""

from datetime import date

import pandas as pd

from .common import (
    OECD_AREA,
    OECD_GDP_AREA_OVERRIDE,
    OECD_UNEMPLOYMENT_AREA_OVERRIDE,
    http_get,
)

OECD_BASE = "https://sdmx.oecd.org/public/rest/data"

# Le CPI n'est pas publie a la meme frequence ni sous la meme classification
# (COICOP 1999 vs 2018) pour tous les pays. On essaie plusieurs combinaisons
# dans l'ordre jusqu'a trouver celle qui renvoie des donnees :
# - USD, EUR, GBP, AUD, CAD, CHF : mensuel, COICOP 1999
# - JPY : mensuel, COICOP 2018
# - NZD : trimestriel, COICOP 1999 (Statistics NZ publie le CPI par trimestre)
_CPI_CANDIDATES = [
    ("OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,", "{area}.M.N.CPI.PA._T.N.GY"),
    ("OECD.SDD.TPS,DSD_PRICES_COICOP2018@DF_PRICES_C2018_ALL,", "{area}.M.N.CPI.PA._T.N.GY"),
    ("OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,", "{area}.Q.N.CPI.PA._T.N.GY"),
    ("OECD.SDD.TPS,DSD_PRICES_COICOP2018@DF_PRICES_C2018_ALL,", "{area}.Q.N.CPI.PA._T.N.GY"),
]

_UNEMPLOYMENT_FLOW = "OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,"
# Taux de chomage, CVS, 15+. Certains pays (ex: Nouvelle-Zelande) ne publient
# pas de serie mensuelle : on essaie M puis Q puis A.
_UNEMPLOYMENT_KEY_CANDIDATES = [
    "{area}.UNE_LF_M.PT_LF_SUB._Z.Y._T.Y_GE15._Z.M",
    "{area}.UNE_LF_M.PT_LF_SUB._Z.Y._T.Y_GE15._Z.Q",
    "{area}.UNE_LF_M.PT_LF_SUB._Z.Y._T.Y_GE15._Z.A",
]

_GDP_FLOW = "OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_OECD,"
_GDP_KEY = "Q.Y.{area}.S1.S1.B1GQ._Z._Z._Z.PC.L.GY.T0102"  # PIB, glissement annuel, trimestriel


def _parse_jsondata(payload: dict) -> pd.DataFrame:
    """Transforme une reponse SDMX-JSON (format=jsondata, cle explicite -> une seule serie)."""
    dataset = payload["data"]["dataSets"][0]
    series = dataset.get("series", {})
    if not series:
        return pd.DataFrame(columns=["date", "value"])

    obs_dims = payload["data"]["structures"][0]["dimensions"]["observation"]
    time_values = next(d["values"] for d in obs_dims if d["id"] == "TIME_PERIOD")
    time_labels = [v["id"] for v in time_values]

    # Une seule serie attendue (cle entierement explicite)
    observations = next(iter(series.values()))["observations"]

    rows = []
    for idx, obs in observations.items():
        period = time_labels[int(idx)]
        value = obs[0]
        rows.append((period, value))

    df = pd.DataFrame(rows, columns=["date", "value"])
    sample = df["date"].iloc[0]
    if "-Q" in sample:
        freq = "Q"
    elif len(sample) == 4:
        freq = "A"
    else:
        freq = "M"
    df["date"] = pd.PeriodIndex(df["date"], freq=freq).to_timestamp()
    return df.sort_values("date").reset_index(drop=True)


def _fetch(flow: str, key: str, start_period: str) -> pd.DataFrame:
    url = f"{OECD_BASE}/{flow}/{key}"
    resp = http_get(url, params={"startPeriod": start_period, "format": "jsondata"})
    return _parse_jsondata(resp.json())


def fetch_cpi_yoy(currency: str, start_period: str = "2018-01") -> pd.DataFrame:
    area = OECD_AREA[currency]
    df = pd.DataFrame()
    for flow, key_template in _CPI_CANDIDATES:
        df = _fetch(flow, key_template.format(area=area), start_period)
        if not df.empty:
            break
    df["currency"] = currency
    df["indicator"] = "cpi_yoy"
    return df


def fetch_unemployment_rate(currency: str, start_period: str = "2018-01") -> pd.DataFrame:
    area = OECD_UNEMPLOYMENT_AREA_OVERRIDE.get(currency, OECD_AREA[currency])
    df = pd.DataFrame()
    for key_template in _UNEMPLOYMENT_KEY_CANDIDATES:
        df = _fetch(_UNEMPLOYMENT_FLOW, key_template.format(area=area), start_period)
        if not df.empty:
            break
    df["currency"] = currency
    df["indicator"] = "unemployment_rate"
    if currency in OECD_UNEMPLOYMENT_AREA_OVERRIDE:
        df["note"] = f"proxy: {area} (agregat zone euro indisponible sur ce dataflow)"
    return df


def fetch_gdp_growth(currency: str, start_period: str = "2018-01") -> pd.DataFrame:
    area = OECD_GDP_AREA_OVERRIDE.get(currency, OECD_AREA[currency])
    df = _fetch(_GDP_FLOW, _GDP_KEY.format(area=area), start_period)
    df["currency"] = currency
    df["indicator"] = "gdp_growth_yoy"
    if currency in OECD_GDP_AREA_OVERRIDE:
        df["note"] = f"proxy: {area} (agregat zone euro indisponible sur ce dataflow)"
    return df


def fetch_all_macro(currency: str, start_period: str = "2018-01") -> dict[str, pd.DataFrame]:
    """Recupere les 3 indicateurs macro pour une devise. Renvoie un dict robuste aux erreurs partielles."""
    result = {}
    for name, fetcher in [
        ("cpi_yoy", fetch_cpi_yoy),
        ("unemployment_rate", fetch_unemployment_rate),
        ("gdp_growth_yoy", fetch_gdp_growth),
    ]:
        try:
            result[name] = fetcher(currency, start_period)
        except Exception as exc:  # source externe : ne bloque pas les autres indicateurs
            result[name] = pd.DataFrame()
            result[f"{name}_error"] = str(exc)
    return result


def summarize_series(df: pd.DataFrame, value_col: str = "value", trend_periods: int = 3) -> dict:
    """Resume une serie macro : derniere valeur + variation sur les N dernieres periodes."""
    if df.empty:
        return {"level": None, "change": None, "as_of": None}

    latest = df.iloc[-1]
    past_idx = max(0, len(df) - 1 - trend_periods)
    past_value = df.iloc[past_idx][value_col]

    return {
        "level": float(latest[value_col]),
        "change": float(latest[value_col] - past_value),
        "as_of": latest["date"].date().isoformat(),
    }
