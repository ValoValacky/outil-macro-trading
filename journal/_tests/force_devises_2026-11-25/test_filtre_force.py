# Test du filtre « force des devises » : voir REGLES.md (regles fixees avant les resultats). Lecture seule MT5.
import MetaTrader5 as mt5, pandas as pd, numpy as np, sys
sys.stdout.reconfigure(encoding="utf-8")
ORDRE = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]
PAIRES = [a + b for i, a in enumerate(ORDRE) for b in ORDRE[i + 1:]]
DEBUT = pd.Timestamp("2015-01-01")
ENTREE, SL, TP, COUT = 0.5, 1.2, 1.8, 0.03

mt5.initialize(path=r"C:\Program Files\FTMO Global Markets MT5 Terminal\terminal64.exe")
def get(s, tf, n):
    for m in (n, 100000, 80000, 60000, 40000):
        r = mt5.copy_rates_from_pos(s, tf, 1, m)
        if r is not None and len(r): break
    d = pd.DataFrame(r); d["time"] = pd.to_datetime(d.time, unit="s"); return d.set_index("time")

D1, H1 = {}, {}
for s in PAIRES:
    D1[s] = get(s, mt5.TIMEFRAME_D1, 5000); H1[s] = get(s, mt5.TIMEFRAME_H1, 120000)
mt5.shutdown()
debut_h1 = max(h.index[0] for h in H1.values())
print("H1 commun depuis", debut_h1.date(), "| D1 depuis", max(d.index[0] for d in D1.values()).date())

# --- force 20 jours par devise, par jour (cloture) ---
cl = pd.DataFrame({s: D1[s].close for s in PAIRES}).dropna()
lr20 = np.log(cl).diff(20) * 100
force = pd.DataFrame(0.0, index=cl.index, columns=ORDRE)
for s in PAIRES:
    a, b = s[:3], s[3:]
    force[a] += lr20[s] / 7; force[b] -= lr20[s] / 7
rang = force.rank(axis=1, ascending=False)  # 1 = plus forte

def atr(d, n=14):
    pc = d.close.shift(); tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()
ATR = {s: atr(D1[s]) for s in PAIRES}

rows = []
for s in PAIRES:
    h = H1[s]; h = h[h.index >= max(DEBUT, debut_h1)]
    lundis = h[(h.index.dayofweek == 0)].groupby(h[(h.index.dayofweek == 0)].index.normalize()).head(1)
    hi, lo, cls, idx = h.high.values, h.low.values, h.close.values, h.index
    for t, bar in lundis.iterrows():
        jour = t.normalize()
        prevA = ATR[s][ATR[s].index < jour]; prevR = rang[rang.index < jour]
        if len(prevA) < 30 or len(prevR) < 30 or np.isnan(prevR.iloc[-1]).any(): continue
        A = prevA.iloc[-1]; rg = prevR.iloc[-1]; o = bar.open
        fin = jour + pd.Timedelta(days=4, hours=23)
        i0 = idx.searchsorted(t); i1 = idx.searchsorted(fin, side="right")
        if i1 - i0 < 10: continue
        for sens in (1, -1):  # 1 achat, -1 vente
            e = o - sens * ENTREE * A; sl = e - sens * SL * A; tp = e + sens * TP * A
            res = None; entre = False
            for i in range(i0, i1):
                if not entre:
                    if (sens == 1 and lo[i] <= e) or (sens == -1 and hi[i] >= e):
                        entre = True
                    else:
                        continue
                hit_sl = lo[i] <= sl if sens == 1 else hi[i] >= sl
                hit_tp = hi[i] >= tp if sens == 1 else lo[i] <= tp
                if hit_sl: res = -1.0; break
                if hit_tp: res = TP / SL; break
            if not entre: continue
            if res is None: res = sens * (cls[i1 - 1] - e) / (SL * A)
            res -= COUT / SL
            achetee, vendue = (s[:3], s[3:]) if sens == 1 else (s[3:], s[:3])
            ecart = rg[vendue] - rg[achetee]  # > 0 : on achete la plus forte
            extreme = rg[achetee] >= 7 or rg[vendue] <= 2
            rows.append(dict(t=jour, paire=s, sens=sens, R=res, dans_sens=ecart > 0, contre_extreme=extreme, ecart=ecart))

r = pd.DataFrame(rows)
r.to_csv("resultats_trades.csv", index=False)
P = [("2015-2018", "2015", "2019"), ("2019-2022", "2019", "2023"), ("2023-2026", "2023", "2027")]
moitie1 = set(sorted(PAIRES)[:14])

def bilan(garde, retire, nom):
    print(f"\n=== {nom} ===")
    def ligne(x): return f"{len(x):6d} trades | moyenne {x.R.mean():+.3f} R | gagnants {100*(x.R>0).mean():.0f} %"
    print("  gardés  :", ligne(r[garde])); print("  retirés :", ligne(r[retire]))
    av = r[garde].R.mean() - r[retire].R.mean(); print(f"  avance des gardés : {av:+.3f} R")
    ok_p = 0
    for n, a, b in P:
        m = (r.t >= a) & (r.t < b); d = r[garde & m].R.mean() - r[retire & m].R.mean()
        ok_p += d > 0; print(f"   {n} : gardés {r[garde & m].R.mean():+.3f} / retirés {r[retire & m].R.mean():+.3f} -> {d:+.3f} R")
    sg = []
    for nm, m in (("paires 1-14", r.paire.isin(moitie1)), ("paires 15-28", ~r.paire.isin(moitie1))):
        d = r[garde & m].R.mean() - r[retire & m].R.mean(); sg.append(d); print(f"   {nm} : {d:+.3f} R")
    c1, c2, c3 = av >= 0.05, ok_p >= 2, (sg[0] > 0) == (sg[1] > 0) and sg[0] > 0
    print(f"  Critères : avance >= 0,05 R {c1} | 2 périodes sur 3 {c2} ({ok_p}/3) | deux moitiés {c3}  => {'UTILE' if c1 and c2 and c3 else 'PAS UTILE'}")

print(f"\nTous les trades : {len(r)} | moyenne {r.R.mean():+.3f} R")
bilan(r.dans_sens, ~r.dans_sens, "Règle 1 : jamais contre le classement 20 j")
bilan(~r.contre_extreme, r.contre_extreme, "Règle 2 : jamais acheter un 7e/8e ni vendre un 1er/2e")
print("\nDétail par écart de rang (achetée mieux classée = écart positif) :")
print(r.groupby(r.ecart.clip(-7, 7)).R.agg(["count", "mean"]).round(3).to_string())
