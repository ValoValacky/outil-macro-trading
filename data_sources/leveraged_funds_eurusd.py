"""Lecture approfondie du COT Leveraged Funds (CFTC TFF) pour l'EUR/USD.

Implemente la methode du guide `guide_cot_leveraged_funds_eurusd.pdf` :
Net Position, Weekly Change, Delta 4 semaines, decomposition Longs/Shorts
(accumulation / short covering / nouvelle vente / reduction), percentile sur
historique long, grille d'interpretation operationnelle, et divergence
prix/COT en reutilisant la meme detection de structure (BOS/CHoCH) que
Bot Trading FTMO / Gold Swing Confluence System.

Source des donnees : CFTC "Traders in Financial Futures" (dataset gpe5-46if),
categorie Leveraged Funds - memes contrats CFTC (EUR=099741, USD=098662) que
l'indicateur MT5 COT_Strength_RSI.mq5 et horiizon/cot_strength.py, pour ne
jamais raconter deux histoires differentes sur la meme donnee source. Les
codes de contrats sont dupliques ici (pas importes depuis HORIIZON) : voir
la note dans market_structure.py sur les limites du deploiement Streamlit
Community Cloud (un seul depot GitHub, pas de dossiers voisins).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import requests

from .market_structure import Bias, analyze_structure
from .technical import fetch_price_history

CFTC_API_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"

# Codes de contrats CFTC (rapport TFF), identiques a ceux de
# horiizon/cot_strength.py et de l'indicateur MT5 COT_Strength_RSI.mq5.
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

BASE_CCY = "EUR"
QUOTE_CCY = "USD"
HISTORY_WEEKS_5Y = 261  # ~5 ans, fenetre choisie pour le calcul du percentile
NEAR_ZERO_PCTL_BAND = 15.0  # |percentile - 50| <= ceci => "proche de zero" (grille 3.4)
LAG_WARNING_DAYS = 10  # rapport hebdomadaire : au-dela, le cache est probablement perime


@dataclass(frozen=True)
class CotFlowWeek:
    report_date: date
    long: float
    short: float
    open_interest: float | None

    @property
    def net(self) -> float:
        return self.long - self.short


def _fetch_currency_flow(cftc_code: str, history_weeks: int, session: requests.Session) -> list[CotFlowWeek]:
    """Comme horiizon.cot_strength.fetch_cot_history, mais recupere en plus l'open interest
    (utile pour le ratio Net/OI de la section 5.2 du guide - limite #4)."""
    params = {
        "cftc_contract_market_code": cftc_code,
        "$select": "report_date_as_yyyy_mm_dd,lev_money_positions_long,lev_money_positions_short,open_interest_all",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(history_weeks),
    }
    resp = session.get(CFTC_API_URL, params=params, timeout=20)
    resp.raise_for_status()
    weeks = []
    for row in resp.json():
        oi = row.get("open_interest_all")
        weeks.append(
            CotFlowWeek(
                report_date=date.fromisoformat(row["report_date_as_yyyy_mm_dd"][:10]),
                long=float(row["lev_money_positions_long"]),
                short=float(row["lev_money_positions_short"]),
                open_interest=float(oi) if oi not in (None, "") else None,
            )
        )
    weeks.sort(key=lambda w: w.report_date)
    return weeks


def fetch_pair_history(history_weeks: int = HISTORY_WEEKS_5Y) -> pd.DataFrame:
    """Historique hebdomadaire aligne EUR/USD (Leveraged Funds), le plus ancien en premier,
    avec toutes les mesures du guide deja calculees (Net, Weekly Change, Delta 4S,
    decomposition Longs/Shorts sur 4 semaines pour chaque devise)."""
    with requests.Session() as session:
        base_weeks = _fetch_currency_flow(CURRENCY_CODES[BASE_CCY], history_weeks, session)
        quote_weeks = _fetch_currency_flow(CURRENCY_CODES[QUOTE_CCY], history_weeks, session)

    base_df = pd.DataFrame(
        {
            "date": [w.report_date for w in base_weeks],
            "long_base": [w.long for w in base_weeks],
            "short_base": [w.short for w in base_weeks],
            "oi_base": [w.open_interest for w in base_weeks],
        }
    )
    quote_df = pd.DataFrame(
        {
            "date": [w.report_date for w in quote_weeks],
            "long_quote": [w.long for w in quote_weeks],
            "short_quote": [w.short for w in quote_weeks],
            "oi_quote": [w.open_interest for w in quote_weeks],
        }
    )
    df = pd.merge(base_df, quote_df, on="date", how="inner")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    df["net_base"] = df["long_base"] - df["short_base"]
    df["net_quote"] = df["long_quote"] - df["short_quote"]
    df["net_pair"] = df["net_base"] - df["net_quote"]
    df["weekly_change"] = df["net_pair"].diff()
    df["delta_4w"] = df["net_pair"].diff(4)

    df["delta_longs_base_4w"] = df["long_base"].diff(4)
    df["delta_shorts_base_4w"] = df["short_base"].diff(4)
    df["delta_longs_quote_4w"] = df["long_quote"].diff(4)
    df["delta_shorts_quote_4w"] = df["short_quote"].diff(4)

    df["net_pair_pct_oi"] = None
    has_oi = df["oi_base"].notna().all() and df["oi_quote"].notna().all()
    if has_oi:
        df["net_pair_pct_oi"] = (df["net_base"] / df["oi_base"] - df["net_quote"] / df["oi_quote"]) * 100.0

    return df


def compute_percentile(series: pd.Series, value: float) -> float:
    """Rang percentile (0-100) de `value` dans `series` - "outil simple" du guide,
    section 4.4 : Percentile_t = rang de Net_t dans l'historique choisi."""
    valid = series.dropna()
    if valid.empty:
        return 50.0
    return float((valid < value).mean() * 100.0)


def classify_flow_source(delta_longs: float, delta_shorts: float, min_move: float) -> str:
    """Source du mouvement sur 4 semaines (section 3.3) : ne traite pas toute hausse de
    la position nette comme un achat agressif - une hausse peut venir d'un simple
    debouclage de shorts (short covering)."""
    longs_up = delta_longs > min_move
    longs_down = delta_longs < -min_move
    shorts_up = delta_shorts > min_move
    shorts_down = delta_shorts < -min_move

    if longs_up and not shorts_up:
        return "Accumulation longue"
    if shorts_down and not longs_up:
        return "Short covering"
    if shorts_up and not longs_down:
        return "Nouvelle vente"
    if longs_down and not shorts_down:
        return "Reduction de longs"
    return "Stable / mouvement mixte"


def build_interpretation(net_pair: float, delta_4w: float, percentile: float, accel_threshold: float) -> dict:
    """Grille d'interpretation operationnelle, section 3.4 du guide - reproduit les 6 lignes
    du tableau (Positive/Negative/Proche de zero x Delta en acceleration/faible)."""
    near_zero = abs(percentile - 50.0) <= NEAR_ZERO_PCTL_BAND

    if near_zero:
        if delta_4w > accel_threshold:
            return {
                "situation": "Proche de zero, Delta 4S fortement positif",
                "lecture": "Rotation haussiere en cours",
                "reaction": "Attendre une confirmation de structure sur le graphique.",
            }
        if delta_4w < -accel_threshold:
            return {
                "situation": "Proche de zero, Delta 4S fortement negatif",
                "lecture": "Rotation baissiere en cours",
                "reaction": "Attendre une cassure ou un rejet confirme.",
            }
        return {
            "situation": "Proche de zero, Delta 4S faible",
            "lecture": "Positionnement neutre, pas de biais net exploitable",
            "reaction": "Attendre un biais plus tranche avant de construire un scenario sur le COT.",
        }

    if net_pair > 0:
        if delta_4w > accel_threshold:
            return {
                "situation": "Position nette positive, Delta 4S positif et en acceleration",
                "lecture": "Biais haussier confirme par le flux",
                "reaction": "Privilegier les achats sur repli si le prix confirme.",
            }
        return {
            "situation": "Position nette positive, Delta 4S faible ou negatif",
            "lecture": "Biais haussier mais perte d'elan",
            "reaction": "Eviter de poursuivre les sommets ; attendre une consolidation.",
        }

    # net_pair <= 0
    if delta_4w < -accel_threshold:
        return {
            "situation": "Position nette negative, Delta 4S negatif et en acceleration",
            "lecture": "Biais baissier confirme",
            "reaction": "Privilegier les ventes sur reprise technique.",
        }
    if delta_4w > 0:
        return {
            "situation": "Position nette negative, Delta 4S positif",
            "lecture": "Biais baissier en reduction",
            "reaction": "Reduire l'agressivite vendeuse ; surveiller une transition.",
        }
    return {
        "situation": "Position nette negative, Delta 4S faible",
        "lecture": "Biais baissier installe mais dynamique hesitante",
        "reaction": "Pas de nouvelle vente agressive ; attendre une confirmation supplementaire.",
    }


def detect_divergence(cot_df: pd.DataFrame, lookback_weeks: int = 78) -> dict:
    """Compare la structure du prix EUR/USD (resamplee sur une cadence hebdomadaire) a la
    structure du Net Position Leveraged Funds, en reutilisant EXACTEMENT `analyze_structure`
    (BOS/CHoCH) de Bot Trading FTMO / Gold Swing Confluence - pas une detection de sommets/
    creux reecrite from scratch, pour rester coherent avec le reste des outils."""
    price_daily = fetch_price_history(BASE_CCY, QUOTE_CCY, period="5y")
    if price_daily.empty or len(cot_df) < 10:
        return {"available": False}

    recent = cot_df.tail(lookback_weeks).copy()

    weekly_price = (
        price_daily.set_index("date")[["high", "low", "close"]]
        .resample("W")
        .agg({"high": "max", "low": "min", "close": "last"})
        .reset_index()
    )

    merged = (
        pd.merge_asof(
            recent[["date", "net_pair"]].sort_values("date"),
            weekly_price.sort_values("date"),
            on="date",
            direction="nearest",
            tolerance=pd.Timedelta("4D"),
        )
        .dropna(subset=["close"])
        .reset_index(drop=True)
    )

    if len(merged) < 10:
        return {"available": False}

    # Index construit a partir de tableaux numpy (pas des Series) : `merged` a ete
    # reindexe (reset_index) juste au-dessus, mais on evite ici tout risque que pandas
    # tente un realignement par LABEL entre les colonnes et le DatetimeIndex plutot
    # qu'un simple positionnement - piege deja rencontre en test (tout finissait en NaN).
    dt_index = pd.DatetimeIndex(merged["date"].to_numpy())
    close_vals = merged["close"].to_numpy()
    price_ohlc = pd.DataFrame(
        {
            "open": pd.Series(close_vals).shift(1).fillna(close_vals[0]).to_numpy(),
            "high": merged["high"].to_numpy(),
            "low": merged["low"].to_numpy(),
            "close": close_vals,
        },
        index=dt_index,
    )
    net_vals = merged["net_pair"].to_numpy()
    cot_ohlc = pd.DataFrame(
        {"open": net_vals, "high": net_vals, "low": net_vals, "close": net_vals},
        index=dt_index,
    )

    price_struct = analyze_structure(price_ohlc, swing_lookback=2, atr_period=6, min_swing_size_atr_mult=0.3)
    cot_struct = analyze_structure(cot_ohlc, swing_lookback=2, atr_period=6, min_swing_size_atr_mult=0.05)

    price_bias, cot_bias = price_struct.bias, cot_struct.bias

    if price_bias is Bias.BULLISH and cot_bias is Bias.BEARISH:
        verdict = "divergence_baissiere"
        message = "Le prix progresse (structure haussiere) mais le flux Leveraged Funds se degrade : accumulation qui ralentit ou distribution en cours. Prudence sur la poursuite haussiere."
    elif price_bias is Bias.BEARISH and cot_bias is Bias.BULLISH:
        verdict = "divergence_haussiere"
        message = "Le prix baisse (structure baissiere) mais le flux Leveraged Funds se redresse : possible essoufflement vendeur ou rotation haussiere en gestation."
    elif price_bias is Bias.UNDEFINED or cot_bias is Bias.UNDEFINED:
        verdict = "indetermine"
        message = "Structure de prix ou de COT pas encore assez etablie sur la fenetre analysee pour conclure."
    elif price_bias == cot_bias:
        verdict = "convergence"
        message = f"Le prix et le flux Leveraged Funds racontent la meme histoire ({price_bias.value}) : confirmation croisee du biais."
    else:
        verdict = "neutre"
        message = "Pas de divergence ni de convergence nette identifiee sur la fenetre analysee."

    return {
        "available": True,
        "price_bias": price_bias.value,
        "cot_bias": cot_bias.value,
        "verdict": verdict,
        "message": message,
        "price_last_date": price_daily["date"].iloc[-1].date().isoformat(),
    }


def lag_warning(last_report_date: pd.Timestamp) -> str | None:
    """Le CFTC publie le vendredi les positions arretees au mardi precedent (section 5.1) -
    signale un cache probablement perime plutot que d'utiliser silencieusement des chiffres
    trop anciens (cf. feedback-cot-differential-breakdown)."""
    days_since = (pd.Timestamp.today().normalize() - last_report_date).days
    if days_since > LAG_WARNING_DAYS:
        return (
            f"Dernier rapport COT integre : {last_report_date.date().isoformat()} "
            f"({days_since} jours) - le cache est peut-etre perime, verifier la source CFTC."
        )
    return None


def build_report(history_weeks: int = HISTORY_WEEKS_5Y) -> dict:
    """Assemble tout ce que raconte le guide en un seul rapport pret a afficher."""
    df = fetch_pair_history(history_weeks)
    if len(df) < 6:
        return {"available": False, "reason": "Historique COT insuffisant recu depuis le CFTC."}

    latest = df.iloc[-1]

    accel_threshold = float(df["delta_4w"].abs().median())
    percentile = compute_percentile(df["net_pair"], latest["net_pair"])
    interpretation = build_interpretation(
        latest["net_pair"], latest["delta_4w"], percentile, accel_threshold
    )

    min_move_base = 0.03 * (df["long_base"] + df["short_base"]).tail(26).mean()
    min_move_quote = 0.03 * (df["long_quote"] + df["short_quote"]).tail(26).mean()
    flow_base = classify_flow_source(latest["delta_longs_base_4w"], latest["delta_shorts_base_4w"], min_move_base)
    flow_quote = classify_flow_source(latest["delta_longs_quote_4w"], latest["delta_shorts_quote_4w"], min_move_quote)

    divergence = detect_divergence(df)
    lag = lag_warning(latest["date"])

    recent_table = df.tail(8)[["date", "net_pair", "weekly_change", "delta_4w"]].copy()
    recent_table["date"] = recent_table["date"].dt.date.astype(str)

    return {
        "available": True,
        "history": df,
        "as_of": latest["date"].date().isoformat(),
        "net_pair": float(latest["net_pair"]),
        "weekly_change": float(latest["weekly_change"]) if pd.notna(latest["weekly_change"]) else None,
        "delta_4w": float(latest["delta_4w"]) if pd.notna(latest["delta_4w"]) else None,
        "percentile": percentile,
        "history_weeks_used": len(df),
        "net_pair_pct_oi": float(latest["net_pair_pct_oi"]) if pd.notna(latest.get("net_pair_pct_oi")) else None,
        "interpretation": interpretation,
        "flow_base": {"currency": BASE_CCY, "classification": flow_base,
                      "delta_longs_4w": float(latest["delta_longs_base_4w"]) if pd.notna(latest["delta_longs_base_4w"]) else None,
                      "delta_shorts_4w": float(latest["delta_shorts_base_4w"]) if pd.notna(latest["delta_shorts_base_4w"]) else None},
        "flow_quote": {"currency": QUOTE_CCY, "classification": flow_quote,
                       "delta_longs_4w": float(latest["delta_longs_quote_4w"]) if pd.notna(latest["delta_longs_quote_4w"]) else None,
                       "delta_shorts_4w": float(latest["delta_shorts_quote_4w"]) if pd.notna(latest["delta_shorts_quote_4w"]) else None},
        "divergence": divergence,
        "lag_warning": lag,
        "recent_table": recent_table,
    }
