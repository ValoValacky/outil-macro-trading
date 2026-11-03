"""Ligne "profil de la semaine" pour les journaux H4.

Usage :  python profil_semaine.py EURNZD [prix] [--sans-courante]
   (prix = cours au moment de l'analyse si elle n'est pas faite en direct ; --sans-courante = n'affiche pas le profil provisoire de la semaine en cours)
Lecture seule (MT5 ouvert sur le terminal FTMO). Meme calcul que l'indicateur Market_Profile_TPO
(session = semaine lundi-vendredi, TPO M30, zone de valeur 70 %, hauteur de ligne automatique).
Affiche la ligne a coller dans la section 3 (Graphique H4) du journal.
"""
import sys, math, datetime as dt
import MetaTrader5 as mt5

TERMINAL = r"C:\Program Files\FTMO Global Markets MT5 Terminal\terminal64.exe"


def profile(h, l, rs):
    lo = min(l); hi = max(h)
    base = math.floor(lo / rs + 1e-6) * rs
    rows = int(math.floor((hi - base) / rs - 1e-6)) + 1
    cnt = [0.0] * rows
    for a, b in zip(l, h):
        rl = max(0, int(math.floor((a - base) / rs + 1e-6)))
        rh = int(math.floor((b - base) / rs - 1e-6))
        rh = min(max(rh, rl), rows - 1)
        for k in range(rl, rh + 1): cnt[k] += 1
    mid = (rows - 1) / 2.0; poc = 0; best = -1
    for k, c in enumerate(cnt):
        if c > best + 1e-9 or (abs(c - best) <= 1e-9 and abs(k - mid) < abs(poc - mid)): best = c; poc = k
    tgt = sum(cnt) * 0.70; vl = vh = poc; acc = cnt[poc]
    while acc < tgt - 1e-9:
        uo = vh + 1 < rows; do = vl - 1 >= 0
        if not uo and not do: break
        up = (cnt[vh + 1] + (cnt[vh + 2] if vh + 2 < rows else 0)) if uo else 0
        dn = (cnt[vl - 1] + (cnt[vl - 2] if vl - 2 >= 0 else 0)) if do else 0
        if uo and (not do or up >= dn): acc += up; vh = min(rows - 1, vh + 2)
        else: acc += dn; vl = max(0, vl - 2)
    return dict(poc=base + (poc + .5) * rs, vah=base + (vh + 1) * rs, val=base + vl * rs, high=hi, low=lo,
                poorH=cnt[-1] >= 2, poorL=cnt[0] >= 2)


def main(sym, px_override=None, with_current=True):
    if not mt5.initialize(path=TERMINAL):
        sys.exit("MT5 indisponible : " + str(mt5.last_error()))
    mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym)
    digits = info.digits
    unit = info.point * 10 if digits in (3, 5) else 1.0
    uTxt = "pips" if digits in (3, 5) else "pts"
    w1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_W1, 0, 12)
    rng = [float(r["high"] - r["low"]) for r in w1[:-1]]
    step = unit / 2
    rs = round(max(step, round((sum(rng) / len(rng)) / 40 / step) * step), digits)
    now = dt.datetime.utcfromtimestamp(mt5.symbol_info_tick(sym).time)
    monday = (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    weeks = []                                   # du plus ancien au plus recent, 9 semaines
    for k in range(8, -1, -1):
        a = monday - dt.timedelta(weeks=k); b = a + dt.timedelta(days=5)
        r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M30, a, b - dt.timedelta(seconds=1))
        if r is not None and len(r) > (10 if k == 0 else 100):      # la semaine en cours est incomplete
            weeks.append((a, r))
    cur_a, cur = weeks[-1]
    px = px_override if px_override else mt5.symbol_info_tick(sym).bid
    P = None; Pa = None
    if len(weeks) >= 2:
        Pa, rp = weeks[-2]
        P = profile([float(x["high"]) for x in rp], [float(x["low"]) for x in rp], rs)
    C = profile([float(x["high"]) for x in cur], [float(x["low"]) for x in cur], rs)
    fmt = lambda v: f"{v:.{digits}f}".replace(".", ",")
    pip = lambda d: f"{abs(d) / unit:.1f}".replace(".", ",")
    where = lambda v, lo, hi: (f"AU-DESSUS de la zone (+{pip(v - hi)} {uTxt})" if v > hi else
                               f"EN DESSOUS de la zone ({'-'}{pip(lo - v)} {uTxt})" if v < lo else "DANS la zone de valeur")
    o = float(cur[0]["open"])
    parts = []
    if P:
        d1 = (Pa).strftime("%d/%m"); d2 = (Pa + dt.timedelta(days=4)).strftime("%d/%m")
        parts.append(f"semaine précédente ({d1} au {d2}) : POC {fmt(P['poc'])}, zone de valeur {fmt(P['val'])} - {fmt(P['vah'])}")
        parts.append(f"ouverture de la semaine {fmt(o)} : {where(o, P['val'], P['vah'])}")
        parts.append(f"prix {fmt(px)} : {where(px, P['val'], P['vah'])}")
        pk = []
        if P["poorH"]: pk.append("Poor High")
        if P["poorL"]: pk.append("Poor Low")
        parts.append("Poor High/Low de la semaine précédente : " + (", ".join(pk) if pk else "aucun"))
    if with_current: parts.append(f"semaine en cours (provisoire) : POC {fmt(C['poc'])}, zone {fmt(C['val'])} - {fmt(C['vah'])}")
    # POC hebdomadaires "nus" (jamais retouches par une semaine posterieure, y compris la semaine en cours)
    naked = []
    for i in range(len(weeks) - 1):
        a, r = weeks[i]
        pr = profile([float(x["high"]) for x in r], [float(x["low"]) for x in r], rs)
        touched = any(min(float(x["low"]) for x in rr) <= pr["poc"] <= max(float(x["high"]) for x in rr) for _, rr in weeks[i + 1:])
        if not touched: naked.append((abs(pr["poc"] - px), pr["poc"], a))
    if naked:
        d, lvl, a = min(naked)
        parts.append(f"POC nu le plus proche : {fmt(lvl)} (semaine du {a.strftime('%d/%m')}, à {pip(lvl - px)} {uTxt} du prix)")
    else:
        parts.append("aucun POC hebdomadaire nu parmi les 8 dernières semaines")
    print("- **Profil de la semaine (TPO M30, comme l'indicateur)** : " + " ; ".join(parts) + ". Lecture de contexte seulement : les tests n'ont pas trouvé d'avantage mécanique.")
    mt5.shutdown()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0].upper(), float(args[1].replace(",", ".")) if len(args) > 1 else None, "--sans-courante" not in sys.argv)
