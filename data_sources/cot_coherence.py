"""Controle de coherence entre categories CFTC - ajoute le 2026-08-31 suite a un
vrai cas trouve sur l'AUD : le rapport Legacy (Non-Commercial, utilise par
cot_report.py) affichait "retournement baissier", alors que le rapport TFF
(Traders in Financial Futures) montrait les Leveraged Funds nettement acheteurs
(+54 061) ET les Asset Managers nettement vendeurs (-45 427) au meme moment -
un vrai desaccord interne a la communaute speculative, pas une erreur.

Objectif : avant de presenter un signal COT comme une confirmation propre pour
une paire, verifier si les differentes categories d'acteurs (Non-Commercial
Legacy, Leveraged Funds TFF, Asset Managers TFF) pointent dans le meme sens.
Si elles divergent, le signal doit etre traite avec prudence, pas comme une
confirmation nette (voir Prompt_Analyse_Dimanche_Soir, regle ajoutee le meme jour).
"""

import requests
import pandas as pd

from data_sources.cot_report import CURRENCY_CODES

TFF_API_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"


def fetch_tff_positioning(currency: str, weeks_back: int = 8) -> pd.DataFrame:
    """Historique hebdomadaire des positions nettes Leveraged Funds et Asset
    Managers (rapport TFF) pour une devise - categories distinctes du rapport
    Legacy utilise par cot_report.py."""
    params = {
        "cftc_contract_market_code": CURRENCY_CODES[currency],
        "$select": "report_date_as_yyyy_mm_dd,lev_money_positions_long,lev_money_positions_short,"
                   "asset_mgr_positions_long,asset_mgr_positions_short",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(weeks_back),
    }
    resp = requests.get(TFF_API_URL, params=params, timeout=15)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame({
        "date": [pd.Timestamp(row["report_date_as_yyyy_mm_dd"]) for row in rows],
        "lev_money_net": [
            float(row["lev_money_positions_long"]) - float(row["lev_money_positions_short"]) for row in rows
        ],
        "asset_mgr_net": [
            float(row["asset_mgr_positions_long"]) - float(row["asset_mgr_positions_short"]) for row in rows
        ],
    })
    return df.sort_values("date").reset_index(drop=True)


def _sign_label(value: float, threshold: float = 0.0) -> str:
    if value > threshold:
        return "haussier"
    if value < -threshold:
        return "baissier"
    return "neutre"


def check_pair_coherence(base: str, quote: str, tff_base: pd.DataFrame, tff_quote: pd.DataFrame,
                          legacy_level_base: float | None, legacy_level_quote: float | None) -> dict:
    """Compare le sens de la paire (base - quote) sur 3 categories : Non-Commercial
    Legacy (niveau % OI actuel, deja calcule ailleurs par cot_report.py - passe en
    parametre pour eviter un appel redondant), Leveraged Funds TFF, Asset Managers
    TFF. Meme base de comparaison pour les 3 : le signe du differentiel base-quote
    au niveau ACTUEL (pas la tendance/retournement, un concept different)."""
    if tff_base.empty or tff_quote.empty:
        return {"available": False}

    latest_base = tff_base.iloc[-1]
    latest_quote = tff_quote.iloc[-1]

    lev_diff = latest_base["lev_money_net"] - latest_quote["lev_money_net"]
    am_diff = latest_base["asset_mgr_net"] - latest_quote["asset_mgr_net"]
    legacy_diff = (
        legacy_level_base - legacy_level_quote
        if legacy_level_base is not None and legacy_level_quote is not None
        else None
    )

    signs = {
        "Non-Commercial (Legacy)": _sign_label(legacy_diff) if legacy_diff is not None else "indisponible",
        "Leveraged Funds (TFF)": _sign_label(lev_diff),
        "Asset Managers (TFF)": _sign_label(am_diff),
    }
    # Neutre/indisponible exclus du calcul de coherence (pas assez tranche pour compter comme desaccord)
    directional = {k: v for k, v in signs.items() if v not in ("neutre", "indisponible")}
    unique_directions = set(directional.values())
    coherent = len(unique_directions) <= 1

    return {
        "available": True,
        "pair": f"{base}/{quote}",
        "signs": signs,
        "lev_diff": lev_diff,
        "am_diff": am_diff,
        "coherent": coherent,
        "as_of": latest_base["date"].date().isoformat(),
    }
