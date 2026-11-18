"""Robot EXÉCUTANT des plans de trading (créé le 2026-11-18, remplace l'idée d'un robot à signaux).

Il n'invente AUCUN trade : il lit les plans écrits lors des analyses (journal/<PAIRE>/suivi/plan_actif.json)
et décide, pour chaque plan, ce qu'il faudrait faire chez le courtier :
  POSER     un ordre en attente (BUY/SELL LIMIT/STOP) au prix du plan, lot calculé pour 0,25 % de l'équité
  RETIRER   un ordre dont le plan est expiré ou invalidé
  ATTENDRE  condition pas encore remplie, annonce forte dans l'heure, volume faible sur la bougie d'armement...
  NE PAS POSER  règle devise cassée (controle_devises), prix déjà au-delà de l'entrée, TP déjà dépassé
  SIGNALER  écart à regarder (ordre hors plan, lot différent du plan, plan en veille dont la condition est remplie)
  RIEN      tout est en ordre

PHASE 1 = OBSERVATION : ce script n'envoie JAMAIS d'ordre (aucun appel order_send). Il écrit ce qu'il ferait.
L'envoi réel sera ajouté seulement après 2-3 semaines d'observation validées par l'utilisatrice.

Règles appliquées :
  - seuls les plans au statut « en_attente » ou « armé » peuvent être posés (« en_veille » = décision humaine) ;
  - conditions jugées sur bougies CLOTURÉES uniquement (D1 ou H4 selon le plan), jamais la bougie en cours ;
  - règles devise par devise 1 à 4 via controle_devises.verdict_nouveau (robots et ordres en attente comptés) ;
  - règle 7 : pas de nouvel ordre dans l'heure avant une annonce forte (calendrier en cache disque, 6 h) ;
  - ordre STOP armé par une clôture à volume sous la moyenne 20 : attendre la clôture suivante (règle du 12/11) ;
  - jamais au marché.

Usage : PYTHONIOENCODING=utf-8 python -u robot_executant.py            (un passage, à lancer toutes les 15 min)
Sorties : journal/_executant/derniere_decision.md, journal/_executant/decisions_<date>.jsonl (seulement les
changements), journal/_executant/robot_executant.log
"""
from __future__ import annotations

import argparse, datetime as dt, json, logging, math, pathlib, sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TERMINAL = r"C:\Program Files\FTMO Global Markets MT5 Terminal\terminal64.exe"
JOURNAL = HERE.parent
OUT = JOURNAL / "_executant"
RISQUE_PCT = 0.25                      # % de l'équité par plan (bouton Magic Panel)
TOL_PRIX = 0.002                       # un ordre « correspond » au plan si son prix est à 0,2 % de l'entrée
CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
TYPES = {("buy", "limit"): 2, ("sell", "limit"): 3, ("buy", "stop"): 4, ("sell", "stop"): 5}
NOM_TYPE = {0: "ACHAT", 1: "VENTE", 2: "BUY LIMIT", 3: "SELL LIMIT", 4: "BUY STOP", 5: "SELL STOP"}
STATUTS_POSABLES = {"en_attente", "armé"}
STATUTS_FINIS = {"annulé", "terminé", "déclenché"}


# ----------------------------------------------------------------------------- fonctions pures (testées)
def cote_ok(side: str, typ: str, entry: float, bid: float, ask: float) -> tuple[bool, str]:
    """Un ordre en attente n'est posable que du bon côté du prix."""
    if typ == "limit":
        ok = ask > entry if side == "buy" else bid < entry
        txt = "le prix est déjà sous l'entrée" if side == "buy" else "le prix est déjà au-dessus de l'entrée"
    else:
        ok = ask < entry if side == "buy" else bid > entry
        txt = "le prix est déjà au-dessus de l'entrée" if side == "buy" else "le prix est déjà sous l'entrée"
    return ok, ("" if ok else txt)


def tp_deja_passe(side: str, tp: float, bid: float, ask: float) -> bool:
    return bid >= tp if side == "buy" else ask <= tp


def lot_pour_risque(risque: float, perte_par_lot: float, step: float, vmin: float, vmax: float) -> float:
    """Lot arrondi VERS LE BAS au pas du courtier (jamais plus de risque que prévu). 0 si trop petit."""
    if perte_par_lot <= 0:
        return 0.0
    lot = math.floor(risque / perte_par_lot / step + 1e-9) * step
    lot = round(min(lot, vmax), 2)
    return lot if lot >= vmin else 0.0


def correspond(pair: str, side: str, typ: str, entry: float, o_symbol: str, o_type: int, o_price: float) -> bool:
    return (o_symbol.upper().startswith(pair) and o_type == TYPES[(side, typ)]
            and abs(o_price - entry) <= TOL_PRIX * entry)


def condition(cond: dict, d1_closed: list[tuple], h4_closed: list[tuple]) -> tuple[bool | None, str, tuple | None]:
    """cond = {"type": ..., "level": ...} ; *_closed = [(time, close, volume, vol_moy20), ...] bougies clôturées
    depuis la création du plan. Retourne (remplie ?, détail, bougie qui l'a remplie)."""
    t = cond.get("type", "none")
    if t == "none":
        return True, "aucune condition", None
    if t.startswith("d1_close") or t.startswith("h4_close"):
        bars = d1_closed if t.startswith("d1") else h4_closed
        below = t.endswith("below")
        lv = cond["level"]
        for b in bars:
            if (b[1] < lv) if below else (b[1] > lv):
                return True, f"clôture {t[:2].upper()} {'sous' if below else 'au-dessus de'} {lv} le {b[0]:%d/%m %H:%M} ({b[1]})", b
        return False, f"pas de clôture {t[:2].upper()} {'sous' if below else 'au-dessus de'} {lv}", None
    return None, f"condition « {t} » non gérée par le robot (jugement humain)", None


def volume_faible(barre: tuple | None, derniere_cloturee: tuple | None) -> bool:
    """Précaution du 12/11 : la bougie qui arme a un volume sous sa moyenne 20 ET c'est encore la dernière
    bougie clôturée (on attend la clôture suivante)."""
    if not barre or not derniere_cloturee or barre[0] != derniere_cloturee[0]:
        return False
    return barre[3] is not None and barre[2] < barre[3]


def annonce_proche(devises: set, maintenant_utc: dt.datetime, evenements: list[dict], minutes: int = 60) -> str | None:
    for e in evenements:
        if e["country"] in devises and e["impact"] == "High":
            quand = dt.datetime.fromisoformat(e["when_utc"])
            if maintenant_utc <= quand <= maintenant_utc + dt.timedelta(minutes=minutes):
                return f"{e['title']} ({e['country']}) à {quand:%H:%M} UTC"
    return None


# ----------------------------------------------------------------------------- accès MT5 / fichiers
def calendrier(log) -> tuple[list[dict], bool]:
    """Calendrier Forex Factory gardé en cache disque 6 h (l'adresse renvoie souvent 429 si on insiste)."""
    f = OUT / "calendrier_cache.json"
    cache = None
    if f.exists():
        try:
            cache = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            cache = None
    frais = cache and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(cache["fetched"])) < dt.timedelta(hours=6)
    if not frais:
        try:
            import requests
            r = requests.get(CAL_URL, timeout=15)
            r.raise_for_status()
            ev = []
            for row in r.json():
                try:
                    w = dt.datetime.fromisoformat(row["date"]).astimezone(dt.timezone.utc)
                except Exception:
                    continue
                ev.append({"title": row.get("title", "?"), "country": row.get("country", ""),
                           "impact": row.get("impact", "Low"), "when_utc": w.isoformat()})
            cache = {"fetched": dt.datetime.now(dt.timezone.utc).isoformat(), "events": ev}
            f.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            log.warning(f"calendrier non rafraîchi ({exc}) ; cache {'gardé' if cache else 'ABSENT'}")
    return (cache["events"], True) if cache else ([], False)


def barres_cloturees(mt5, sym: str, tf, depuis: dt.datetime, n: int) -> list[tuple]:
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    if r is None or len(r) < 2:
        return []
    out = []
    vols = [int(x["tick_volume"]) for x in r]
    for i, x in enumerate(r[:-1]):                       # la dernière bougie est en cours : exclue
        t = dt.datetime.utcfromtimestamp(int(x["time"]))
        moy = sum(vols[i - 20:i]) / 20 if i >= 20 else None
        if t >= depuis:
            out.append((t, float(x["close"]), vols[i], moy))
    return out


def perte_par_lot(mt5, sym, side, entry, sl) -> float:
    p = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL, sym, 1.0, entry, sl)
    return -p if p is not None else 0.0


# ----------------------------------------------------------------------------- décision
def decider(mt5, cd, plan: dict, p: dict, ctx: dict) -> dict:
    pair, side, typ = plan["pair"], p["side"], p["type"]
    entry, sl, tps = p["entry"], p["sl"], p.get("tp") or []
    tp1 = tps[0] if tps else None
    d = {"paire": pair, "plan": p["id"], "statut_plan": p.get("statut"), "type": NOM_TYPE.get(TYPES.get((side, typ)), "?"),
         "entree": entry, "sl": sl, "tp": tp1}
    sym = ctx["symboles"].get(pair, pair)
    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    if info is None or tick is None:
        return {**d, "action": "SIGNALER", "raison": f"symbole {sym} introuvable dans MT5"}
    ordres = [o for o in ctx["ordres"] if correspond(pair, side, typ, entry, o.symbol, o.type, o.price_open)]
    ordre = ordres[0] if ordres else None
    if ordre:
        ctx["ordres_rattaches"].add(ordre.ticket)
        d["ticket"] = ordre.ticket
    srv_date = ctx["srv_now"].date().isoformat()

    if plan.get("valid_until", "9999") < srv_date:
        return {**d, "action": "RETIRER" if ordre else "RIEN",
                "raison": f"plan expiré le {plan['valid_until']}" + ("" if ordre else " (rien de posé)")}

    created = dt.datetime.strptime(plan["created"], "%Y-%m-%d %H:%M")
    d1 = barres_cloturees(mt5, sym, mt5.TIMEFRAME_D1, dt.datetime.fromisoformat(plan["created_server_date"]), 60)
    h4 = barres_cloturees(mt5, sym, mt5.TIMEFRAME_H4, created, 260)

    inv = p.get("invalidation") or {"type": "none"}
    inv_ok, inv_txt, _ = (False, "", None) if inv.get("type", "none") == "none" else condition(inv, d1, h4)
    if inv_ok:
        return {**d, "action": "RETIRER" if ordre else "RIEN",
                "raison": f"plan invalidé : {inv_txt}" + ("" if ordre else " (rien de posé ; à passer en « annulé » au prochain suivi)")}

    cond_ok, cond_txt, barre = condition(p.get("condition", {"type": "none"}), d1, h4)
    statut = p.get("statut")
    if statut not in STATUTS_POSABLES:          # en_veille et autres : décision humaine
        if ordre:
            return {**d, "action": "SIGNALER", "raison": f"un ordre est posé alors que le plan est « {statut} »"}
        if cond_ok and p.get("condition", {}).get("type", "none") != "none":
            ok, txt = cote_ok(side, typ, entry, tick.bid, tick.ask)
            if not ok:
                return {**d, "action": "RIEN", "raison": f"plan « {statut} », condition remplie mais {txt} : non posable"}
            return {**d, "action": "SIGNALER", "raison": f"plan « {statut} » mais sa condition est remplie ({cond_txt}) et le prix le permet : à valider au suivi"}
        return {**d, "action": "RIEN", "raison": f"plan « {statut} » (décision humaine)"}

    if cond_ok is None:
        return {**d, "action": "SIGNALER", "raison": cond_txt}
    if not cond_ok:
        if ordre:
            return {**d, "action": "SIGNALER", "raison": f"ordre posé avant l'armement ({cond_txt})"}
        return {**d, "action": "ATTENDRE", "raison": cond_txt}

    # calcul du lot voulu (0,25 % de l'équité)
    risque_voulu = ctx["equite"] * RISQUE_PCT / 100
    ppl = perte_par_lot(mt5, sym, side, entry, sl)
    lot = lot_pour_risque(risque_voulu, ppl, info.volume_step, info.volume_min, info.volume_max)
    d.update(lot=lot, risque=round(lot * ppl, 2), pct=round(lot * ppl / ctx["equite"] * 100, 2) if ctx["equite"] else None)

    if ordre:                                   # déjà posé : on contrôle seulement
        ecarts = []
        if abs(ordre.volume_current - lot) > info.volume_step + 1e-9:
            r_reel = ordre.volume_current * ppl
            ecarts.append(f"lot {ordre.volume_current} au lieu de {lot} ({r_reel:.0f} $ = {r_reel / ctx['equite'] * 100:.2f} %)")
        if abs(ordre.sl - sl) > 2 * info.point * 10:
            ecarts.append(f"SL {ordre.sl} au lieu de {sl}")
        if tp1 and abs(ordre.tp - tp1) > 2 * info.point * 10:
            ecarts.append(f"TP {ordre.tp} au lieu de {tp1}")
        return {**d, "action": "SIGNALER" if ecarts else "RIEN",
                "raison": ("ordre posé, écart : " + " ; ".join(ecarts)) if ecarts else "ordre déjà posé, conforme au plan"}

    ok, txt = cote_ok(side, typ, entry, tick.bid, tick.ask)
    if not ok:
        return {**d, "action": "NE PAS POSER", "raison": f"{txt} (bid {tick.bid} / ask {tick.ask}) : ne jamais entrer au marché"}
    if tp1 and tp_deja_passe(side, tp1, tick.bid, tick.ask):
        return {**d, "action": "NE PAS POSER", "raison": "TP déjà dépassé par le prix"}
    if lot <= 0:
        return {**d, "action": "SIGNALER", "raison": "lot calculé trop petit (vérifier SL / symbole)"}
    if typ == "stop" and barre and volume_faible(barre, (d1 if p["condition"]["type"].startswith("d1") else h4)[-1]):
        return {**d, "action": "ATTENDRE", "raison": f"bougie d'armement à volume faible ({barre[2]} < moyenne 20 {barre[3]:.0f}) : attendre la clôture suivante"}
    if not ctx["cal_dispo"]:
        return {**d, "action": "ATTENDRE", "raison": "calendrier économique indisponible : prudence (règle 7)"}
    news = annonce_proche({pair[:3], pair[3:6]}, ctx["utc_now"], ctx["evenements"])
    if news:
        return {**d, "action": "ATTENDRE", "raison": f"règle 7 : annonce forte dans l'heure ({news})"}
    verdict = cd.verdict_nouveau(pair, side == "buy", lot * ppl, ctx["table"], ctx["total"])
    if verdict.startswith("INTERDIT"):
        return {**d, "action": "NE PAS POSER", "raison": verdict}
    if verdict.startswith("RÉDUIS"):
        lot2 = lot_pour_risque(cd.DEMI, ppl, info.volume_step, info.volume_min, info.volume_max)
        d.update(lot=lot2, risque=round(lot2 * ppl, 2), pct=round(lot2 * ppl / ctx["equite"] * 100, 2))
    dist = abs(entry - (tick.ask if side == "buy" else tick.bid)) / (info.point * 10)
    return {**d, "action": "POSER",
            "raison": f"{d['type']} {entry} SL {sl} TP {tp1} | {d['lot']} lot = {d['pct']} % (≈ {d['risque']:.0f} $) | prix à {dist:.0f} pip de l'entrée"
                      + (f" | {verdict}" if verdict != "OK" else "")}


# ----------------------------------------------------------------------------- programme
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="(pas encore disponible) envoyer les ordres")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        handlers=[logging.FileHandler(OUT / "robot_executant.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("executant")
    if args.live:
        log.error("--live n'existe pas encore : phase 1 = observation seulement (aucun ordre envoyé).")
        sys.exit(2)

    import MetaTrader5 as mt5
    import controle_devises as cd
    for _ in range(4):
        if mt5.initialize(path=TERMINAL):
            break
        import time; time.sleep(3)
    else:
        log.error(f"MT5 ne répond pas : {mt5.last_error()}"); sys.exit(1)
    try:
        acc = mt5.account_info()
        t = mt5.symbol_info_tick("EURUSD")
        srv_now = dt.datetime.utcfromtimestamp(t.time)
        utc_now = dt.datetime.now(dt.timezone.utc)
        lignes = cd.lignes_compte()
        evenements, cal_dispo = calendrier(log)
        ctx = {"srv_now": srv_now, "utc_now": utc_now, "equite": acc.equity, "ordres": list(mt5.orders_get() or []),
               "table": cd.tableau(lignes), "total": sum(l["risque"] or 0 for l in lignes),
               "evenements": evenements, "cal_dispo": cal_dispo, "ordres_rattaches": set(),
               "symboles": {}}
        decisions = []
        for f in sorted(JOURNAL.glob("*/suivi/plan_actif.json")):
            try:
                plan = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:
                decisions.append({"paire": f.parent.parent.name, "plan": "-", "action": "SIGNALER", "raison": f"plan illisible : {exc}"})
                continue
            for p in plan.get("plans", []):
                if p.get("statut") in STATUTS_FINIS or (p.get("side"), p.get("type")) not in TYPES:
                    continue
                try:
                    decisions.append(decider(mt5, cd, plan, p, ctx))
                except Exception as exc:
                    log.exception(f"{plan.get('pair')} {p.get('id')}")
                    decisions.append({"paire": plan.get("pair"), "plan": p.get("id"), "action": "SIGNALER", "raison": f"erreur : {exc}"})
        for o in ctx["ordres"]:
            if o.ticket not in ctx["ordres_rattaches"]:
                qui = cd.QUI.get(o.magic, f"magic {o.magic}")
                decisions.append({"paire": o.symbol, "plan": "HORS PLAN", "action": "SIGNALER", "ticket": o.ticket,
                                  "raison": f"{NOM_TYPE.get(o.type, o.type)} {o.volume_current} lot @ {o.price_open} (SL {o.sl}, TP {o.tp}) posé par {qui} : aucun plan actif ne correspond"})
        for po in mt5.positions_get() or []:
            decisions.append({"paire": po.symbol, "plan": "POSITION", "action": "RIEN", "ticket": po.ticket,
                              "raison": f"{NOM_TYPE.get(po.type)} {po.volume} lot @ {po.price_open} SL {po.sl} TP {po.tp}, résultat {po.profit:+.2f} {acc.currency} ({cd.QUI.get(po.magic, po.magic)})"})
    finally:
        mt5.shutdown()

    # ------------------------------------------------------------- sorties
    ordre_aff = {"RETIRER": 0, "POSER": 1, "SIGNALER": 2, "NE PAS POSER": 3, "ATTENDRE": 4, "RIEN": 5}
    decisions.sort(key=lambda x: (ordre_aff.get(x["action"], 9), x["paire"], str(x["plan"])))
    etat_f = OUT / "etat.json"
    avant = json.loads(etat_f.read_text(encoding="utf-8")) if etat_f.exists() else {}
    apres, changements = {}, []
    for x in decisions:
        cle = f"{x['paire']}|{x['plan']}|{x.get('ticket', '')}"
        sig = x["action"] + "|" + "".join(c for c in x["raison"].split(" | prix à")[0] if not c.isdigit())
        apres[cle] = sig
        if avant.get(cle) != sig:
            changements.append(x)
    etat_f.write_text(json.dumps(apres, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(OUT / f"decisions_{srv_now:%Y-%m-%d}.jsonl", "a", encoding="utf-8") as fj:
        for x in changements:
            fj.write(json.dumps({"serveur": f"{srv_now:%Y-%m-%d %H:%M}", **x}, ensure_ascii=False, default=str) + "\n")
    for x in changements:
        log.info(f"CHANGEMENT {x['paire']} {x['plan']} -> {x['action']} : {x['raison']}")

    L = [f"# Robot exécutant — dernier passage {srv_now:%d/%m %H:%M} serveur ({(srv_now - dt.timedelta(hours=1)):%H:%M} Paris)",
         "", "**PHASE 1 : OBSERVATION. Aucun ordre n'a été envoyé.** Ce tableau dit ce que le robot ferait.", "",
         f"Compte {acc.login} | équité {acc.equity:,.2f} {acc.currency} | calendrier {'OK' if cal_dispo else 'INDISPONIBLE'}".replace(",", " "), "",
         "| Action | Paire | Plan | Détail |", "|---|---|---|---|"]
    for x in decisions:
        L.append(f"| **{x['action']}** | {x['paire']} | {x['plan']} | {x['raison']} |")
    L += ["", "Rappel : le robot n'invente aucun trade ; il applique les plans du suivi et les règles devise par devise.",
          "Carte de contexte, pas un signal : nos tests n'ont trouvé aucun avantage mécanique."]
    (OUT / "derniere_decision.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    log.info(f"passage terminé : {len(decisions)} décisions, {len(changements)} changement(s)")


if __name__ == "__main__":
    main()
