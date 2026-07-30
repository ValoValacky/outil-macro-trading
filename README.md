# Force macro des devises — Dashboard

Outil educatif qui classe les devises majeures (USD, EUR, GBP, JPY, AUD, NZD, CAD, CHF)
par force macro relative, a partir de donnees publiques et gratuites :

- **BIS** (Bank for International Settlements) : taux directeurs (`stats.bis.org`, dataset `WS_CBPOL`)
- **OECD** (SDMX API) : inflation (CPI), croissance du PIB, taux de chomage (`sdmx.oecd.org`)
- **CFTC** (Commitment of Traders) : positionnement net des grands speculateurs sur futures de
  devises (`publicreporting.cftc.gov`, dataset "Legacy Futures Only")

Aucune de ces sources ne necessite de cle API.

## Avertissement

Cet outil est **informatif et pedagogique**. Il ne constitue pas un conseil en
investissement personnalise et ne genere aucun signal d'execution automatique.
La grille de scoring est une simplification pedagogique de la logique macro
utilisee par les traders institutionnels ; elle ne garantit aucun resultat.

## Installation

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Architecture

```
data_sources/
  common.py       -> mapping devise <-> code pays (OECD/BIS), helper HTTP avec retry
  bis_rates.py     -> taux directeurs (BIS)
  oecd_macro.py    -> CPI, chomage, PIB (OECD)
  cot_report.py    -> positionnement net des grands speculateurs (CFTC)
scoring/
  engine.py        -> transformation indicateurs -> scores -3/+3 -> classement + matrice de paires
  history.py       -> reconstruction de l'historique du score (sans stockage persistant)
app.py             -> dashboard Streamlit
```

## Notes techniques importantes

### Rate limiting (OECD)

L'API OECD applique une limite de requetes par minute. Le helper `http_get`
(dans `data_sources/common.py`) retente automatiquement avec un backoff
progressif sur les reponses `429`. Le dashboard met aussi en cache les
donnees 6h (`st.cache_data(ttl=...)` dans `app.py`) pour eviter de re-solliciter
les API a chaque interaction utilisateur.

### Cles SDMX par indicateur

Les cles ci-dessous ont ete determinees par exploration des dataflows/codelists
OECD (aucune documentation officielle simple ne les liste directement) :

| Indicateur | Dataflow | Cle (template) |
|---|---|---|
| CPI (glissement annuel) | `DSD_PRICES@DF_PRICES_ALL` (COICOP1999) ou `DSD_PRICES_COICOP2018@DF_PRICES_C2018_ALL` | `{area}.{FREQ}.N.CPI.PA._T.N.GY` |
| Chomage (CVS, 15+) | `DSD_LFS@DF_IALFS_UNE_M` | `{area}.UNE_LF_M.PT_LF_SUB._Z.Y._T.Y_GE15._Z.{FREQ}` |
| PIB (glissement annuel) | `DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_OECD` | `Q.Y.{area}.S1.S1.B1GQ._Z._Z._Z.PC.L.GY.T0102` |

**Cas particuliers geres automatiquement (fallback en cascade) :**
- **JPY** : le CPI japonais n'est publie que sous classification COICOP2018 (pas COICOP1999).
- **NZD** : le CPI et le chomage neo-zelandais sont publies **trimestriellement**, pas mensuellement.
- **EUR** : l'agregat "zone euro" n'existe pas dans les dataflows de PIB ni de chomage utilises ->
  l'Allemagne (`DEU`) sert de proxy pour ces deux indicateurs (annotee comme telle dans les
  donnees, premiere economie de la zone). Le CPI, lui, est bien disponible en agregat zone euro (`EA20`).

### Codes pays

| Devise | Code OECD (`REF_AREA`) | Code BIS (`REF_AREA`) | Contrat CFTC (COT) |
|---|---|---|---|
| USD | USA | US | USD INDEX (proxy, panier de devises) |
| EUR | EA20 (proxy DEU pour le PIB/chomage) | XM | EURO FX |
| GBP | GBR | GB | BRITISH POUND |
| JPY | JPN | JP | JAPANESE YEN |
| AUD | AUS | AU | AUSTRALIAN DOLLAR |
| NZD | NZL | NZ | NZ DOLLAR |
| CAD | CAN | CA | CANADIAN DOLLAR |
| CHF | CHE | CH | SWISS FRANC |

## Methodologie de scoring (resume)

Chaque indicateur recoit un score de -3 (tres defavorable a la devise) a +3
(tres favorable), pondere puis additionne :

| Indicateur | Poids | Logique |
|---|---|---|
| Taux directeur (variation) | 30% | Hausse recente = positif (flux de capitaux) |
| Positionnement COT (dynamique) | 25% | Momentum 4 sem. + retournement de signe, pas le niveau brut |
| Inflation (niveau + momentum) | 20% | Ecart a la cible ~2%, qui se creuse = negatif |
| Croissance du PIB (niveau) | 15% | Croissance forte = positif |
| Chomage (variation) | 10% | Baisse recente = positif |

Voir `scoring/engine.py` pour le detail exact des seuils.

### Positionnement institutionnel (COT) : lecture par dynamique, pas par niveau

Le tableau de bord ne score pas le niveau absolu du positionnement des grands
speculateurs (qui est souvent durablement positif ou negatif pour certaines
devises), mais sa **dynamique recente** :
- **Franchissement du zero** (le net passe de positif a negatif ou l'inverse
  sur les 12 dernieres semaines) = signal le plus fort, score maximal (+3/-3).
- Sinon, la **variation sur 4 semaines** determine le score : un biais qui se
  renforce compte plus qu'un biais deja ancien et stable.

Quatre etiquettes qualitatives resument la situation (`classify_momentum`) :
`retournement haussier/baissier`, `renforcement haussier/baissier`,
`affaiblissement haussier/baissier` — avec un code couleur dans le dashboard.

### Historique du score sans base de donnees

`scoring/history.py` ne stocke rien : il recalcule le score composite "tel
qu'il aurait ete" a chaque date passee (fenetre glissante "as of" sur les
memes series BIS/OECD/CFTC deja recuperees). Ca evite toute fragilite liee
au caractere ephemere de l'hebergement gratuit (conteneur qui redemarre =
perte d'un fichier local), au prix d'un peu plus de calcul a chaque
chargement.

## Roadmap / evolutions possibles

- Ajouter une couche de confirmation technique (structure de marche, moyennes
  mobiles) comme filtre pedagogique complementaire au score macro.
- Volume/VSA via les futures CME (le forex spot n'a pas de volume centralise) :
  proxy raisonnable mais pas une lecture VSA "pure" du spot.
- Backtesting historique des poids du scoring pour les valider empiriquement.
- Remplacer/completer OECD par une source payante (ex: Trading Economics) si
  le besoin de donnees plus fraiches/plus larges se confirme.
- Deploiement : voir section dediee ci-dessous.

## Deploiement (Streamlit Community Cloud, gratuit)

1. Pousser ce dossier dans un repo GitHub (public ou prive).
2. Sur https://share.streamlit.io, connecter le repo et pointer sur `app.py`.
3. Aucune cle API n'est necessaire pour cette version (OECD + BIS sont libres d'acces).
4. Si une cle est ajoutee plus tard (ex: Trading Economics), la stocker dans
   les "Secrets" Streamlit (`st.secrets`), jamais en dur dans le code.
