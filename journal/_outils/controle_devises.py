"""Controle devise par devise (regles du fichier Regles_Devises_Pas_De_Double_Pari.xlsx).

Lecture seule MT5 : n'envoie jamais d'ordre.
Usage : PYTHONIOENCODING=utf-8 python -u controle_devises.py [--md chemin_note.md]

Regles verifiees :
 1 un seul sens par devise (jamais achetee ET vendue)
 2 pas plus de 2 paris dans le meme sens sur une devise
 3 plafond de risque par devise (1 200 EUR)
 4 plafond de risque total positions + ordres (3 200 EUR)
 5 un ordre en attente compte comme une position
 6 les positions des robots comptent
Les plans du journal encore posables (statut en_attente / armé, pas encore poses
chez le courtier) sont testes un par un : « si tu le poses, est-ce permis ? ».
"""
import json, re, sys, datetime as dt, pathlib
import MetaTrader5 as mt5

NORMAL, DEMI, PLAF_DEVISE, PLAF_TOTAL, SEUIL_GARDE = 800, 400, 1200, 3200, 4800
ROOT = pathlib.Path(__file__).resolve().parents[1]          # .../journal
GUARD = ROOT.parents[1] / "Bot Trading FTMO" / "state" / "ftmo_demo_guard.json"
NOMS = {"EUR": "euro", "USD": "dollar US", "GBP": "livre", "JPY": "yen", "CHF": "franc suisse",
        "AUD": "dollar australien", "NZD": "dollar néo-zélandais", "CAD": "dollar canadien",
        "XAU": "or", "XAG": "argent métal"}
QUI = {20260801: "robot structure-bot", 20260827: "robot HORIIZON", 20260900: "Magic Panel", 0: "manuel"}
BUY_TYPES = {0, 2, 4, 6}  # buy, buy limit, buy stop, buy stop limit


def devises(symbol):
    s = re.sub(r"[^A-Z]", "", symbol.upper())[:6]
    a, b = s[:3], s[3:6]
    return (a, b) if a in NOMS and b in NOMS else (None, None)


def risque(symbol, achat, vol, entree, sl):
    if not sl:
        return None
    t = mt5.ORDER_TYPE_BUY if achat else mt5.ORDER_TYPE_SELL
    p = mt5.order_calc_profit(t, symbol, vol, entree, sl)
    return 0.0 if p is None else max(0.0, -p)


def lignes_compte():
    out = []
    for p in mt5.positions_get() or []:
        achat = p.type == 0
        out.append(dict(sym=p.symbol, achat=achat, statut="position", vol=p.volume, prix=p.price_open,
                        sl=p.sl, risque=risque(p.symbol, achat, p.volume, p.price_open, p.sl),
                        qui=QUI.get(p.magic, f"magic {p.magic}")))
    for o in mt5.orders_get() or []:
        achat = o.type in BUY_TYPES
        out.append(dict(sym=o.symbol, achat=achat, statut="ordre en attente", vol=o.volume_current,
                        prix=o.price_open, sl=o.sl,
                        risque=risque(o.symbol, achat, o.volume_current, o.price_open, o.sl),
                        qui=QUI.get(o.magic, f"magic {o.magic}")))
    return out


def tableau(lignes):
    t = {d: dict(achat=0, vente=0, r_achat=0.0, r_vente=0.0, qui_achat=[], qui_vente=[]) for d in NOMS}
    for l in lignes:
        a, b = devises(l["sym"])
        if not a:
            continue
        r = l["risque"] or 0.0
        etiquette = f"{l['sym']} {'achat' if l['achat'] else 'vente'} ({l['statut']})"
        for dev, achetee in ((a, l["achat"]), (b, not l["achat"])):
            k = "achat" if achetee else "vente"
            t[dev][k] += 1; t[dev]["r_" + k] += r; t[dev]["qui_" + k].append(etiquette)
    return t


def etat(dev, x):
    n = NOMS[dev]
    if x["achat"] and x["vente"]:
        return f"CONFLIT règle 1 : {n} en achat {x['achat']} fois ET en vente {x['vente']} fois"
    sens, nb, r = ("achat", x["achat"], x["r_achat"]) if x["achat"] else ("vente", x["vente"], x["r_vente"])
    if nb > 2:
        return f"TROP règle 2 : {n} en {sens} {nb} fois"
    if r > PLAF_DEVISE:
        return f"TROP règle 3 : {r:,.0f} € sur le {n} (maximum {PLAF_DEVISE} €)".replace(",", " ")
    if nb == 0:
        return None
    return f"OK : {n} en {sens} {nb} fois ({r:,.0f} €)".replace(",", " ")


def verdict_nouveau(sym, achat, r, t, total):
    a, b = devises(sym)
    if not a:
        return "hors devises (non contrôlé)"
    msgs = []
    for dev, achetee in ((a, achat), (b, not achat)):
        x = t[dev]
        meme, oppose = ("achat", "vente") if achetee else ("vente", "achat")
        if x[oppose]:
            return f"INTERDIT règle 1 : {dev} déjà en {oppose} ailleurs"
        if x[meme] >= 2:
            return f"INTERDIT règle 2 : {dev} déjà en {meme} {x[meme]} fois"
        if x["r_" + meme] + r > PLAF_DEVISE:
            return f"INTERDIT règle 3 : {x['r_' + meme] + r:,.0f} € sur {dev}".replace(",", " ")
        if x[meme] == 1 and r > DEMI:
            msgs.append(f"RÉDUIS à {DEMI} € (2e pari en {meme} sur {dev})")
    if total + r > PLAF_TOTAL:
        return f"INTERDIT règle 4 : total {total + r:,.0f} € (maximum {PLAF_TOTAL} €)".replace(",", " ")
    return msgs[0] if msgs else "OK"


def plans_posables(lignes):
    """Plans du journal en_attente / armé, encore valables, pas encore posés chez le courtier."""
    today = dt.date.today().isoformat()
    out = []
    for f in sorted(ROOT.glob("*/suivi/plan_actif.json")):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if p.get("valid_until", "9999") < today:
            continue
        for pl in p.get("plans", []):
            if pl.get("statut") not in ("en_attente", "armé"):
                continue
            achat = pl.get("side") == "buy"
            entree = pl.get("entry") or 0
            pose = any(l["sym"].upper().startswith(p["pair"]) and l["achat"] == achat
                       and abs(l["prix"] - entree) <= 0.003 * entree for l in lignes)
            if pose:
                continue
            m = re.search(r"risque\s+([\d\s]+)\s*EUR", pl.get("lot_texte", ""))
            r = float(m.group(1).replace(" ", "")) if m else float(NORMAL)
            out.append(dict(pair=p["pair"], id=pl["id"], achat=achat, entree=entree, risque=r))
    return out


def main():
    md_path = sys.argv[sys.argv.index("--md") + 1] if "--md" in sys.argv else None
    ok = mt5.initialize()
    if not ok:
        print("MT5 ne répond pas :", mt5.last_error()); sys.exit(1)
    acc = mt5.account_info()
    lignes = lignes_compte()
    t = tableau(lignes)
    total = sum(l["risque"] or 0 for l in lignes)
    out = []
    P = out.append
    P(f"## Contrôle devise par devise ({dt.datetime.now():%d/%m %H:%M}, compte {acc.login})")
    P("")
    P("### Ce qui est ouvert ou en attente (robots compris)")
    P("| Paire | Sens | Statut | Qui | Ce que ça veut dire | Risque jusqu'au stop |")
    P("|---|---|---|---|---|---|")
    for l in lignes:
        a, b = devises(l["sym"])
        clair = (f"j'achète {a}, je vends {b}" if l["achat"] else f"je vends {a}, j'achète {b}") if a else "hors devises"
        r = "SANS STOP" if l["risque"] is None else f"{l['risque']:,.0f} €".replace(",", " ")
        P(f"| {l['sym']} | {'ACHAT' if l['achat'] else 'VENTE'} | {l['statut']} | {l['qui']} | {clair} | {r} |")
    P("")
    P("### Chaque devise")
    P("| Devise | En achat | En vente | État |")
    P("|---|---|---|---|")
    alertes = []
    for dev, x in t.items():
        e = etat(dev, x)
        if e is None:
            continue
        P(f"| {dev} | {x['achat']} ({x['r_achat']:,.0f} €) | {x['vente']} ({x['r_vente']:,.0f} €) | {e} |".replace(",", " "))
        if not e.startswith("OK"):
            alertes.append((dev, e, x))
    P("")
    pct = total / acc.balance * 100 if acc.balance else 0
    tot_txt = f"{total:,.0f}".replace(",", " ")
    pct_txt = f"{pct:.2f}".replace(".", ",")
    regle4 = f"TROP règle 4 (maximum {PLAF_TOTAL} €)" if total > PLAF_TOTAL else "OK"
    P(f"**Total du risque (positions + ordres) : {tot_txt} € soit {pct_txt} % du compte** -> {regle4}")
    sans_stop = [l["sym"] for l in lignes if l["risque"] is None]
    if sans_stop:
        P(f"**ATTENTION : sans stop (risque illimité) : {', '.join(sans_stop)}**")
    if alertes:
        P("")
        P("### Problèmes à régler (qui est en cause)")
        for dev, e, x in alertes:
            P(f"- **{dev}** : {e}. En achat : {', '.join(x['qui_achat']) or '-'} ; en vente : {', '.join(x['qui_vente']) or '-'}.")
    cand = plans_posables(lignes)
    if cand:
        P("")
        P("### Si tu poses un ordre de plan pas encore posé")
        for c in cand:
            v = verdict_nouveau(c["pair"], c["achat"], c["risque"], t, total)
            P(f"- {c['pair']} plan {c['id']} ({'achat' if c['achat'] else 'vente'} à {c['entree']}, {c['risque']:.0f} €) : **{v}**")
    try:
        g = json.loads(GUARD.read_text(encoding="utf-8"))
        perte = g["daily_start_balance"] - acc.equity
        P("")
        P(f"**Garde-fou** : référence {g['daily_start_balance']:,.2f} € ({g['daily_start_date']}) ; équité {acc.equity:,.2f} € -> "
          f"{'perte' if perte > 0 else 'gain'} du jour {abs(perte):,.0f} € "
          f"({max(perte, 0) / SEUIL_GARDE * 100:.0f} % du seuil de {SEUIL_GARDE} €)".replace(",", " "))
    except Exception as ex:
        P(f"Garde-fou illisible : {ex}")
    mt5.shutdown()
    txt = "\n".join(out)
    print(txt)
    if md_path:
        pathlib.Path(md_path).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(md_path).write_text(txt + "\n\nRègles : Regles_Devises_Pas_De_Double_Pari.xlsx (lecture seule MT5, aucun ordre envoyé).\n", encoding="utf-8")


if __name__ == "__main__":
    main()
