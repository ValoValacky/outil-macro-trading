# Test du filtre « force des devises » — règles fixées AVANT de regarder les résultats (25/11/2026)

**Question de l'utilisatrice** : le classement de force des devises (force_devises.py, façon indice Mataf) peut-il nous dire « pas acheter / pas vendre » ?

**Règle testée** : ne jamais prendre un trade qui va CONTRE le classement 20 jours, c'est-à-dire acheter une devise moins forte que celle qu'on vend. Variante plus stricte : ne jamais acheter une devise classée 7e ou 8e, ni vendre une devise classée 1re ou 2e (« contre un extrême »).

## Trades de base (imitent nos plans : entrée sur rebond, ordre en attente)
- Univers : les 28 paires des 8 devises (MT5 FTMO, lecture seule), de 2015 à novembre 2026.
- Chaque lundi à l'ouverture, pour chaque paire, on simule DEUX ordres : un achat et une vente.
  - Achat = BUY LIMIT 0,5 ATR D1 sous l'ouverture du lundi ; vente = SELL LIMIT 0,5 ATR au-dessus.
  - Valable jusqu'au vendredi 23h serveur. Jamais touché = pas de trade.
  - SL 1,2 ATR au-delà de l'entrée ; TP 1,8 ATR (R/R 1,5). Si le TP n'est pas touché, sortie au prix du vendredi 23h serveur.
  - Si une même bougie H1 touche le SL et le TP : on compte le SL (prudence).
  - Coût : 0,03 ATR par trade (écart + commission), soit ≈ 0,025 R.
  - ATR D1 14 jours, calculé sur les clôtures jusqu'au vendredi précédent.
- Force des devises : même calcul que force_devises.py (variation moyenne d'une devise contre les 7 autres sur 20 jours), sur les clôtures D1 jusqu'au vendredi précédent (rien du futur).

## Groupes comparés
- « dans le sens » : la devise achetée est mieux classée que la devise vendue.
- « contre » : l'inverse.
- « contre un extrême » : achat d'une devise 7e ou 8e, ou vente d'une devise 1re ou 2e.

## Critère de verdict (fixé avant)
Le filtre est jugé UTILE seulement si les trois conditions sont vraies :
1. les trades gardés (« dans le sens ») font en moyenne au moins **+0,05 R de mieux** que les trades retirés (« contre ») ;
2. cette avance existe dans **au moins 2 des 3 périodes** 2015-2018, 2019-2022, 2023-2026 ;
3. elle reste du même signe sur les deux moitiés des paires (classées par ordre alphabétique).
Même logique pour la variante « contre un extrême ».
Sinon : le classement reste une aide à la lecture, sans règle dans le contrôle devise par devise.

Aucun ordre envoyé. Lecture seule MT5.
