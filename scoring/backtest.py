"""Backtest historique : la paire suggeree par l'outil (devise la plus forte
vs la plus faible du score composite, chaque semaine) aurait-elle genere un
rendement positif ?

Methodologie :
1. Pour chaque date de rapport COT passee (grille hebdomadaire), on
   recalcule le score composite de chaque devise "tel qu'il aurait ete"
   (meme logique as-of que scoring/history.py), et on identifie la paire
   BASE (la plus forte) / QUOTE (la plus faible) de la semaine.
2. On mesure le rendement de cette paire sur les N semaines suivantes
   (prix Yahoo Finance).
3. On agrege : rendement moyen, taux de reussite (% de semaines positives).

Limites assumees (a lire avant d'interpreter les resultats) :
- Echantillon limite (dependant de l'historique COT/OECD disponible), fenetres
  hebdomadaires chevauchantes (pas des essais independants) -> a lire comme
  une indication, pas une preuve statistique forte.
- Le baseline de comparaison est theorique (une selection aleatoire de paire
  a un rendement moyen attendu de ~0% par symetrie, puisque chaque paire a
  sa paire inverse dans l'univers) plutot que simule avec de vraies donnees,
  pour limiter le nombre d'appels aux API gratuites.
- Les poids testes sont ceux actuellement en place (scoring/engine.py) : ce
  backtest ne les "decouvre" pas, il verifie s'ils ont un edge historique.
"""

import pickle
import time
from pathlib import Path

import pandas as pd

from data_sources.bis_rates import fetch_policy_rate_history, summarize_policy_rate
from data_sources.common import CURRENCIES
from data_sources.cot_report import fetch_cot_history, summarize_cot_momentum
from data_sources.oecd_macro import (
    fetch_cpi_yoy,
    fetch_gdp_growth,
    fetch_unemployment_rate,
    summarize_series,
)
from data_sources.technical import fetch_price_history
from scoring.engine import score_currency
from scoring.history import _as_of


_CACHE_DIR = Path(__file__).parent.parent / ".backtest_cache"


def fetch_long_raw_data(
    currencies: list[str],
    cot_weeks_back: int = 75,
    oecd_start_period: str = "2024-06",
    use_disk_cache: bool = True,
) -> dict:
    """Recupere l'historique complet (plus long que le dashboard live) une seule
    fois par devise, reutilise ensuite pour tous les recalculs 'as of'.

    Cache disque local (usage dev/analyse uniquement, pas pour le dashboard
    deploye) : evite de re-solliciter OECD/BIS/CFTC a chaque nouvelle analyse
    avec les memes parametres, utile vu leurs limites de requetes."""
    cache_key = f"{'-'.join(sorted(currencies))}_{cot_weeks_back}_{oecd_start_period}"
    cache_file = _CACHE_DIR / f"{cache_key}.pkl"

    if use_disk_cache and cache_file.exists():
        with open(cache_file, "rb") as f:
            return pickle.load(f)

    raw = {}
    for ccy in currencies:
        policy = fetch_policy_rate_history(ccy, lookback_days=cot_weeks_back * 7 + 400)
        cpi = fetch_cpi_yoy(ccy, start_period=oecd_start_period)
        time.sleep(1)
        gdp = fetch_gdp_growth(ccy, start_period=oecd_start_period)
        time.sleep(1)
        unemployment = fetch_unemployment_rate(ccy, start_period=oecd_start_period)
        cot = fetch_cot_history(ccy, weeks_back=cot_weeks_back)
        raw[ccy] = {"policy": policy, "cpi": cpi, "gdp": gdp, "unemployment": unemployment, "cot": cot}

    if use_disk_cache:
        _CACHE_DIR.mkdir(exist_ok=True)
        with open(cache_file, "wb") as f:
            pickle.dump(raw, f)

    return raw


def compute_weekly_scores(raw: dict, weeks_back: int = 52, forward_weeks: int = 4) -> dict:
    """Pour chaque date de test, calcule le CurrencyScore complet (avec le detail
    par indicateur) de chaque devise. Reutilise pour le score composite ET pour
    l'attribution par facteur (compute_factor_picks)."""
    any_currency = next(iter(raw.values()))
    all_dates = any_currency["cot"]["date"].sort_values().reset_index(drop=True)
    if len(all_dates) <= forward_weeks:
        return {}

    test_dates = all_dates.iloc[:-forward_weeks] if forward_weeks > 0 else all_dates
    test_dates = test_dates.tail(weeks_back)

    scores_by_date = {}
    for as_of_date in test_dates:
        scores = {}
        for ccy, data in raw.items():
            scores[ccy] = score_currency(
                ccy,
                policy_rate_summary=summarize_policy_rate(_as_of(data["policy"], as_of_date)),
                cpi_summary=summarize_series(_as_of(data["cpi"], as_of_date)),
                gdp_summary=summarize_series(_as_of(data["gdp"], as_of_date)),
                unemployment_summary=summarize_series(_as_of(data["unemployment"], as_of_date)),
                cot_summary=summarize_cot_momentum(_as_of(data["cot"], as_of_date)),
            )
        scores_by_date[as_of_date] = scores

    return scores_by_date


def _picks_from_scores(scores_by_date: dict, key_fn) -> pd.DataFrame:
    """key_fn(CurrencyScore) -> valeur numerique a classer. En cas d'egalite,
    la premiere devise rencontree (ordre de CURRENCIES) est retenue."""
    rows = []
    for as_of_date, scores in scores_by_date.items():
        values = {ccy: key_fn(cs) for ccy, cs in scores.items()}
        best = max(values, key=values.get)
        worst = min(values, key=values.get)
        if best == worst:
            continue
        rows.append(
            {
                "date": as_of_date,
                "best_base": best,
                "worst_quote": worst,
                "score_gap": round(values[best] - values[worst], 2),
            }
        )
    return pd.DataFrame(rows)


def compute_weekly_picks(scores_by_date: dict) -> pd.DataFrame:
    """Picks bases sur le score composite (methode actuelle du dashboard)."""
    return _picks_from_scores(scores_by_date, key_fn=lambda cs: cs.composite_score)


def compute_factor_picks(scores_by_date: dict, indicator_name: str) -> pd.DataFrame:
    """Picks bases sur UN SEUL indicateur (isole des autres) - pour l'attribution
    de performance : quel facteur porte (ou plombe) le score composite ?"""

    def _factor_score(cs, name=indicator_name):
        return next(ind.score for ind in cs.indicators if ind.indicator == name)

    return _picks_from_scores(scores_by_date, key_fn=_factor_score)


def _price_near(df: pd.DataFrame, target_date, prefer_after: bool) -> float | None:
    if df.empty:
        return None
    if prefer_after:
        candidates = df[df["date"] >= target_date]
        row = candidates.iloc[0] if not candidates.empty else df.iloc[-1]
    else:
        candidates = df[df["date"] <= target_date]
        row = candidates.iloc[-1] if not candidates.empty else df.iloc[0]
    return float(row["close"])


def compute_forward_returns(
    picks_df: pd.DataFrame,
    forward_weeks: int = 4,
    price_fetch_delay: float = 1.0,
    price_cache: dict | None = None,
) -> pd.DataFrame:
    """Recupere le prix de chaque paire unique une seule fois (periode complete),
    puis calcule le rendement a forward_weeks pour chaque pick. Un price_cache
    externe peut etre passe pour partager les prix deja recuperes entre
    plusieurs backtests (composite + facteurs) et eviter les appels redondants."""
    if picks_df.empty:
        return picks_df

    price_cache = price_cache if price_cache is not None else {}
    unique_pairs = picks_df[["best_base", "worst_quote"]].drop_duplicates()
    for _, r in unique_pairs.iterrows():
        pair = (r["best_base"], r["worst_quote"])
        if pair in price_cache:
            continue
        df = fetch_price_history(pair[0], pair[1], period="2y")
        price_cache[pair] = df
        time.sleep(price_fetch_delay)

    returns = []
    for _, row in picks_df.iterrows():
        pair = (row["best_base"], row["worst_quote"])
        df = price_cache.get(pair, pd.DataFrame())
        entry_date = row["date"]
        exit_date = entry_date + pd.Timedelta(weeks=forward_weeks)

        entry_price = _price_near(df, entry_date, prefer_after=True)
        exit_price = _price_near(df, exit_date, prefer_after=True)

        forward_return_pct = None
        if entry_price and exit_price:
            forward_return_pct = round((exit_price - entry_price) / entry_price * 100, 3)

        returns.append(forward_return_pct)

    picks_df = picks_df.copy()
    picks_df["forward_return_pct"] = returns
    return picks_df


def summarize_backtest(results_df: pd.DataFrame) -> dict:
    valid = results_df.dropna(subset=["forward_return_pct"])
    if valid.empty:
        return {"n": 0}

    wins = (valid["forward_return_pct"] > 0).sum()
    return {
        "n": len(valid),
        "mean_return_pct": round(valid["forward_return_pct"].mean(), 3),
        "median_return_pct": round(valid["forward_return_pct"].median(), 3),
        "win_rate_pct": round(wins / len(valid) * 100, 1),
        "best_pct": round(valid["forward_return_pct"].max(), 3),
        "worst_pct": round(valid["forward_return_pct"].min(), 3),
        "std_pct": round(valid["forward_return_pct"].std(), 3),
    }


FACTOR_NAMES = ["policy_rate", "cot_positioning", "cpi_yoy", "gdp_growth_yoy", "unemployment_rate"]


def run_backtest(
    currencies: list[str] | None = None,
    weeks_back: int = 52,
    forward_weeks: int = 4,
    cot_weeks_back: int = 75,
    oecd_start_period: str = "2024-06",
) -> tuple[pd.DataFrame, dict]:
    currencies = currencies or CURRENCIES
    raw = fetch_long_raw_data(currencies, cot_weeks_back=cot_weeks_back, oecd_start_period=oecd_start_period)
    scores_by_date = compute_weekly_scores(raw, weeks_back=weeks_back, forward_weeks=forward_weeks)
    picks = compute_weekly_picks(scores_by_date)
    results = compute_forward_returns(picks, forward_weeks=forward_weeks)
    summary = summarize_backtest(results)
    return results, summary


def run_factor_attribution(
    currencies: list[str] | None = None,
    weeks_back: int = 52,
    forward_weeks: int = 4,
    cot_weeks_back: int = 75,
    oecd_start_period: str = "2024-06",
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Compare le score composite a chacun de ses 5 facteurs pris isolement, pour
    voir lequel porte (ou plombe) la performance. Renvoie (resultats detailles
    par strategie, tableau de synthese comparatif)."""
    currencies = currencies or CURRENCIES
    raw = fetch_long_raw_data(currencies, cot_weeks_back=cot_weeks_back, oecd_start_period=oecd_start_period)
    scores_by_date = compute_weekly_scores(raw, weeks_back=weeks_back, forward_weeks=forward_weeks)

    price_cache: dict = {}
    all_results = {}
    summary_rows = []

    composite_picks = compute_weekly_picks(scores_by_date)
    composite_results = compute_forward_returns(composite_picks, forward_weeks=forward_weeks, price_cache=price_cache)
    all_results["composite"] = composite_results
    summary_rows.append({"strategie": "composite (actuel)", **summarize_backtest(composite_results)})

    for factor in FACTOR_NAMES:
        factor_picks = compute_factor_picks(scores_by_date, factor)
        factor_results = compute_forward_returns(factor_picks, forward_weeks=forward_weeks, price_cache=price_cache)
        all_results[factor] = factor_results
        summary_rows.append({"strategie": factor, **summarize_backtest(factor_results)})

    summary_df = pd.DataFrame(summary_rows)
    return all_results, summary_df
