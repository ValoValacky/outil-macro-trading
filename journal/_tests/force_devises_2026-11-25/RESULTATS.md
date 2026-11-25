# Résultats — filtre « force des devises » (25/11/2026)

Règles : REGLES.md (fixées avant). Données : 28 paires MT5, H1 et D1, lundis de 2015 à novembre 2026. Script : test_filtre_force.py ; détail des trades : resultats_trades.csv ; sortie brute : sortie.txt.

**Verdict : PAS UTILE.** Les deux règles échouent aux critères fixés à l'avance.

| Règle | Trades gardés | Trades retirés | Avance des gardés | Périodes favorables | Deux moitiés des paires |
|---|---|---|---|---|---|
| 1. Jamais contre le classement 20 j | 12 362, −0,021 R | 12 163, −0,018 R | **−0,003 R** (critère : +0,05) | 1 sur 3 | signes opposés |
| 2. Jamais acheter un 7e/8e ni vendre un 1er/2e | 14 055, −0,018 R | 10 470, −0,021 R | **+0,003 R** | 2 sur 3 | signes opposés |

- Tous les trades : 24 525, −0,019 R en moyenne (≈ le coût de 0,025 R) : les trades de base ne gagnent rien, comme attendu.
- Par écart de rang (de −7 à +7) : toutes les moyennes entre −0,05 et +0,00 R, sans pente. Acheter la plus forte contre la plus faible (+7) : −0,046 R ; l'inverse (−7) : +0,003 R. Différences dans le bruit (≈ 0,05 R d'erreur sur ~450 trades).
- 2023-2026 : les trades « dans le sens » font même un peu moins bien (−0,035 contre −0,009 R).

**Conclusion pratique** : le classement de force des devises explique le mouvement (qui pousse, qui freine) mais ne dit ni « acheter », ni « vendre », ni « ne pas acheter ». Il reste une carte de lecture dans le suivi ; aucune règle ajoutée au contrôle devise par devise. Confirme le test du 30/10 (classements macro sans avantage).
