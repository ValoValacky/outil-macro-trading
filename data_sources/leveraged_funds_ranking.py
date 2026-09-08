"""Classement hebdomadaire des 8 devises majeures par force COT (Leveraged Funds, CFTC TFF).

Version autonome (pas d'import cross-projet, voir market_structure.py pour le pourquoi) du
classement calcule par HORIIZON/scripts/cot_weekly_ranking.py, qui tourne deja chaque semaine
via une tache planifiee Windows mais dont le resultat n'etait lu par rien - ni bot, ni
dashboard. Ajoute ici comme aide a la decision manuelle (choix de paire a surveiller), a la
demande explicite de l'utilisateur le 2026-09-09.

Meme methode que cot_strength.py : COT Index (Larry Williams) sur une fenetre courte (mois)
et longue (trimestre), score = moyenne ponderee des deux, "confluent" = les deux fenetres
pointent dans le meme sens (ecarte les devises en transition du classement des paires).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import combinations

import pandas as pd
import requests

CFTC_API_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"

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

# Convention marche : la devise qui apparait en premier dans cette liste est toujours la
# devise de base du symbole reel (ex: EUR avant USD -> "EURUSD", jamais "USDEUR").
CURRENCY_MARKET_PRIORITY: list[str] = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]

DEFAULT_HISTORY_WEEKS = 26
MONTH_WEEKS = 4
QUARTER_WEEKS = 13
QUARTER_WEIGHT = 0.6


@dataclass(frozen=True)
class CurrencyStrength:
    currency: str
    report_date: date
    net: float
    quarter_index: float
    month_index: float
    quarter_change: float
    month_change: float
    score: float
    confluent: bool


@dataclass(frozen=True)
class PairRanking:
    pair: str
    base: str
    quote: str
    direction: str
    conviction: float
    base_score: float
    quote_score: float
    mt5_symbol: str
    side: str


def _fetch_history(cftc_code: str, history_weeks: int, session: requests.Session) -> list[tuple[date, float, float]]:
    """Retourne [(date, long, short), ...] du plus recent au plus ancien."""
    params = {
        "cftc_contract_market_code": cftc_code,
        "$select": "report_date_as_yyyy_mm_dd,lev_money_positions_long,lev_money_positions_short",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(history_weeks),
    }
    resp = session.get(CFTC_API_URL, params=params, timeout=20)
    resp.raise_for_status()
    weeks = [
        (date.fromisoformat(row["report_date_as_yyyy_mm_dd"][:10]),
         float(row["lev_money_positions_long"]), float(row["lev_money_positions_short"]))
        for row in resp.json()
    ]
    weeks.sort(key=lambda w: w[0], reverse=True)
    return weeks


def fetch_all_currencies(history_weeks: int = DEFAULT_HISTORY_WEEKS) -> dict[str, list[tuple[date, float, float]]]:
    with requests.Session() as session:
        return {ccy: _fetch_history(code, history_weeks, session) for ccy, code in CURRENCY_CODES.items()}


def _index_at(nets: list[float], offset: int, weeks: int) -> float:
    window = nets[offset:offset + weeks]
    if len(window) < 2:
        return 50.0
    lo, hi = min(window), max(window)
    if hi - lo < 1e-6:
        return 50.0
    return (nets[offset] - lo) / (hi - lo) * 100.0


def compute_currency_strength(
    history: dict[str, list[tuple[date, float, float]]],
    month_weeks: int = MONTH_WEEKS,
    quarter_weeks: int = QUARTER_WEEKS,
    quarter_weight: float = QUARTER_WEIGHT,
) -> list[CurrencyStrength]:
    results = []
    for ccy, weeks in history.items():
        nets = [long - short for _, long, short in weeks]
        if len(nets) < 2:
            continue
        quarter_idx = _index_at(nets, 0, quarter_weeks)
        month_idx = _index_at(nets, 0, month_weeks)
        quarter_change = nets[0] - nets[min(quarter_weeks, len(nets) - 1)]
        month_change = nets[0] - nets[min(month_weeks, len(nets) - 1)]
        score = quarter_weight * quarter_idx + (1 - quarter_weight) * month_idx
        confluent = (quarter_idx > 50.0 and month_idx > 50.0) or (quarter_idx < 50.0 and month_idx < 50.0)
        results.append(CurrencyStrength(
            currency=ccy, report_date=weeks[0][0], net=nets[0],
            quarter_index=quarter_idx, month_index=month_idx,
            quarter_change=quarter_change, month_change=month_change,
            score=score, confluent=confluent,
        ))
    results.sort(key=lambda c: c.score, reverse=True)
    return results


def _mt5_symbol_and_side(strong: str, weak: str) -> tuple[str, str]:
    market_base, market_quote = sorted((strong, weak), key=lambda c: CURRENCY_MARKET_PRIORITY.index(c))
    symbol = market_base + market_quote
    side = "BUY" if market_base == strong else "SELL"
    return symbol, side


def rank_pairs(strengths: list[CurrencyStrength], top_n: int = 3, require_confluence: bool = True) -> list[PairRanking]:
    candidates = [s for s in strengths if s.confluent] if require_confluence else strengths
    rankings = []
    for a, b in combinations(candidates, 2):
        base, quote = (a, b) if a.score >= b.score else (b, a)
        conviction = base.score - quote.score
        mt5_symbol, side = _mt5_symbol_and_side(base.currency, quote.currency)
        rankings.append(PairRanking(
            pair=f"{base.currency}{quote.currency}", base=base.currency, quote=quote.currency,
            direction=f"LONG {base.currency} / SHORT {quote.currency}", conviction=conviction,
            base_score=base.score, quote_score=quote.score, mt5_symbol=mt5_symbol, side=side,
        ))
    rankings.sort(key=lambda r: r.conviction, reverse=True)
    return rankings[:top_n]


def build_ranking_report(history_weeks: int = DEFAULT_HISTORY_WEEKS, top_n: int = 3) -> dict:
    history = fetch_all_currencies(history_weeks)
    strengths = compute_currency_strength(history)
    if not strengths:
        return {"available": False}

    strength_df = pd.DataFrame([
        {
            "Rang": i + 1, "Devise": s.currency, "Score": round(s.score, 1),
            "Index trimestre": round(s.quarter_index, 1), "Index mois": round(s.month_index, 1),
            "Chg trimestre (contrats)": round(s.quarter_change), "Chg mois (contrats)": round(s.month_change),
            "Net position": round(s.net), "Confluent": "Oui" if s.confluent else "Non",
        }
        for i, s in enumerate(strengths)
    ])

    excluded = [s.currency for s in strengths if not s.confluent]
    pairs = rank_pairs(strengths, top_n=top_n, require_confluence=True)
    pairs_df = pd.DataFrame([
        {
            "TOP": i + 1, "Symbole MT5": p.mt5_symbol, "Sens": p.side,
            "Detail": p.direction, "Conviction (ecart de score)": round(p.conviction, 1),
        }
        for i, p in enumerate(pairs)
    ])

    return {
        "available": True,
        "report_date": strengths[0].report_date.isoformat(),
        "strength_df": strength_df,
        "excluded": excluded,
        "pairs_df": pairs_df,
    }
