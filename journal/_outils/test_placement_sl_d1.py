"""Test : ou placer le SL d'une entree de cassure D1 ?
Signal : cloture D1 au-dessus du plus haut des 20 jours precedents -> achat a l'ouverture suivante (miroir pour la vente).
Stops compares (meme risque en EUR, donc resultat en R) :
  serre   = 0,8 ATR (comme notre 1,9340 : 0,78 ATR)
  1ATR, 1,2ATR (comme le SL initial), 1,5ATR
  struct  = sous le dernier creux D1 confirme (pivot 3 bougies de chaque cote) - 0,1 ATR
Sortie : SL, ou TP a 2 R, ou cloture apres 20 jours. SL et TP dans la meme bougie -> SL (prudent).
Couts : 0,05 ATR par trade (spread + commission), retires en R.
Aussi : % des stops touches ou le prix revient a l'entree dans les 5 jours (la "meche qui sort puis repart").
"""
import MetaTrader5 as mt5, pandas as pd, numpy as np
mt5.initialize()
PAIRS = ["GBPAUD", "GBPCAD", "GBPNZD", "GBPUSD", "EURUSD", "USDCAD", "AUDCHF", "EURNZD", "EURJPY", "NZDCAD", "AUDUSD", "XAUUSD"]
STOPS = ["serre_0.8", "1ATR", "1.2ATR", "1.5ATR", "struct"]
rows = []
for sym in PAIRS:
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 5000)
    if r is None: continue
    d = pd.DataFrame(r); d["t"] = pd.to_datetime(d.time, unit="s"); d = d[d.t >= "2010-01-01"].reset_index(drop=True)
    tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(), (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    hh = d.high.rolling(20).max().shift(1); ll = d.low.rolling(20).min().shift(1)
    H, L, C, O, A = d.high.values, d.low.values, d.close.values, d.open.values, d.atr.values
    n = len(d); k = 3
    piv_lo = np.full(n, np.nan); piv_hi = np.full(n, np.nan)  # dernier pivot CONFIRME connu a la bougie i
    last_lo = last_hi = np.nan
    for i in range(n):
        j = i - k
        if j >= k:
            if L[j] == L[j-k:j+k+1].min(): last_lo = L[j]
            if H[j] == H[j-k:j+k+1].max(): last_hi = H[j]
        piv_lo[i] = last_lo; piv_hi[i] = last_hi
    busy_until = -1
    for i in range(30, n - 21):
        if i <= busy_until: continue
        for side in (1, -1):
            sig = C[i] > hh[i] if side == 1 else C[i] < ll[i]
            if not sig: continue
            e = O[i+1]; a = A[i]
            for s in STOPS:
                if s == "struct":
                    ref = piv_lo[i] if side == 1 else piv_hi[i]
                    if np.isnan(ref): continue
                    sl = ref - side * 0.1 * a
                    if (e - sl) * side <= 0.2 * a: continue
                else:
                    m = float(s.split("_")[-1].replace("ATR", "")) if s != "1ATR" else 1.0
                    sl = e - side * m * a
                dist = abs(e - sl); tp = e + side * 2 * dist
                res = None; back = False
                for j in range(i+1, i+21):
                    hit_sl = L[j] <= sl if side == 1 else H[j] >= sl
                    hit_tp = H[j] >= tp if side == 1 else L[j] <= tp
                    if hit_sl:
                        res = -1.0
                        back = any((H[x] >= e if side == 1 else L[x] <= e) for x in range(j, min(j+6, n)))
                        break
                    if hit_tp: res = 2.0; break
                if res is None: res = (C[i+20] - e) * side / dist
                rows.append(dict(sym=sym, t=d.t[i], side=side, stop=s, dist_atr=dist / a, R=res - 0.05 * a / dist, sl=res == -1.0, back=back))
            busy_until = i + 5
mt5.shutdown()
df = pd.DataFrame(rows)
def stats(g):
    w = g.R[g.R > 0].sum(); l = -g.R[g.R < 0].sum()
    return pd.Series(dict(trades=len(g), dist_ATR=round(g.dist_atr.median(), 2), pct_stoppes=round(100*g.sl.mean()),
                          pct_stop_puis_retour=round(100*g.back.sum()/max(g.sl.sum(), 1)), R_moyen=round(g.R.mean(), 3), PF=round(w/l, 2) if l else np.nan))
print("=== TOUTES PAIRES 2010-2026 ===")
print(df.groupby("stop").apply(stats).loc[STOPS].to_string())
print("\n=== Depuis 2019 ===")
print(df[df.t >= "2019-01-01"].groupby("stop").apply(stats).loc[STOPS].to_string())
print("\n=== Achats seulement ===")
print(df[df.side == 1].groupby("stop").apply(stats).loc[STOPS].to_string())
print("\n=== GBPAUD seul ===")
print(df[df.sym == "GBPAUD"].groupby("stop").apply(stats).loc[STOPS].to_string())
print("\n=== R moyen par paire ===")
print(df.pivot_table(index="sym", columns="stop", values="R", aggfunc="mean").round(3)[STOPS].to_string())
