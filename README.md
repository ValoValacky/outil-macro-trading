# Force macro des devises — Dashboard

Outil educatif qui classe les devises majeures (USD, EUR, GBP, JPY, AUD, NZD, CAD, CHF)
par force macro relative, a partir de donnees publiques et gratuites :

- **BIS** (Bank for International Settlements) : taux directeurs (`stats.bis.org`, dataset `WS_CBPOL`)
- **OECD** (SDMX API) : inflation (CPI), croissance du PIB, taux de chomage (`sdmx.oecd.org`)

Aucune de ces deux sources ne necessite de cle API.

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
scoring/
  engine.py        -> transformation indicateurs -> scores -3/+3 -> classement + matrice de paires
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

| Devise | Code OECD (`REF_AREA`) | Code BIS (`REF_AREA`) |
|---|---|---|
| USD | USA | US |
| EUR | EA20 (proxy DEU pour le PIB) | XM |
| GBP | GBR | GB |
| JPY | JPN | JP |
| AUD | AUS | AU |
| NZD | NZL | NZ |
| CAD | CAN | CA |
| CHF | CHE | CH |

## Methodologie de scoring (resume)

Chaque indicateur recoit un score de -3 (tres defavorable a la devise) a +3
(tres favorable), pondere puis additionne :

| Indicateur | Poids | Logique |
|---|---|---|
| Taux directeur (variation) | 40% | Hausse recente = positif (flux de capitaux) |
| Inflation (niveau + momentum) | 25% | Ecart a la cible ~2%, qui se creuse = negatif |
| Croissance du PIB (niveau) | 20% | Croissance forte = positif |
| Chomage (variation) | 15% | Baisse recente = positif |

Voir `scoring/engine.py` pour le detail exact des seuils.

## Roadmap / evolutions possibles

- Ajouter une couche de confirmation technique (structure de marche, moyennes
  mobiles) comme filtre pedagogique complementaire au score macro.
- Remplacer/completer OECD par une source payante (ex: Trading Economics) si
  le besoin de donnees plus fraiches/plus larges se confirme.
- Deploiement : voir section dediee ci-dessous.

## Deploiement (Streamlit Community Cloud, gratuit)

1. Pousser ce dossier dans un repo GitHub (public ou prive).
2. Sur https://share.streamlit.io, connecter le repo et pointer sur `app.py`.
3. Aucune cle API n'est necessaire pour cette version (OECD + BIS sont libres d'acces).
4. Si une cle est ajoutee plus tard (ex: Trading Economics), la stocker dans
   les "Secrets" Streamlit (`st.secrets`), jamais en dur dans le code.
