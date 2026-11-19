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

PHASE 2 (19/11/2026, demandée par l'utilisatrice après l'arrêt des 5 robots à signaux) — reprend la bonne
mécanique des anciens robots, PAS leurs signaux :
  - REPOSER : ordre posé dont le lot / SL / TP ne correspond plus au plan (cas GBPNZD : SL déplacé, lot gardé
    -> 0,35 % au lieu de 0,25 %) -> retirer puis reposer au bon lot ;
  - plans exclusifs (« jamais en même temps que A ») vérifiés, y compris pour les plans en veille ;
  - stop minuscule refusé (< 0,5 ATR D1 : cause des 27,32 lots du structure-bot), SL < 1,2 ATR signalé ;
  - spread > 10 % de la distance au stop -> attendre (garde-fou d'exécution des robots) ;
  - garde-fou du jour : référence à minuit de Prague recalculée (midnight_reference.py) ; à 40 % du seuil
    (3 000 $) plus aucun nouvel ordre, au seuil -> retirer les ordres en attente ;
  - vendredi : aucun nouvel ordre après 20h00 serveur (gap du week-end) ;
  - modes : (défaut) observation ; --essai = chaque action passée à mt5.order_check (vérifiée par le courtier,
    RIEN n'est envoyé) ; --live = envoi réel, VERROUILLÉ tant que _executant/autorisation.json ne contient pas
    {"envoi_reel": true} écrit par l'utilisatrice après la période d'observation.

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
MAGIC = 20260910                       # ordres posés par le robot exécutant (distinct de Magic Panel 20260900)
SL_MIN_ATR, SL_CONSEIL_ATR = 0.5, 1.2  # stop minuscule refusé / règle SL du 12/11
SPREAD_MAX_FRAC = 0.10                 # spread max = 10 % de la distance au stop
SEUIL_JOUR = 3000                      # perte du jour : seuil du garde-fou (60 % de la limite FTMO 5 000 $)
ALERTE_JOUR = 0.40                     # à 40 % du seuil : plus de nouvel ordre
VENDREDI_STOP_H = 20                   # vendredi, heure serveur : plus de nouvel ordre après
AUTORISATION = "autorisation.json"


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


def exclusifs(p: dict) -> set[str]:
    """Plans de la même paire qui ne doivent jamais coexister avec p : champ « exclusif_avec » ou texte
    « jamais en même temps que A » (lot_texte / condition_texte)."""
    import re
    out = set(p.get("exclusif_avec") or [])
    txt = " ".join(str(p.get(k, "")) for k in ("lot_texte", "condition_texte", "nom"))
    for m in re.finditer(r"jamais en même temps que ([A-Z]\w*)", txt, flags=re.IGNORECASE):
        out.add(m.group(1).rstrip(".,;"))
    out.discard(p.get("id"))
    return out


def juge_sl(dist: float, atr_d1: float | None) -> tuple[str, float | None]:
    """'refus' (stop minuscule), 'court' (< 1,2 ATR : à vérifier), 'ok'. Renvoie aussi la distance en ATR."""
    if not atr_d1 or atr_d1 <= 0:
        return "ok", None
    x = dist / atr_d1
    if x < SL_MIN_ATR:
        return "refus", x
    if x < SL_CONSEIL_ATR:
        return "court", x
    return "ok", x


def ecarts_ordre(lot_ordre: float, lot_plan: float, step: float, sl_ordre: float, sl_plan: float,
                 tp_ordre: float, tp_plan: float | None, tol_prix: float) -> tuple[list[str], bool]:
    """Écarts entre un ordre posé et son plan. Renvoie (liste, faut_reposer). On repose si le lot dépasse le plan
    (risque trop gros) ou si le SL/TP diffère ; un lot plus petit est seulement signalé."""
    e, reposer = [], False
    if lot_ordre > lot_plan + step + 1e-9:
        e.append(f"lot {lot_ordre} au lieu de {lot_plan} (risque trop gros)"); reposer = True
    elif lot_ordre < lot_plan - step - 1e-9:
        e.append(f"lot {lot_ordre} au lieu de {lot_plan} (risque plus petit que prévu)")
    if abs(sl_ordre - sl_plan) > tol_prix:
        e.append(f"SL {sl_ordre} au lieu de {sl_plan}"); reposer = True
    if tp_plan and abs(tp_ordre - tp_plan) > tol_prix:
        e.append(f"TP {tp_ordre} au lieu de {tp_plan}"); reposer = True
    return e, reposer


def garde_jour(perte: float | None) -> str:
    """'ok' / 'bloque' (plus de nouvel ordre) / 'stop' (retirer les ordres en attente)."""
    if perte is None:
        return "bloque"                      # référence inconnue : prudence
    if perte >= SEUIL_JOUR:
        return "stop"
    if perte >= ALERTE_JOUR * SEUIL_JOUR:
        return "bloque"
    return "ok"


def envoi_autorise(dossier: pathlib.Path) -> tuple[bool, str]:
    f = dossier / AUTORISATION
    if not f.exists():
        return False, f"{AUTORISATION} absent : envoi réel verrouillé (phase d'observation)"
    try:
        a = json.loads(f.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"{AUTORISATION} illisible ({exc})"
    if a.get("envoi_reel") is not True:
        return False, f"{AUTORISATION} : envoi_reel n'est pas true"
    return True, f"autorisé par {a.get('valide_par', '?')} le {a.get('date', '?')}"


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


def atr_d1(mt5, sym: str, n: int = 14) -> float | None:
    """ATR D1 (moyenne simple des vrais écarts) sur les bougies CLÔTURÉES."""
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 1, n + 1)
    if r is None or len(r) < n + 1:
        return None
    tr = [max(r[i]["high"] - r[i]["low"], abs(r[i]["high"] - r[i - 1]["close"]), abs(r[i]["low"] - r[i - 1]["close"]))
          for i in range(1, len(r))]
    return sum(tr) / len(tr)


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
    if ordre and ctx.get("garde") == "stop":
        return {**d, "action": "RETIRER", "raison": f"garde-fou du jour : perte ≥ {SEUIL_JOUR} $, on retire les ordres en attente"}
    autres = exclusifs(p) & ctx.get("occupes", {}).get(pair, set())
    excl_txt = f"plan exclusif avec {', '.join(sorted(autres))} (déjà posé ou en position)" if autres else ""

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
            if excl_txt:
                return {**d, "action": "RIEN", "raison": f"plan « {statut} », condition remplie mais {excl_txt} : non posable"}
            ppl0 = perte_par_lot(mt5, sym, side, entry, sl)
            v0 = cd.verdict_nouveau(pair, side == "buy", ctx["equite"] * RISQUE_PCT / 100, ctx["table"], ctx["total"])
            if v0.startswith("INTERDIT") and ppl0 > 0:
                return {**d, "action": "RIEN", "raison": f"plan « {statut} », condition remplie mais {v0} : non posable"}
            return {**d, "action": "SIGNALER", "raison": f"plan « {statut} » mais sa condition est remplie ({cond_txt}), le prix et les règles le permettent : à valider au suivi"}
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

    if ordre:                                   # déjà posé : on contrôle, et on repose si le risque a dérivé
        ecarts, reposer = ecarts_ordre(ordre.volume_current, lot, info.volume_step, ordre.sl, sl, ordre.tp, tp1,
                                       2 * info.point * 10)
        r_reel = ordre.volume_current * perte_par_lot(mt5, sym, side, entry, ordre.sl or sl)
        d["risque_reel_pct"] = round(r_reel / ctx["equite"] * 100, 2) if ctx["equite"] else None
        if not ecarts:
            return {**d, "action": "RIEN", "raison": "ordre déjà posé, conforme au plan"}
        txt_e = " ; ".join(ecarts) + f" | risque réel {d['risque_reel_pct']} %"
        if not reposer:
            return {**d, "action": "SIGNALER", "raison": "ordre posé, écart : " + txt_e}
        ok, txt = cote_ok(side, typ, entry, tick.bid, tick.ask)
        if not ok:
            return {**d, "action": "SIGNALER", "raison": f"ordre posé, écart : {txt_e} ; à reposer mais {txt} : le garder ou le retirer (humain)"}
        return {**d, "action": "REPOSER", "ticket_a_retirer": ordre.ticket,
                "raison": f"ordre posé, écart : {txt_e} -> retirer #{ordre.ticket} et reposer {d['type']} {entry} SL {sl} TP {tp1} à {lot} lot = {d['pct']} %"}

    ok, txt = cote_ok(side, typ, entry, tick.bid, tick.ask)
    if not ok:
        return {**d, "action": "NE PAS POSER", "raison": f"{txt} (bid {tick.bid} / ask {tick.ask}) : ne jamais entrer au marché"}
    if tp1 and tp_deja_passe(side, tp1, tick.bid, tick.ask):
        return {**d, "action": "NE PAS POSER", "raison": "TP déjà dépassé par le prix"}
    if lot <= 0:
        return {**d, "action": "SIGNALER", "raison": "lot calculé trop petit (vérifier SL / symbole)"}
    if excl_txt:
        return {**d, "action": "NE PAS POSER", "raison": excl_txt}
    jsl, x_atr = juge_sl(abs(entry - sl), atr_d1(mt5, sym))
    d["sl_atr"] = round(x_atr, 2) if x_atr is not None else None
    if jsl == "refus":
        return {**d, "action": "NE PAS POSER", "raison": f"stop minuscule : {x_atr:.2f} ATR D1 (< {SL_MIN_ATR}) -> lot énorme, refusé"}
    spread = tick.ask - tick.bid
    if spread > SPREAD_MAX_FRAC * abs(entry - sl):
        return {**d, "action": "ATTENDRE", "raison": f"spread {spread / (info.point * 10):.1f} pip > 10 % de la distance au stop"}
    if ctx.get("garde") != "ok":
        return {**d, "action": "ATTENDRE", "raison": f"garde-fou du jour : {ctx.get('garde_txt', '?')} -> aucun nouvel ordre"}
    if ctx["srv_now"].weekday() == 4 and ctx["srv_now"].hour >= VENDREDI_STOP_H:
        return {**d, "action": "ATTENDRE", "raison": f"vendredi après {VENDREDI_STOP_H}h serveur : aucun nouvel ordre avant le week-end"}
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
    return {**d, "action": "POSER", "symbole": sym,
            "raison": f"{d['type']} {entry} SL {sl} TP {tp1} | {d['lot']} lot = {d['pct']} % (≈ {d['risque']:.0f} $) | prix à {dist:.0f} pip de l'entrée"
                      + (f" | {verdict}" if verdict != "OK" else "")
                      + (f" | SL à {x_atr:.2f} ATR (< {SL_CONSEIL_ATR} : vérifier qu'il est derrière le dernier extrême)" if jsl == "court" else "")}


# ----------------------------------------------------------------------------- exécution (essai / réel)
def requete_pose(mt5, d: dict) -> dict:
    info = mt5.symbol_info(d["symbole"])
    typ = {"BUY LIMIT": mt5.ORDER_TYPE_BUY_LIMIT, "SELL LIMIT": mt5.ORDER_TYPE_SELL_LIMIT,
           "BUY STOP": mt5.ORDER_TYPE_BUY_STOP, "SELL STOP": mt5.ORDER_TYPE_SELL_STOP}[d["type"]]
    rq = {"action": mt5.TRADE_ACTION_PENDING, "symbol": d["symbole"], "volume": float(d["lot"]), "type": typ,
          "price": round(d["entree"], info.digits), "sl": round(d["sl"], info.digits),
          "type_time": mt5.ORDER_TIME_GTC, "magic": MAGIC, "comment": f"executant {d['plan']}"}
    if d.get("tp"):
        rq["tp"] = round(d["tp"], info.digits)
    return rq


def executer(mt5, d: dict, reel: bool, log) -> str:
    """Mode essai (reel=False) : mt5.order_check seulement, rien n'est envoyé. Mode réel : order_send."""
    a = d["action"]
    etapes = []
    if a in ("RETIRER", "REPOSER"):
        t = d.get("ticket_a_retirer") or d.get("ticket")
        rq = {"action": mt5.TRADE_ACTION_REMOVE, "order": int(t)}
        if reel:
            r = mt5.order_send(rq)
            etapes.append(f"retrait #{t} : retcode {getattr(r, 'retcode', None)} {getattr(r, 'comment', mt5.last_error())}")
            if not r or r.retcode != mt5.TRADE_RETCODE_DONE:
                return " ; ".join(etapes) + " -> ARRÊT (pas de nouvelle pose)"
        else:
            etapes.append(f"retrait #{t} : prêt (essai, non envoyé)")
    if a in ("POSER", "REPOSER"):
        d.setdefault("symbole", d.get("symbole") or d["paire"])
        rq = requete_pose(mt5, d)
        if reel:
            r = mt5.order_send(rq)
            etapes.append(f"pose : retcode {getattr(r, 'retcode', None)} {getattr(r, 'comment', mt5.last_error())}")
        else:
            c = mt5.order_check(rq)
            etapes.append(f"pose vérifiée par le courtier (essai) : retcode {getattr(c, 'retcode', None)} "
                          f"« {getattr(c, 'comment', mt5.last_error())} » marge {getattr(c, 'margin', '?')}")
    txt = " ; ".join(etapes)
    log.info(f"{'RÉEL' if reel else 'ESSAI'} {d['paire']} {d['plan']} {a} : {txt}")
    return txt


# ----------------------------------------------------------------------------- programme
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--essai", action="store_true", help="vérifier chaque action auprès du courtier (order_check), sans rien envoyer")
    ap.add_argument("--live", action="store_true", help="envoyer réellement (verrouillé sans _executant/autorisation.json)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        handlers=[logging.FileHandler(OUT / "robot_executant.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("executant")
    mode = "observation"
    if args.live:
        ok, why = envoi_autorise(OUT)
        if not ok:
            log.error(f"--live refusé : {why}. Aucun ordre envoyé.")
            sys.exit(2)
        mode = "réel"
        log.warning(f"MODE RÉEL : {why}")
    elif args.essai:
        mode = "essai"

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
        ref = cd.reference_du_jour()
        perte = (ref["reference"] - acc.equity) if ref else None
        garde = garde_jour(perte)
        garde_txt = ("référence du jour inconnue" if perte is None else
                     f"perte du jour {max(perte, 0):.0f} $ ({max(perte, 0) / SEUIL_JOUR * 100:.0f} % du seuil {SEUIL_JOUR} $)")
        ordres = list(mt5.orders_get() or [])
        positions = list(mt5.positions_get() or [])
        ctx = {"srv_now": srv_now, "utc_now": utc_now, "equite": acc.equity, "ordres": ordres,
               "table": cd.tableau(lignes), "total": sum(l["risque"] or 0 for l in lignes),
               "evenements": evenements, "cal_dispo": cal_dispo, "ordres_rattaches": set(),
               "symboles": {}, "garde": garde, "garde_txt": garde_txt, "occupes": {}}
        plans_lus = []
        for f in sorted(JOURNAL.glob("*/suivi/plan_actif.json")):
            try:
                plans_lus.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception as exc:
                plans_lus.append({"pair": f.parent.parent.name, "_erreur": str(exc), "plans": []})
        # plans « occupés » : un ordre réel leur correspond, ou déclenchés avec une position ouverte sur la paire
        for plan in plans_lus:
            pair = plan.get("pair", "")
            pos_paire = any(po.symbol.upper().startswith(pair) for po in positions)
            for p in plan.get("plans", []):
                if (p.get("side"), p.get("type")) not in TYPES:
                    continue
                if any(correspond(pair, p["side"], p["type"], p["entry"], o.symbol, o.type, o.price_open) for o in ordres) \
                        or (p.get("statut") == "déclenché" and pos_paire):
                    ctx["occupes"].setdefault(pair, set()).add(p["id"])
        decisions = []
        for plan in plans_lus:
            if "_erreur" in plan:
                decisions.append({"paire": plan["pair"], "plan": "-", "action": "SIGNALER", "raison": f"plan illisible : {plan['_erreur']}"})
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
        for po in positions:
            decisions.append({"paire": po.symbol, "plan": "POSITION", "action": "RIEN", "ticket": po.ticket,
                              "raison": f"{NOM_TYPE.get(po.type)} {po.volume} lot @ {po.price_open} SL {po.sl} TP {po.tp}, résultat {po.profit:+.2f} {acc.currency} ({cd.QUI.get(po.magic, po.magic)})"})
        if mode in ("essai", "réel"):
            for x in decisions:
                if x["action"] in ("POSER", "RETIRER", "REPOSER") and x.get("plan") not in ("HORS PLAN", "POSITION"):
                    try:
                        x["execution"] = executer(mt5, x, mode == "réel", log)
                    except Exception as exc:
                        x["execution"] = f"échec : {exc}"
                        log.exception(f"exécution {x['paire']} {x['plan']}")
    finally:
        mt5.shutdown()

    # ------------------------------------------------------------- sorties
    ordre_aff = {"RETIRER": 0, "REPOSER": 0, "POSER": 1, "SIGNALER": 2, "NE PAS POSER": 3, "ATTENDRE": 4, "RIEN": 5}
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

    titre_mode = {"observation": "**MODE OBSERVATION. Aucun ordre n'a été envoyé.** Ce tableau dit ce que le robot ferait.",
                  "essai": "**MODE ESSAI. Aucun ordre n'a été envoyé** : chaque action a seulement été vérifiée par le courtier (order_check).",
                  "réel": "**MODE RÉEL : les actions POSER / RETIRER / REPOSER ont été envoyées au courtier.**"}[mode]
    L = [f"# Robot exécutant — dernier passage {srv_now:%d/%m %H:%M} serveur ({(srv_now - dt.timedelta(hours=1)):%H:%M} Paris)",
         "", titre_mode, "",
         f"Compte {acc.login} | équité {acc.equity:,.2f} {acc.currency} | calendrier {'OK' if cal_dispo else 'INDISPONIBLE'} | garde-fou : {garde} ({garde_txt})".replace(",", " "), "",
         "| Action | Paire | Plan | Détail |", "|---|---|---|---|"]
    for x in decisions:
        ex = f" — **{x['execution']}**" if x.get("execution") else ""
        L.append(f"| **{x['action']}** | {x['paire']} | {x['plan']} | {x['raison']}{ex} |")
    L += ["", "Rappel : le robot n'invente aucun trade ; il applique les plans du suivi et les règles devise par devise.",
          "Carte de contexte, pas un signal : nos tests n'ont trouvé aucun avantage mécanique."]
    (OUT / "derniere_decision.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    log.info(f"passage terminé : {len(decisions)} décisions, {len(changements)} changement(s)")


if __name__ == "__main__":
    main()
