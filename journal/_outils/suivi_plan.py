"""Suivi quotidien d'un plan de trading (lundi -> vendredi).

Usage :  python suivi_plan.py [PAIRE]          (defaut EURUSD)
Lit journal/<PAIRE>/suivi/plan_actif.json (le plan ecrit lors de l'analyse), interroge MT5 en LECTURE SEULE
(aucun ordre), rejoue le prix depuis la creation du plan et dit factuellement :
  - ce que le prix a fait (touche-t-il les niveaux ? les conditions d'armement / d'invalidation sont-elles remplies ?)
  - ce que chaque plan A/B aurait donne (simulation M15, entree au niveau, SL pris avant TP si meme bougie)
  - l'etat reel : positions, ordres en attente, resultats du jour
  - les evenements des prochaines 30 h et le COT
Le jugement (confirmer / modifier / annuler) et la reecriture du plan sont faits ensuite par l'assistant.
Heures : toutes en heure SERVEUR MT5 (UTC+2 en hiver, UTC+3 en ete).
"""
import sys, json, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
BOT_SRC = ROOT.parent / "Bot Trading FTMO" / "src"
TERMINAL = r"C:\Program Files\FTMO Global Markets MT5 Terminal\terminal64.exe"
COT_DIR = Path(r"C:\Users\Tina\AppData\Roaming\MetaQuotes\Terminal\Common\Files\COT")

pair = (sys.argv[1] if len(sys.argv) > 1 else "EURUSD").upper()
plan_path = ROOT / "journal" / pair / "suivi" / "plan_actif.json"
if not plan_path.exists():
    sys.exit(f"Pas de plan actif pour {pair} : {plan_path} introuvable. Il faut d'abord une analyse du lundi (plan_actif.json).")
plan = json.loads(plan_path.read_text(encoding="utf-8"))
PIP = plan.get("pip", 0.0001)
created = dt.datetime.strptime(plan["created"], "%Y-%m-%d %H:%M")

import MetaTrader5 as mt5
for attempt in range(4):
    if mt5.initialize(path=TERMINAL): break
    import time; time.sleep(3)
else:
    sys.exit("MT5 ne repond pas (reessaie dans une minute).")

def to_df(rates):
    df = pd.DataFrame(rates); df["time"] = pd.to_datetime(df["time"], unit="s"); return df.set_index("time")

tick = mt5.symbol_info_tick(pair)
srv_now = dt.datetime.utcfromtimestamp(tick.time)                       # etiquette serveur
off_h = round((srv_now - dt.datetime.utcnow()).total_seconds() / 3600)  # decalage serveur / UTC
m15 = to_df(mt5.copy_rates_from_pos(pair, mt5.TIMEFRAME_M15, 0, 1500))
d1 = to_df(mt5.copy_rates_from_pos(pair, mt5.TIMEFRAME_D1, 0, 40))
h4 = to_df(mt5.copy_rates_from_pos(pair, mt5.TIMEFRAME_H4, 0, 200))
closed4 = h4[(h4.index >= created) & (h4.index < h4.index[-1])]   # bougies H4 cloturees depuis le plan
bid, ask = tick.bid, tick.ask
today = d1.index[-1]
print(f"=== SUIVI {pair} | plan v{plan['version']} cree le {plan['created']} (serveur) | maintenant {srv_now:%a %d/%m %H:%M} serveur (UTC{off_h:+d}) | validite jusqu'au {plan['valid_until']} ===")
print(f"Biais du plan : {plan['bias']}")
print(f"Prix : bid {bid:.5f} / ask {ask:.5f} (spread {(ask - bid) / PIP:.1f} pip)\n")

# ---------------- 1. ce que le prix a fait -----------------
since = m15[m15.index >= created]
if len(since) == 0:
    since = m15.iloc[-1:]   # plan cree a l'instant : une seule barre pour eviter les tableaux vides
print("--- 1. Le prix depuis la creation du plan ---")
print(f"Plus haut {since.high.max():.5f} ({since.high.idxmax():%a %H:%M}) | plus bas {since.low.min():.5f} ({since.low.idxmin():%a %H:%M}) | etendue {(since.high.max() - since.low.min()) / PIP:.0f} pip")
d1c = d1.copy(); d1c["atr"] = (pd.concat([d1.high - d1.low, (d1.high - d1.close.shift()).abs(), (d1.low - d1.close.shift()).abs()], axis=1).max(axis=1)).rolling(14).mean()
atr = d1c.atr.iloc[-2] / PIP
closed = d1[(d1.index >= pd.Timestamp(plan["created_server_date"])) & (d1.index < today)]
print(f"ATR D1 (14) = {atr:.0f} pip")
print("Bougies D1 CLOTUREES depuis le plan :")
for t, r in closed.iterrows():
    print(f"   {t:%a %d/%m} : O {r.open:.5f}  H {r.high:.5f}  L {r.low:.5f}  C {r.close:.5f}  ({(r.close - r.open) / PIP:+.0f} pip, etendue {(r.high - r.low) / PIP:.0f})")
tr = d1.iloc[-1]
print(f"Bougie D1 du jour (en cours) : O {tr.open:.5f}  H {tr.high:.5f}  L {tr.low:.5f}  dernier {bid:.5f} | mouvement {(bid - tr.open) / PIP:+.0f} pip, etendue {(tr.high - tr.low) / PIP:.0f} pip ({(tr.high - tr.low) / PIP / atr * 100:.0f} % de l'ATR)")
print("Distance aux niveaux du plan (pip, + = niveau au-dessus du prix) :")
for k, v in plan["levels"].items(): print(f"   {k:16s} {v:.5f}  {(v - bid) / PIP:+.0f}")

# ---------------- 2. conditions et plans -----------------
def cond_met(c):
    """(vrai/faux, detail) pour les conditions d'armement / d'invalidation."""
    t = c["type"]
    if t == "d1_close_below":
        hit = closed[closed.close < c["level"]]
        return (len(hit) > 0, f"clôture D1 sous {c['level']} : " + (f"OUI le {hit.index[0]:%d/%m} ({hit.close.iloc[0]:.5f})" if len(hit) else "non"))
    if t == "d1_close_above":
        hit = closed[closed.close > c["level"]]
        return (len(hit) > 0, f"clôture D1 au-dessus de {c['level']} : " + (f"OUI le {hit.index[0]:%d/%m} ({hit.close.iloc[0]:.5f})" if len(hit) else "non"))
    if t in ("h4_close_below", "h4_close_above"):
        below = t.endswith("below")
        hit = closed4[closed4.close < c["level"]] if below else closed4[closed4.close > c["level"]]
        return (len(hit) > 0, f"clôture H4 {'sous' if below else 'au-dessus de'} {c['level']} : " + (f"OUI le {hit.index[0]:%d/%m %H:%M} ({hit.close.iloc[0]:.5f})" if len(hit) else "non"))
    if t == "none":
        return (True, "aucune condition")
    if t == "no_position":
        pos = [p for p in (mt5.positions_get(symbol=pair) or []) if p.magic == c["magic"]]
        return (len(pos) == 0, f"aucune position du robot {c['magic']} : " + ("OUI (fermée)" if not pos else f"NON, {len(pos)} position(s) ouverte(s) ({pos[0].volume} lot)"))
    return (None, f"condition inconnue {t}")

def simulate(p, arm_time):
    """Rejoue M15 : remplissage au niveau d'entree, puis SL/TP (SL d'abord si les deux dans la meme bougie)."""
    bars = since[since.index >= arm_time] if arm_time is not None else since
    side, e, sl, tps = p["side"], p["entry"], p["sl"], p["tp"]
    filled = None
    for t, b in bars.iterrows():
        if filled is None:
            if p["type"] == "limit": touched = (b.low <= e) if side == "buy" else (b.high >= e)
            else: touched = (b.high >= e) if side == "buy" else (b.low <= e)
            if touched:
                filled = t
            else: continue
        # apres remplissage (y compris dans la meme bougie, de facon conservatrice)
        hit_sl = (b.low <= sl) if side == "buy" else (b.high >= sl)
        hit_tp = [(b.high >= tp) if side == "buy" else (b.low <= tp) for tp in tps]
        if hit_sl: return filled, "SL", t
        if hit_tp[0]: return filled, "TP1", t
    return filled, None, None

print("\n--- 2. Les plans, un par un ---")
for p in plan["plans"]:
    if p["statut"] == "annulé":
        print(f"\n[Plan {p['id']}] {p['nom']} : ANNULÉ ({p.get('note_v2') or p.get('note_v3') or ''})"); continue
    print(f"\n[Plan {p['id']}] {p['nom']} : {p['side'].upper()} {p['type'].upper()} {p['entry']:.5f} | SL {p['sl']:.5f} | TP {', '.join(f'{x:.4f}' for x in p['tp'])} | lot {p.get('lot_texte', str(p['lot_800eur']) + ' (800 EUR)')} | statut enregistre : {p['statut']}")
    ok, det = cond_met(p["condition"]); print(f"   condition d'armement ({p['condition_texte']}) -> {det}")
    iv, det2 = cond_met(p["invalidation"]); print(f"   invalidation ({p['invalidation_texte']}) -> {det2}")
    arm = None
    if p["condition"]["type"].startswith("d1_close") and ok:
        c = p["condition"]; hit = closed[closed.close < c["level"]] if "below" in c["type"] else closed[closed.close > c["level"]]
        arm = hit.index[0] + pd.Timedelta(days=1)       # armee a la clôture de la bougie D1, donc active le lendemain
    if p["condition"]["type"].startswith("h4_close") and ok:
        c = p["condition"]; hit = closed4[closed4.close < c["level"]] if "below" in c["type"] else closed4[closed4.close > c["level"]]
        arm = hit.index[0] + pd.Timedelta(hours=4)
    if p["condition"]["type"] == "no_position" and not ok: arm = srv_now + pd.Timedelta(days=3650)   # pas armee
    if (p["condition"]["type"].startswith("d1_close") or p["condition"]["type"].startswith("h4_close")) and not ok:
        print("   simulation : plan NON ARMÉ (condition pas remplie) -> aucune entrée théorique"); continue
    if arm is not None and arm > srv_now:
        print(f"   simulation : plan pas encore actif (armé le {arm:%d/%m %H:%M}) ou condition non remplie -> aucune entrée théorique")
        continue
    f, res, tt = simulate(p, arm)
    if f is None: print(f"   simulation : niveau d'entrée {p['entry']:.5f} NON atteint depuis la création (le plus proche : {since.low.min() if p['side'] == 'buy' else since.high.max():.5f}, à {abs((since.low.min() if p['side'] == 'buy' else since.high.max()) - p['entry']) / PIP:.0f} pip)")
    elif res is None: print(f"   simulation : ENTRÉE théorique {f:%a %d/%m %H:%M} à {p['entry']:.5f}, position encore ouverte, résultat flottant {((bid if p['side'] == 'buy' else -ask) - (p['entry'] if p['side'] == 'buy' else -p['entry'])) / PIP:+.0f} pip")
    else: print(f"   simulation : entrée {f:%a %d/%m %H:%M} puis {res} à {tt:%a %d/%m %H:%M}")

# ---------------- 3. etat reel -----------------
print("\n--- 3. Etat reel du compte sur la paire ---")
acc = mt5.account_info(); print(f"Compte {acc.login} | solde {acc.balance:.2f} | equite {acc.equity:.2f} {acc.currency}")
pos = list(mt5.positions_get(symbol=pair) or []); orders = list(mt5.orders_get(symbol=pair) or [])
for p in pos:
    ty = "BUY" if p.type == 0 else "SELL"
    print(f"POSITION {ty} {p.volume} lot @ {p.price_open:.5f} | SL {p.sl:.5f} | TP {p.tp:.5f} | P&L {p.profit:+.2f} EUR | magic {p.magic} ({p.comment}) | ticket {p.ticket} | ouverte {dt.datetime.utcfromtimestamp(p.time):%a %d/%m %H:%M}")
    tp_d = abs(p.tp - bid) / PIP if p.tp else None; sl_d = abs(p.sl - bid) / PIP if p.sl else None
    print(f"   distance : SL {sl_d:.0f} pip, TP {tp_d:.0f} pip" if sl_d and tp_d else "   (SL ou TP absent)")
if not pos: print("Aucune position ouverte sur la paire.")
for o in orders: print(f"ORDRE EN ATTENTE type {o.type} {o.volume_current} lot @ {o.price_open:.5f} SL {o.sl:.5f} TP {o.tp:.5f} magic {o.magic} ({o.comment})")
for sp in plan.get("positions_suivies", []):
    if not any(p.ticket == sp["ticket"] for p in pos):
        deals = mt5.history_deals_get(position=sp["ticket"]) or []
        outs = [d for d in deals if d.entry == 1]
        print(f"Position suivie {sp['ticket']} : " + (f"FERMÉE le {dt.datetime.utcfromtimestamp(outs[-1].time):%a %d/%m %H:%M} à {outs[-1].price:.5f}, résultat {sum(d.profit + d.commission + d.swap for d in deals):+.2f} EUR (raison {outs[-1].reason}, commentaire '{outs[-1].comment}')" if outs else "introuvable dans l'historique"))
    else:
        cur = [p for p in pos if p.ticket == sp["ticket"]][0]
        chg = []
        if abs(cur.sl - sp["sl"]) > 1e-6: chg.append(f"SL modifié {sp['sl']} -> {cur.sl}")
        if abs(cur.tp - sp["tp"]) > 1e-6: chg.append(f"TP modifié {sp['tp']} -> {cur.tp}")
        print(f"Position suivie {sp['ticket']} : toujours ouverte" + (" | " + ", ".join(chg) if chg else " | SL/TP inchangés"))
deals = [d for d in (mt5.history_deals_get(created - dt.timedelta(hours=2), srv_now + dt.timedelta(days=1)) or []) if d.symbol == pair]
if deals: print("Transactions sur la paire depuis le plan : " + " ; ".join(f"{dt.datetime.utcfromtimestamp(d.time):%a %H:%M} {'entrée' if d.entry == 0 else 'sortie'} {d.volume} @ {d.price:.5f}" for d in deals))

# ---------------- 4. evenements et COT -----------------
print("\n--- 4. Evenements (heure serveur) et COT ---")
try:
    sys.path.insert(0, str(BOT_SRC)); import news_calendar as nc
    utcnow = dt.datetime.now(dt.timezone.utc)
    ccy = {pair[:3], pair[3:]}
    ev = [e for e in nc.fetch_calendar_events() if e.country in ccy and e.impact in ("High", "Medium", "Holiday") and utcnow - dt.timedelta(hours=8) <= e.when_utc <= utcnow + dt.timedelta(hours=34)]
    for e in sorted(ev, key=lambda e: e.when_utc):
        st = (e.when_utc + dt.timedelta(hours=off_h)).replace(tzinfo=None)
        print(f"   {st:%a %d/%m %H:%M} {e.country} {e.impact:7s} {e.title}  ({'PASSÉ' if e.when_utc < utcnow else 'à venir'})")
    if not ev: print("   (aucun événement notable sur les 8 h passées / 34 h à venir)")
except Exception as exc:
    print(f"   calendrier indisponible : {exc}")
for c in sorted(ccy):
    f = COT_DIR / f"{c}.csv"
    if f.exists(): print(f"   COT {c} : dernier rapport au {f.read_text().splitlines()[0].split(';')[0]}")
print("   (plan écrit avec le COT du 27/10 ; un nouveau rapport arrive en général le vendredi soir ou samedi)")
print("\nÉvénements prévus dans le plan :", " | ".join(f"{e['quand_serveur']} {e['texte']}" for e in plan.get("evenements", [])))
print("Règles du plan :", plan.get("regles", ""))
print("Journal du plan :", " ; ".join(f"{j['date']} v{j['version']} {j['decision']}" for j in plan["journal"]))
mt5.shutdown()
