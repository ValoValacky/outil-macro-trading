"""Moteur de scoring macro : transforme les indicateurs bruts en un score de
force relative par devise.

Principe (voir README.md pour le detail pedagogique) :
- Chaque indicateur macro recoit un score de -3 a +3 selon qu'il est
  favorable ou defavorable a la devise, et selon l'ampleur de sa variation
  recente (momentum), pas seulement son niveau absolu.
- Les scores par indicateur sont ponderes puis additionnes -> score composite
  par devise.
- Le classement des devises (du plus fort au plus faible score) donne la
  lecture "direction probable" pour les paires.

Ceci reste un outil de lecture macro, pas un signal d'execution automatique.
"""

from dataclasses import dataclass, field

import pandas as pd

# Poids relatifs de chaque indicateur dans le score composite.
# Le taux directeur pese le plus lourd (c'est le driver macro le plus direct
# sur le marche des changes), suivi de l'inflation puis de l'activite reelle.
DEFAULT_WEIGHTS = {
    "policy_rate": 0.40,
    "cpi_yoy": 0.25,
    "gdp_growth_yoy": 0.20,
    "unemployment_rate": 0.15,
}


@dataclass
class IndicatorScore:
    indicator: str
    level: float | None
    change: float | None
    score: int
    weight: float
    as_of: str | None = None


@dataclass
class CurrencyScore:
    currency: str
    composite_score: float
    indicators: list[IndicatorScore] = field(default_factory=list)


def _score_policy_rate(change: float | None) -> int:
    """Une banque centrale qui hausse ses taux soutient sa devise (flux de capitaux)."""
    if change is None:
        return 0
    if change >= 0.75:
        return 3
    if change >= 0.25:
        return 2
    if change > 0:
        return 1
    if change == 0:
        return 0
    if change > -0.25:
        return -1
    if change > -0.75:
        return -2
    return -3


def _score_cpi(level: float | None, change: float | None) -> int:
    """Inflation moderee et stable = neutre. Trop haute ou en forte acceleration
    = pression sur la banque centrale a resserrer (positif court terme) mais
    risque de perte de pouvoir d'achat (negatif). On score ici la dynamique
    attendue par les banques centrales : une inflation qui converge vers la
    cible (~2%) est vue positivement ; un ecart qui se creuse est negatif."""
    if level is None:
        return 0
    target = 2.0
    gap = level - target
    momentum = change or 0.0
    # Inflation trop forte ET qui accelere -> tres negatif (perte de confiance)
    if gap > 2 and momentum > 0:
        return -3
    if gap > 2:
        return -2
    if gap > 0.5:
        return -1 if momentum > 0 else 1
    if gap < -1:
        return -1  # trop faible = risque deflationniste
    return 1 if abs(gap) <= 1 else 0


def _score_gdp(level: float | None, change: float | None) -> int:
    if level is None:
        return 0
    if level >= 3:
        return 3
    if level >= 1.5:
        return 2
    if level > 0:
        return 1
    if level == 0:
        return 0
    if level > -1.5:
        return -2
    return -3


def _score_unemployment(change: float | None) -> int:
    """Un chomage qui baisse est positif pour la devise (economie qui se tend)."""
    if change is None:
        return 0
    if change <= -0.5:
        return 2
    if change < 0:
        return 1
    if change == 0:
        return 0
    if change < 0.5:
        return -1
    return -2


def score_currency(
    currency: str,
    policy_rate_summary: dict,
    cpi_summary: dict,
    gdp_summary: dict,
    unemployment_summary: dict,
    weights: dict | None = None,
) -> CurrencyScore:
    weights = weights or DEFAULT_WEIGHTS

    indicators = [
        IndicatorScore(
            indicator="policy_rate",
            level=policy_rate_summary.get("level"),
            change=policy_rate_summary.get("change"),
            score=_score_policy_rate(policy_rate_summary.get("change")),
            weight=weights["policy_rate"],
            as_of=policy_rate_summary.get("as_of"),
        ),
        IndicatorScore(
            indicator="cpi_yoy",
            level=cpi_summary.get("level"),
            change=cpi_summary.get("change"),
            score=_score_cpi(cpi_summary.get("level"), cpi_summary.get("change")),
            weight=weights["cpi_yoy"],
            as_of=cpi_summary.get("as_of"),
        ),
        IndicatorScore(
            indicator="gdp_growth_yoy",
            level=gdp_summary.get("level"),
            change=gdp_summary.get("change"),
            score=_score_gdp(gdp_summary.get("level"), gdp_summary.get("change")),
            weight=weights["gdp_growth_yoy"],
            as_of=gdp_summary.get("as_of"),
        ),
        IndicatorScore(
            indicator="unemployment_rate",
            level=unemployment_summary.get("level"),
            change=unemployment_summary.get("change"),
            score=_score_unemployment(unemployment_summary.get("change")),
            weight=weights["unemployment_rate"],
            as_of=unemployment_summary.get("as_of"),
        ),
    ]

    composite = sum(ind.score * ind.weight for ind in indicators)
    return CurrencyScore(currency=currency, composite_score=round(composite, 2), indicators=indicators)


def build_ranking(currency_scores: list[CurrencyScore]) -> pd.DataFrame:
    """Classement des devises du plus fort au plus faible score composite."""
    rows = [
        {"currency": cs.currency, "score": cs.composite_score}
        for cs in currency_scores
    ]
    df = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    df.index = df.index + 1  # rang 1 = plus fort
    df.index.name = "rank"
    return df


def build_pair_matrix(currency_scores: list[CurrencyScore]) -> pd.DataFrame:
    """Matrice de biais directionnel pour chaque paire (score_base - score_quote).
    Positif = biais haussier sur la paire BASE/QUOTE, negatif = biais baissier."""
    scores = {cs.currency: cs.composite_score for cs in currency_scores}
    currencies = list(scores.keys())
    matrix = pd.DataFrame(index=currencies, columns=currencies, dtype=float)
    for base in currencies:
        for quote in currencies:
            matrix.loc[base, quote] = round(scores[base] - scores[quote], 2)
    return matrix


def build_detail_table(currency_scores: list[CurrencyScore]) -> pd.DataFrame:
    """Table detaillee : un indicateur par ligne, une devise par colonne (score brut)."""
    rows = {}
    for cs in currency_scores:
        for ind in cs.indicators:
            rows.setdefault(ind.indicator, {})[cs.currency] = ind.score
    return pd.DataFrame(rows).T
