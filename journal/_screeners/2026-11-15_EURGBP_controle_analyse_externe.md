# Contrôle d'une analyse externe EURGBP H4 (VENTE) — dimanche 15/11/2026

Analyse reçue : vente sur rebond 0,8370-0,8390, SL 0,8425, TP1 0,8335, TP2 0,8300 (niveaux déduits de GBP/EUR, non vérifiés par la source).
Données MT5 FTMO, clôture de vendredi 13/11 : 0,83415. ATR D1 26 pip, ATR H4 10 pip. Spread 1,7 pip. Swap vendeur +0,2.

## Les niveaux contre MT5
| Point de l'analyse | MT5 | Verdict |
|---|---|---|
| Range 0,833-0,837 | vrai range depuis le 30/10 : 0,83208 (05/11, plus bas d'un an) - 0,83727 (10/11) | confirmé |
| Entrée 0,8370-0,8390 | haut du range 0,83727 ; MM20 D1 0,83604 ; Kijun D1 0,8403 ; EMA200 H4 0,8407 | cohérent (vente sous la Kijun D1), mais 0,8390 pas touché depuis le 29/10 au moins |
| SL 0,8425 | au-dessus de la Kijun D1 et de l'EMA200 H4 ; 35 à 55 pip = 1,3 à 2,1 ATR D1 | conforme à la règle SL (>= 1,2 ATR) |
| TP1 0,8335 | 6 pip sous le prix de vendredi | atteignable seulement si l'entrée est remplie ; pas de sens au prix actuel |
| TP2 0,8300 | SOUS le plus bas d'un an 0,83208 | demande une cassure du plancher : obstacle |
| R/R 1:1 / 1:1,8 | recalculé depuis 0,8380 : risque 45 pip, TP1 46, TP2 80 | exact |

## Les 3 screeners
- Moyennes : H4 baisse 3/4, D1 baisse 4/4 (prix sous MM20/50/100/200 D1 et sous la MM20 W1 0,8501) → confirme le SENS vendeur.
- Doubles sommets/creux D1 : rien.
- Quasimodo H4 : ACHAT en attente à 0,8330 (SL 0,83197, TP1 0,83591, lecture OK), 1,1 ATR sous le prix : contre-tendance, mais marque un support pile sur le TP1 de l'analyse.
- Valoalgo : aucun signal.
- RSI D1 30 (bas), volumes sous la moyenne depuis le 09/11 (0,79 / 0,95 / 0,71 / 0,99 / 0,91) : le prix se tasse au-dessus du plancher, sans vente forte.

## Règles devises (MT5 au 15/11 00h47)
- Vendre EURGBP = euro VENDU + livre ACHETÉE.
- EUR : acheté 1 fois (position EURNZD) → **INTERDIT règle 1** tant que EURNZD est ouvert.
- GBP : acheté 1 fois (BUY LIMIT GBPAUD) → ce serait le 2e : 400 € maximum (règle 2).
- Lot si autorisé : risque 400 € = 0,25 % de 158 137 € ; ≈ 12,0 € par pip et par lot → 0,61 lot (entrée 0,8370) à 1,14 lot (entrée 0,8390).

## Chiffres économiques vérifiés sur le net (15/11)
| Affirmation de l'analyse | Source trouvée | Verdict |
|---|---|---|
| GBP/EUR au plus haut depuis 20 mois | MT5 : EURGBP 0,83208 le 05/11, jamais aussi bas depuis le 28/03/2025 (0,83153) = ~20 mois | confirmé |
| CPI UK précédent 3,5 % | ONS : 3,5 % en septembre 2026 (3,1 % en août), services 3,7 %, core 2,8 % | confirmé |
| Prévision BoE 3,73 % | chiffre exact introuvable (graphique du rapport sans valeurs). Commons Library : BoE ~3,75 % en moyenne au T4 2026 ; Trésor (moyenne des prévisionnistes) 3,6 % au T4 (3,3-3,9 %) | plausible, non vérifié au centième |
| CPI UK mercredi 8h00 Paris | ONS : publication du CPI d'octobre le 18/11 (7h00 Londres = 8h00 Paris, 9h00 serveur) | confirmé |
| Emploi UK mardi 8h00 Paris | ONS : marché du travail le 17/11 à 7h00 Londres ; dernier chômage 4,7 % (juin-août) | confirmé |
| HICP mercredi | Eurostat : HICP final d'octobre le 18/11 ; estimation rapide 4,0 % (plus haut depuis 3 ans) | confirmé |
| Ventes au détail UK vendredi 8h00 Paris | ONS : 20/11 à 7h00 Londres ; septembre -0,4 % | confirmé |

Contexte trouvé en plus : BoE a MONTÉ son taux à 4,0 % le 5/11 (vote 8-1, un membre voulait 4,25 %), et dit qu'une nouvelle hausse « peut être justifiée » ; inflation vue à ~5 % mi-2027 (énergie, conflit au Moyen-Orient). BCE inchangée à 2,50 % le 29/10. Écart de taux en faveur de la livre = cohérent avec EURGBP en baisse. Risque inverse : l'inflation de la zone euro (4,0 %) dépasse celle du Royaume-Uni (3,5 %) → une hausse de la BCE en décembre ferait remonter EURGBP.

## Verdict
Sens confirmé par nos outils, niveaux plausibles, mais : TP1 quasi au prix actuel, TP2 sous le plus bas d'un an avec un achat Quasimodo et un RSI bas juste au-dessus, et INTERDIT règle 1 tant que EURNZD est ouvert. Candidat pour l'analyse de lundi, pas d'ordre à poser. Chiffres macro (prévision CPI BoE 3,73 %) non vérifiés.
Annonces : emploi UK mardi 8h00 Paris (9h00 serveur), CPI UK mercredi 8h00 Paris (9h00 serveur) : pas de nouvel ordre GBP de 7h00 à 8h00 Paris ces jours-là.
