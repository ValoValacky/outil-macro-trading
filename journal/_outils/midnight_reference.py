"""Point de départ du garde-fou = compte à MINUIT HEURE DE PRAGUE, reconstruit depuis l'historique MT5.

Pourquoi (2026-11-05) : l'utilisatrice éteint son PC chaque nuit, donc aucun robot ne tourne à minuit
de Prague. Au démarrage du matin, le premier robot lancé prenait soit l'équité du moment (Bot Trading
FTMO, Gold Watch), soit le solde de départ fixe 160 000 (HORIIZON, Scanner HA) : jamais le vrai chiffre.
Le 05/11 le PC est resté en veille et s'est réveillé à 03h05 : la référence est devenue 160 000.

Calcul :
  solde à minuit  = solde actuel - (profit + commission + swap + frais) de TOUTES les transactions
                    passées depuis minuit de Prague ;
  équité à minuit ≈ solde à minuit + latent, au prix de clôture M1 de minuit, des positions qui
                    étaient ouvertes à minuit (encore ouvertes ou fermées depuis) ;
  référence       = la plus haute des deux (prudent : on ne sous-estime jamais la perte du jour).

Renvoie None si le calcul est impossible (marché fermé, horloge serveur non mesurable, erreur MT5) :
l'appelant garde alors son ancien comportement.
Fichier identique copié dans HORIIZON/horiizon, LES SCRIPTS/HA_Ichimoku_BB_Swing_H4 et (19/11/2026) journal/_outils (pas d'import
entre projets).
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5

FTMO_TZ = ZoneInfo("Europe/Prague")
_CLOCK_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD")


def _server_offset_seconds() -> int | None:
    """Décalage horloge serveur MT5 - UTC, mesuré sur le dernier tick (arrondi à l'heure).
    None si aucun tick récent (marché fermé) : on ne devine pas."""
    now = _time.time()
    latest = None
    for sym in _CLOCK_SYMBOLS:
        tick = mt5.symbol_info_tick(sym)
        if tick is not None and tick.time:
            latest = tick.time if latest is None else max(latest, tick.time)
    if latest is None:
        return None
    raw = latest - now
    offset = round(raw / 3600) * 3600
    if abs(raw - offset) > 600 or abs(offset) > 14 * 3600:
        return None  # tick trop vieux ou incohérent
    return int(offset)


def _midnight_price(symbol: str, midnight_server_epoch: int) -> float | None:
    rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M1,
                                datetime.fromtimestamp(midnight_server_epoch - 1, tz=timezone.utc), 1)  # bougie qui FINIT a minuit
    if rates is None or len(rates) == 0:
        return None
    return float(rates[-1]["close"])


def midnight_reference(prague_day: date, logger=None) -> dict | None:
    """Reconstruit le compte à 00:00 Prague du jour `prague_day`. MT5 doit être connecté."""
    try:
        acc = mt5.account_info()
        offset = _server_offset_seconds()
        for _ in range(6):  # au démarrage du PC, le terminal peut mettre un moment à recevoir des ticks
            if offset is not None:
                break
            _time.sleep(10)
            acc = mt5.account_info()
            offset = _server_offset_seconds()
        if acc is None or offset is None:
            if logger:
                logger.warning("Référence de minuit : horloge serveur ou compte illisible - ancien calcul gardé.")
            return None
        midnight_utc = datetime.combine(prague_day, time(0), tzinfo=FTMO_TZ).timestamp()
        midnight_srv = int(midnight_utc) + offset

        date_from = datetime.fromtimestamp(midnight_srv - 3600, tz=timezone.utc)
        date_to = datetime.fromtimestamp(int(_time.time()) + offset + 86400, tz=timezone.utc)
        deals = mt5.history_deals_get(date_from, date_to)
        if deals is None:
            if logger:
                logger.warning(f"Référence de minuit : historique MT5 illisible {mt5.last_error()} - ancien calcul gardé.")
            return None
        after = [d for d in deals if d.time >= midnight_srv]
        realised = sum(d.profit + d.commission + d.swap + d.fee for d in after)
        balance_mid = acc.balance - realised

        # Positions ouvertes à minuit : volume à minuit = volume actuel + volumes sortis depuis minuit.
        out_volume: dict[int, float] = {}
        for d in after:
            if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
                out_volume[d.position_id] = out_volume.get(d.position_id, 0.0) + d.volume
        legs: dict[int, tuple] = {}  # position_id -> (symbol, type 0=buy/1=sell, volume, prix d'ouverture)
        for p in mt5.positions_get() or []:
            if p.time < midnight_srv:
                legs[p.ticket] = (p.symbol, p.type, p.volume + out_volume.get(p.ticket, 0.0), p.price_open)
        for pid, vol in out_volume.items():
            if pid in legs:
                continue
            hist = mt5.history_deals_get(position=pid) or []
            entry = next((d for d in hist if d.entry == mt5.DEAL_ENTRY_IN), None)
            if entry is not None and entry.time < midnight_srv:
                legs[pid] = (entry.symbol, entry.type, vol, entry.price)

        floating = 0.0
        missing = []
        for symbol, side, vol, price_open in legs.values():
            px = _midnight_price(symbol, midnight_srv)
            order_type = mt5.ORDER_TYPE_BUY if side == mt5.DEAL_TYPE_BUY else mt5.ORDER_TYPE_SELL
            pnl = mt5.order_calc_profit(order_type, symbol, vol, price_open, px) if px is not None else None
            if pnl is None:
                missing.append(symbol)
                continue
            floating += pnl
        equity_mid = balance_mid + floating
        reference = max(balance_mid, equity_mid)
        info = {
            "reference": round(reference, 2),
            "balance_midnight": round(balance_mid, 2),
            "equity_midnight_est": round(equity_mid, 2),
            "deals_since_midnight": len(after),
            "positions_at_midnight": len(legs),
            "missing_prices": missing,
            "server_offset_h": offset / 3600,
        }
        if logger:
            logger.info(
                f"Référence de minuit (Prague {prague_day}) reconstruite depuis MT5 : {info['reference']:.2f} "
                f"(solde {info['balance_midnight']:.2f}, équité estimée {info['equity_midnight_est']:.2f}, "
                f"{info['deals_since_midnight']} transaction(s) depuis minuit, "
                f"{info['positions_at_midnight']} position(s) ouvertes à minuit"
                + (f", prix manquant : {missing}" if missing else "") + ")."
            )
        return info
    except Exception as exc:  # le garde-fou ne doit jamais planter à cause de ce calcul
        if logger:
            logger.warning(f"Référence de minuit : calcul impossible ({exc}) - ancien calcul gardé.")
        return None


def day_start_provider(logger=None):
    """Fonction à brancher sur le garde-fou : appelée au changement de jour (PC resté allumé
    ou réveillé de veille après minuit)."""
    def provider(prague_day: date):
        info = midnight_reference(prague_day, logger)
        return None if info is None else info["reference"]
    return provider
