# Force des devises calculee depuis MT5 (lecture seule), facon « currency index » de Mataf :
# pour chaque devise, moyenne de ses variations contre les 7 autres (28 paires), sur clotures D1.
# Carte de contexte, PAS un signal : le test du 30/10/2026 n'a trouve aucun avantage aux classements de devises.
# Usage : python -u force_devises.py [--md fichier.md]   (ajoute le bloc au fichier s'il est donne)
import MetaTrader5 as mt5, numpy as np, sys, datetime as dt
sys.stdout.reconfigure(encoding="utf-8")
DEV = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
NOMS = {"USD": "dollar US", "EUR": "euro", "GBP": "livre", "JPY": "yen", "CHF": "franc suisse",
        "CAD": "dollar canadien", "AUD": "dollar australien", "NZD": "dollar néo-zélandais"}
ORDRE = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]  # ordre de cotation standard
N = 130  # bougies D1 chargees

mt5.initialize(path=r"C:\Program Files\FTMO Global Markets MT5 Terminal\terminal64.exe")
closes = {}
for i, a in enumerate(ORDRE):
    for b in ORDRE[i + 1:]:
        s = a + b
        r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_D1, 1, N)  # 1 = on ignore la bougie du jour non fermee
        if r is None or len(r) < N:
            print(f"  (paire {s} indisponible)"); continue
        closes[(a, b)] = {int(x["time"]): x["close"] for x in r}
mt5.shutdown()

jours = sorted(set.intersection(*[set(v) for v in closes.values()]))
date_fin = dt.datetime.utcfromtimestamp(jours[-1]).strftime("%d/%m/%Y")

def force(k_debut, k_fin):
    """Variation moyenne (%) de chaque devise contre les autres entre deux index de jours."""
    out = {c: [] for c in DEV}
    for (a, b), v in closes.items():
        ch = (np.log(v[jours[k_fin]]) - np.log(v[jours[k_debut]])) * 100
        out[a].append(ch); out[b].append(-ch)
    return {c: float(np.mean(x)) for c, x in out.items()}

f1, f5, f20 = force(-2, -1), force(-6, -1), force(-21, -1)
f20_avant = force(-26, -6)  # la meme mesure 20 j, il y a 5 jours : sert a dire si ca monte ou descend
classement = sorted(DEV, key=lambda c: f20[c], reverse=True)

def tendance(c):
    d = f20[c] - f20_avant[c]
    return "monte" if d > 0.3 else ("baisse" if d < -0.3 else "stable")

lignes = [f"## Force des devises (calcul MT5, clôtures D1 jusqu'au {date_fin})", "",
          "Pour chaque devise : variation moyenne contre les 7 autres (28 paires). + = la devise s'est renforcée.", "",
          "| Rang | Devise | 1 jour | 5 jours | 20 jours | Sens sur 5 jours |", "|---|---|---|---|---|---|"]
for i, c in enumerate(classement, 1):
    v = lambda x: f"{x:+.2f}".replace(".", ",")
    lignes.append(f"| {i} | {c} ({NOMS[c]}) | {v(f1[c])} % | {v(f5[c])} % | **{v(f20[c])} %** | {tendance(c)} |")
lignes += ["", f"Plus forte sur 20 jours : **{classement[0]}** ; plus faible : **{classement[-1]}**. "
           "Carte de contexte, pas un signal (test du 30/10/2026 : aucun avantage aux classements de devises).", ""]
texte = "\n".join(lignes)
print(texte)
if "--md" in sys.argv:
    with open(sys.argv[sys.argv.index("--md") + 1], "a", encoding="utf-8") as fh:
        fh.write("\n" + texte + "\n")
