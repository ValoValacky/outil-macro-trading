"""Détection de structure de marché : swings, ATR, Break of Structure (BOS) et Change of Character (CHoCH).

Copie vendue (verbatim) de `Bot Trading FTMO/src/market_structure.py`, utilisée par
`leveraged_funds_eurusd.py` pour la divergence prix/COT. Dupliquée plutôt qu'importée
depuis l'autre projet : Streamlit Community Cloud ne déploie QUE ce dépôt GitHub
(outil-macro-trading), le dossier "Bot Trading FTMO" n'existe pas à côté sur la machine
du cloud - un import cross-dossier qui marche en local casse silencieusement en
production (ModuleNotFoundError constaté le 2026-08-31, même piège que cot_report.py).

Si le fichier source évolue côté Bot Trading FTMO, reporter les changements ici à la main.

Approche "price action" classique (fractals + état HH/HL/LH/LL), volontairement
sans order blocks / FVG — plus simple à valider statistiquement en backtest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd


class SwingType(Enum):
    HIGH = "high"
    LOW = "low"


class Bias(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    UNDEFINED = "undefined"


class EventType(Enum):
    BOS = "BOS"        # continuation dans le sens du biais
    CHOCH = "CHOCH"     # rupture à contre-biais -> retournement potentiel


@dataclass
class Swing:
    time: pd.Timestamp
    price: float
    type: SwingType
    label: str = ""  # "HH" / "LH" / "HL" / "LL" une fois classifié


@dataclass
class StructureEvent:
    time: pd.Timestamp
    event_type: EventType
    direction: Bias
    level: float


@dataclass
class StructureAnalysis:
    swings: list[Swing] = field(default_factory=list)
    events: list[StructureEvent] = field(default_factory=list)

    @property
    def bias(self) -> Bias:
        return self.events[-1].direction if self.events else Bias.UNDEFINED

    def last_swing(self, swing_type: SwingType, before: pd.Timestamp | None = None) -> Swing | None:
        candidates = [s for s in self.swings if s.type == swing_type and (before is None or s.time < before)]
        return candidates[-1] if candidates else None


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _find_raw_swings(df: pd.DataFrame, lookback: int) -> list[Swing]:
    """Version vectorisée (rolling window) — équivalente en pratique à une comparaison
    bougie par bougie, mais des dizaines de fois plus rapide sur de gros historiques.
    Les rares égalités exactes (plusieurs bougies au même prix pile) sont toutes retenues
    plutôt qu'exclues ; sans incidence en pratique sur des prix réels, et de toute façon
    nettoyées ensuite par _consolidate_alternating.
    """
    highs, lows = df["high"], df["low"]
    window = 2 * lookback + 1
    roll_max = highs.rolling(window, center=True, min_periods=window).max()
    roll_min = lows.rolling(window, center=True, min_periods=window).min()

    is_high = (highs == roll_max).to_numpy()
    is_low = (lows == roll_min).to_numpy()

    swings: list[Swing] = []
    idx = df.index
    for i in np.flatnonzero(is_high):
        swings.append(Swing(idx[i], float(highs.iloc[i]), SwingType.HIGH))
    for i in np.flatnonzero(is_low):
        swings.append(Swing(idx[i], float(lows.iloc[i]), SwingType.LOW))
    swings.sort(key=lambda s: s.time)
    return swings


def _consolidate_alternating(swings: list[Swing]) -> list[Swing]:
    """Ne garde que le swing le plus extrême parmi des swings consécutifs de même type."""
    consolidated: list[Swing] = []
    for s in swings:
        if consolidated and consolidated[-1].type == s.type:
            prev = consolidated[-1]
            more_extreme = (s.type == SwingType.HIGH and s.price > prev.price) or (
                s.type == SwingType.LOW and s.price < prev.price
            )
            if more_extreme:
                consolidated[-1] = s
        else:
            consolidated.append(s)
    return consolidated


def _drop_insignificant_swings(swings: list[Swing], atr: pd.Series, min_mult: float) -> list[Swing]:
    if not swings:
        return swings
    filtered = [swings[0]]
    for s in swings[1:]:
        prev = filtered[-1]
        atr_val = atr.asof(s.time)
        leg_size = abs(s.price - prev.price)
        if pd.notna(atr_val) and leg_size < min_mult * atr_val:
            continue
        filtered.append(s)
    return filtered


def _classify_labels(swings: list[Swing]) -> None:
    last_high: Swing | None = None
    last_low: Swing | None = None
    for s in swings:
        if s.type == SwingType.HIGH:
            s.label = "H" if last_high is None else ("HH" if s.price > last_high.price else "LH")
            last_high = s
        else:
            s.label = "L" if last_low is None else ("HL" if s.price > last_low.price else "LL")
            last_low = s


def _detect_events(swings: list[Swing]) -> list[StructureEvent]:
    events: list[StructureEvent] = []
    bias = Bias.UNDEFINED
    for s in swings:
        if s.label in ("H", "L"):
            continue  # premier swing de son type, pas encore classifiable

        if bias is Bias.UNDEFINED:
            if s.label == "HH" or s.label == "HL":
                bias = Bias.BULLISH
            elif s.label == "LH" or s.label == "LL":
                bias = Bias.BEARISH
            continue

        if bias is Bias.BULLISH:
            if s.type is SwingType.LOW and s.label == "LL":
                bias = Bias.BEARISH
                events.append(StructureEvent(s.time, EventType.CHOCH, bias, s.price))
            elif s.type is SwingType.HIGH and s.label == "HH":
                events.append(StructureEvent(s.time, EventType.BOS, bias, s.price))
        elif bias is Bias.BEARISH:
            if s.type is SwingType.HIGH and s.label == "HH":
                bias = Bias.BULLISH
                events.append(StructureEvent(s.time, EventType.CHOCH, bias, s.price))
            elif s.type is SwingType.LOW and s.label == "LL":
                events.append(StructureEvent(s.time, EventType.BOS, bias, s.price))
    return events


def analyze_structure(
    df: pd.DataFrame,
    swing_lookback: int = 5,
    atr_period: int = 14,
    min_swing_size_atr_mult: float = 0.5,
) -> StructureAnalysis:
    """df doit avoir un index temporel croissant et les colonnes open/high/low/close."""
    atr = compute_atr(df, atr_period)
    swings = _find_raw_swings(df, swing_lookback)
    swings = _consolidate_alternating(swings)
    swings = _drop_insignificant_swings(swings, atr, min_swing_size_atr_mult)
    swings = _consolidate_alternating(swings)  # re-consolide après filtrage
    _classify_labels(swings)
    events = _detect_events(swings)
    return StructureAnalysis(swings=swings, events=events)
