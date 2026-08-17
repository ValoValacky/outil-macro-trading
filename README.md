# Force macro des devises — Dashboard

Outil educatif qui classe les devises majeures (USD, EUR, GBP, JPY, AUD, NZD, CAD, CHF)
par force macro relative, a partir de donnees publiques et gratuites :

- **BIS** (Bank for International Settlements) : taux directeurs (`stats.bis.org`, dataset `WS_CBPOL`)
- **OECD** (SDMX API) : inflation (CPI), croissance du PIB, taux de chomage (`sdmx.oecd.org`)
- **CFTC** (Commitment of Traders) : positionnement net des grands speculateurs sur futures de
  devises (`publicreporting.cftc.gov`, dataset "Legacy Futures Only")
- **Yahoo Finance** (`yfinance`) : prix quotidiens des paires de devises (confirmation technique)
  et volume reel des futures CME de devises (Volume Spread Analysis)

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
  technical.py     -> structure de marche, MM, RSI, niveaux cles (Yahoo Finance)
  vsa.py           -> Volume Spread Analysis sur futures CME (Yahoo Finance)
scoring/
  engine.py        -> transformation indicateurs -> scores -3/+3 -> classement + matrice de paires
  history.py       -> reconstruction de l'historique du score (sans stockage persistant)
journal/
  <PAIRE>/<DATE>.md -> entrees du journal de trading (voir section dediee)
journal_incoming/  -> depot pour les captures d'ecran en attente d'integration au journal
app.py             -> dashboard Streamlit
```

## Journal de trading

Section "Journal de trading" du dashboard (bascule via le menu "Vue" dans la
barre laterale) : historique chronologique de toutes les analyses, un onglet
par paire, la plus recente en premier.

**Stockage** : chaque entree est un fichier Markdown avec front-matter dans
`journal/<PAIRE>/<DATE>.md` :

```
---
pair: EUR/JPY
date: 2026-08-16
type: Graphique MT5 (COT Strength+RSI / Larry Williams)
image: 2026-08-16.png
---

## 1. ...
```

Le champ `image` (optionnel, `null` si absent) pointe vers un fichier dans le
meme dossier que l'entree. Contrairement aux donnees macro (recalculees a la
volee), le journal est un **vrai historique** qui doit persister : il est donc
versionne dans le depot Git, jamais genere a la demande - c'est ce qui lui
permet de survivre aux redemarrages de l'hebergement gratuit.

**Ajout d'une nouvelle entree** : dépose la capture d'ecran dans
`journal_incoming/` ; lors de l'analyse suivante, le fichier est deplace vers
`journal/<PAIRE>/<DATE>.png`, un `.md` est ecrit a cote, et les deux sont
commit + push - la nouvelle entree apparait alors automatiquement dans le
dashboard deploye.

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

### Confirmation technique par paire

`data_sources/technical.py` recupere les prix quotidiens (Yahoo Finance,
ticker `BASEQUOTE=X`) d'une paire choisie et calcule :
- **Structure de marche** : detection des swing highs/lows (fenetre de 5
  bougies de part et d'autre) -> haussiere si Higher High + Higher Low,
  baissiere si Lower High + Lower Low, sinon range.
- **Moyennes mobiles** 20/50 : alignement prix > MM20 > MM50 (haussier),
  inverse (baissier), ou mixte.
- **RSI(14)** : avec flag surachat (>=70) / survente (<=30).
- **Proximite d'un niveau cle** : distance au plus haut/bas sur 60 jours
  (alerte si <1.5%, zone ou une reaction est plus probable).

Le dashboard combine ce biais technique avec le biais macro (cellule de la
heatmap) et la dynamique COT des deux devises de la paire, dans un seul
resume de confluence. La paire par defaut proposee est celle avec le plus
gros ecart dans la heatmap (`matrix.stack().idxmax()`).

**Limite assumee** : le forex spot est un marche OTC decentralise sans volume
reel. Aucune donnee de volume n'est utilisee ici (voir roadmap "Volume/VSA").

### Volume Spread Analysis (VSA) sur futures CME

`data_sources/vsa.py` recupere le volume reel des futures de devises (CME,
via Yahoo Finance : `6E=F`, `6B=F`, `6J=F`, `6A=F`, `6N=F`, `6C=F`, `6S=F` -
pas de contrat direct pour l'USD). Pour chaque bougie recente, on calcule :
- `volume_ratio` = volume / moyenne mobile 20 jours du volume
- `spread_ratio` = (high-low) / moyenne mobile 20 jours du spread
- `close_position` = position de la cloture dans le range de la bougie

Et on en deduit des patterns VSA objectivables : **climax** (volume et
spread tres au-dessus de la moyenne, cloture qui contredit la direction du
mouvement -> essoufflement possible), **no demand/no supply** (mouvement sur
volume anormalement faible -> manque de conviction), **effort sans resultat**
(gros volume, peu de mouvement de prix -> absorption possible), **spring** /
**up-thrust** (faux breakout d'un plus bas/haut recent qui se retourne sur
volume).

**Limite assumee et importante** : la VSA (methode Wyckoff / Tom Williams)
est fondamentalement une lecture discretionnaire de contexte. Ces regles
formalisent les cas les plus objectivables, mais restent une aide a la
lecture, pas un signal fiable a 100% - a combiner avec le reste (macro, COT,
structure technique), jamais seule.

### Backtest historique (analyse hors-dashboard)

`scoring/backtest.py` (script d'analyse, pas branche sur le dashboard en
production - trop lent/couteux en requetes pour tourner a chaque session
utilisateur) teste si suivre la paire suggeree par l'outil chaque semaine
(devise la plus forte vs la plus faible du score composite, tenue 4
semaines) aurait ete rentable, et decompose la performance facteur par
facteur (isole chaque indicateur du score composite).

**Resultats (52 semaines testees, horizon 4 semaines, aout 2026)** :

| Strategie | Rendement moyen | Taux de reussite |
|---|---|---|
| Composite (poids actuels) | -0.31% | 42.3% |
| Taux directeur seul | -0.31% | 32.7% |
| Inflation seule | -0.63% | 38.5% |
| COT seul | -0.08% | 38.5% |
| PIB seul | +0.13% | 57.7% |
| Chomage seul | +0.44% | 61.7% |

**Constat** : sur cette periode, le chomage et le PIB (les 2 facteurs les
moins ponderes actuellement : 10% et 15%) performent isolement mieux que le
taux directeur (le plus pondere : 30%), qui a le pire taux de reussite.

**Decision prise (avec l'utilisateur)** : ne pas re-ponderer le scoring sur
la base de ce seul test. Avec ~52 semaines qui se chevauchent (fenetre de 4
semaines glissante d'une semaine sur l'autre), l'echantillon independant
reel est trop petit (~13 observations) pour recalibrer sans tomber dans le
surapprentissage (on testerait et calibrerait sur les memes donnees). Le
resultat est garde comme piste de recherche a revalider avec des donnees
fraiches (hors-echantillon) avant tout changement de poids.

## Roadmap / evolutions possibles

- Revalider (ou infirmer) le constat ci-dessus avec des donnees fraiches
  dans quelques mois, avant d'envisager un changement de ponderation.
- Remplacer/completer OECD par une source payante (ex: Trading Economics) si
  le besoin de donnees plus fraiches/plus larges se confirme.
- Deploiement : voir section dediee ci-dessous.

## Deploiement (Streamlit Community Cloud, gratuit)

1. Pousser ce dossier dans un repo GitHub (public ou prive).
2. Sur https://share.streamlit.io, connecter le repo et pointer sur `app.py`.
3. Aucune cle API n'est necessaire pour cette version (OECD + BIS sont libres d'acces).
4. Si une cle est ajoutee plus tard (ex: Trading Economics), la stocker dans
   les "Secrets" Streamlit (`st.secrets`), jamais en dur dans le code.
