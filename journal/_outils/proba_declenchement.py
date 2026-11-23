# Probabilite qu'un SELL LIMIT place k ATR D1 au-dessus du prix soit TOUCHE avant la fin de la semaine.
# Depart : chaque lundi vers 11h serveur (comme aujourd'hui), horizon : vendredi 23h serveur.
# Lecture seule MT5. Pas de prediction de gain : seulement le declenchement.
import MetaTrader5 as mt5, pandas as pd, numpy as np, sys, datetime as dt
sys.stdout.reconfigure(encoding="utf-8")
mt5.initialize(path=r"C:\Program Files\FTMO Global Markets MT5 Terminal\terminal64.exe")

def get(s, tf, n):
    for m in (n, 80000, 50000, 30000):
        r = mt5.copy_rates_from_pos(s, tf, 0, m)
        if r is not None and len(r): break
    d = pd.DataFrame(r); d["time"] = pd.to_datetime(d.time, unit="s"); return d.set_index("time")

def atr(d, n=14):
    pc = d.close.shift(); tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

CAS = {"EURUSD": dict(entry=1.1015), "XAUUSD": dict(entry=3700.0)}
for s, c in CAS.items():
    h1 = get(s, mt5.TIMEFRAME_H1, 120000); d1 = get(s, mt5.TIMEFRAME_D1, 6000)
    d1["atr"] = atr(d1); d1["mm20"] = d1.close.rolling(20).mean(); d1["mm50"] = d1.close.rolling(50).mean()
    d1["r5"] = d1.close / d1.close.shift(5) - 1
    tick = mt5.symbol_info_tick(s); prix = tick.bid
    a_now = d1.atr.iloc[-2]; k = (c["entry"] - prix) / a_now
    cl = d1.iloc[-2]
    print(f"\n===== {s} : prix {prix} | entrée {c['entry']} | ATR D1 {a_now:.5g} | distance {k:.2f} ATR | "
          f"contexte : clôture<MM20<MM50 = {cl.close < cl.mm20 < cl.mm50}, chute 5 j {cl.r5*100:+.1f} %")
    print(f"Historique H1 disponible depuis {h1.index[0]:%d/%m/%Y}")
    rows = []
    mondays = h1[(h1.index.dayofweek == 0) & (h1.index.hour == 11)]
    for t, bar in mondays.iterrows():
        prev = d1[d1.index < t.normalize()]
        if len(prev) < 60: continue
        p = prev.iloc[-1]; A = p.atr
        start = bar.open; lvl = start + k * A
        fin = t.normalize() + pd.Timedelta(days=4, hours=23)
        w = h1[(h1.index >= t) & (h1.index <= fin)]
        hit = w[w.high >= lvl]
        jour = hit.index[0].dayofweek if len(hit) else None
        rows.append(dict(t=t, hit=len(hit) > 0, jour=jour, trend=p.close < p.mm20 < p.mm50, r5=p.r5))
    r = pd.DataFrame(rows)
    def bloc(x, nom):
        if len(x) == 0: print(f"  {nom} : aucun cas"); return
        cum = [(x.jour <= j).sum() / len(x) * 100 for j in range(5)]
        print(f"  {nom} ({len(x)} semaines) : touché avant vendredi soir {x.hit.mean()*100:.0f} % | "
              f"dès lundi {cum[0]:.0f} %, mardi soir {cum[1]:.0f} %, mercredi soir {cum[2]:.0f} %, jeudi soir {cum[3]:.0f} %")
    bloc(r, "toutes les semaines")
    bloc(r[r.trend], "tendance baissière D1 (clôture < MM20 < MM50)")
    bloc(r[r.t >= "2019-01-01"], "depuis 2019")
    bloc(r[(r.t >= "2019-01-01") & r.trend], "depuis 2019 + tendance baissière")
    if s == "XAUUSD":
        bloc(r[r.r5 <= -0.04], "après une chute de 4 % ou plus sur 5 jours")
    # sensibilite a la distance
    print("  Sensibilité (toutes semaines, même méthode) :", end=" ")
    for kk in (0.3, 0.5, 0.7, 0.85, 1.0, 1.2):
        n = 0; h = 0
        for t, bar in mondays.iterrows():
            prev = d1[d1.index < t.normalize()]
            if len(prev) < 60: continue
            A = prev.iloc[-1].atr; lvl = bar.open + kk * A
            w = h1[(h1.index >= t) & (h1.index <= t.normalize() + pd.Timedelta(days=4, hours=23))]
            n += 1; h += (w.high >= lvl).any()
        print(f"{kk} ATR = {h/n*100:.0f} %", end=" | ")
    print()
mt5.shutdown()
