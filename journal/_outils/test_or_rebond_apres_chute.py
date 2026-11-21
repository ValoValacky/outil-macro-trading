import MetaTrader5 as mt5, numpy as np, datetime as dt
assert mt5.initialize(path=r"C:\Program Files\FTMO Global Markets MT5 Terminal\terminal64.exe")
d = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_D1, 0, 9000)
t = d['time']; o,h,l,c = d['open'],d['high'],d['low'],d['close']
print("D1 :", len(d), "bougies, de", dt.datetime.utcfromtimestamp(int(t[0])).date(), "a", dt.datetime.utcfromtimestamp(int(t[-1])).date())
h1 = None
for cnt in (140000, 120000, 100000, 80000, 60000, 40000, 20000):
    h1 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, cnt)
    if h1 is not None and len(h1) > 0: break
print("H1 :", len(h1), "bougies, de", dt.datetime.utcfromtimestamp(int(h1[0]['time'])).date())
n = len(d)
# ATR14 et RSI14 (Wilder)
tr = np.maximum(h[1:]-l[1:], np.maximum(abs(h[1:]-c[:-1]), abs(l[1:]-c[:-1])))
atr = np.full(n, np.nan)
for i in range(14, n): atr[i] = tr[i-14:i].mean()
rsi = np.full(n, np.nan)
dl = np.diff(c); g = np.where(dl>0, dl, 0); ls = np.where(dl<0, -dl, 0)
ag, al = g[:14].mean(), ls[:14].mean()
for i in range(14, n-1):
    ag = (ag*13+g[i])/14; al = (al*13+ls[i])/14
    rsi[i+1] = 100-100/(1+ag/al) if al>0 else 100
ret5 = np.full(n, np.nan); ret5[5:] = c[5:]/c[:-5]-1
H1t = h1['time']
def sim(i, mult_entry, sl_atr, tps_atr, hold_days=5):
    """Ordre SELL LIMIT pose a la cloture du jour i, valable hold_days sessions. Retourne None si non rempli, sinon R (stop=-1)."""
    A = atr[i]; ent = c[i] + mult_entry*A; sl = ent + sl_atr*A; tp = ent - tps_atr*A
    t0 = t[i]; t1 = t[min(i+hold_days, n-1)] + 86400
    a = np.searchsorted(H1t, t0+86400-3600*0)  # H1 apres la cloture du jour (apres minuit serveur du jour suivant)
    b = np.searchsorted(H1t, t1)
    if b - a < 20: return "nodata"
    hh, ll, cc = h1['high'][a:b], h1['low'][a:b], h1['close'][a:b]
    filled = False
    for k in range(len(hh)):
        if not filled:
            if hh[k] >= ent: filled = True
            else: continue
        # apres remplissage (meme bougie comprise : on teste stop d'abord = pessimiste)
        if hh[k] >= sl: return -1.0
        if ll[k] <= tp: return abs(ent-tp)/abs(sl-ent)
    if filled: return (ent - cc[-1])/abs(sl-ent)
    return None
def stats(mask, label):
    idx = [i for i in range(60, n-6) if mask[i]]
    # declustering : au plus 1 evenement par 10 sessions
    dec = []
    for i in idx:
        if not dec or i - dec[-1] >= 10: dec.append(i)
    out = {}
    for name, ids in (("tous jours", idx), ("1 par episode", dec)):
        mh = [ (h[i+1:i+6].max()-c[i])/atr[i] for i in ids ]
        mh = np.array(mh)
        out[name] = (len(ids), (mh>=1.23).mean()*100, (mh>=1.62).mean()*100, np.median(mh))
    print(f"\n### {label}")
    for name,(k,p1,p2,med) in out.items():
        print(f"  {name:14s}: {k:4d} cas | rebond >= 1,23 ATR (3 660) en 5 seances : {p1:4.0f} % | >= 1,62 ATR (3 700) : {p2:4.0f} % | rebond median {med:.2f} ATR")
    for plan,(me,sl_,tp_) in {"A' (3 660 : SL +1,26 ATR, TP -1,41 ATR)":(1.23,1.26,1.41), "A (3 700 : SL +1,26 ATR, TP1 -1,36 ATR)":(1.62,1.26,1.36)}.items():
        for name, ids in (("tous jours", idx), ("1 par episode", dec)):
            res = [sim(i, me, sl_, tp_) for i in ids]
            nd = sum(1 for r in res if r=="nodata"); res = [r for r in res if r!="nodata"]
            fill = [r for r in res if r is not None]
            if not fill: print(f"    {plan} [{name}] : aucun ordre rempli ({len(res)} cas)"); continue
            fill = np.array(fill)
            print(f"    {plan} [{name}] : rempli {len(fill)}/{len(res)} ({len(fill)/len(res)*100:.0f} %) | touche le TP {np.mean(fill>0.5)*100:.0f} % | stop {np.mean(fill==-1.0)*100:.0f} % | resultat moyen {fill.mean():+.2f} R | somme {fill.sum():+.1f} R" + (f" (sans H1 : {nd})" if nd else ""))
valid = ~np.isnan(ret5) & ~np.isnan(rsi) & ~np.isnan(atr)
stats(valid, "REFERENCE : tous les jours (n'importe quel contexte)")
stats(valid & (ret5 <= -0.05), "Baisse de 5 jours >= 5 %")
stats(valid & (ret5 <= -0.06), "Baisse de 5 jours >= 6 %")
stats(valid & (ret5 <= -0.05) & (rsi < 30), "Baisse >= 5 % ET RSI D1 < 30")
stats(valid & (ret5 <= -0.07), "Baisse de 5 jours >= 7 %")
stats(valid & (rsi < 25), "RSI D1 < 25")
# liste des episodes de baisse >= 6 %
print("\nEpisodes (baisse 5 j >= 6 %, 1 par episode) :")
last=-99
for i in range(60,n-6):
    if valid[i] and ret5[i] <= -0.06 and i-last>=10:
        last=i
        mh=(h[i+1:i+6].max()-c[i])/atr[i]; ml=(c[i]-l[i+1:i+6].min())/atr[i]
        print("  ", dt.datetime.utcfromtimestamp(int(t[i])).date(), f"ret5 {ret5[i]*100:+.1f} % RSI {rsi[i]:.0f} | rebond max 5 j {mh:+.2f} ATR | nouveau creux {ml:+.2f} ATR")
mt5.shutdown()
