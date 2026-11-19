"""Tests des règles du robot exécutant (sans MT5). Lancer : python -m pytest -q test_robot_executant.py"""
import datetime as dt
import robot_executant as r

T = dt.datetime


def test_cote_limit_et_stop():
    # BUY LIMIT : le prix doit être AU-DESSUS de l'entrée
    assert r.cote_ok("buy", "limit", 1.0, 1.0010, 1.0011)[0]
    assert not r.cote_ok("buy", "limit", 1.0, 0.9990, 0.9991)[0]
    # SELL LIMIT : le prix doit être SOUS l'entrée (cas EURUSD A : entrée 1,1100, prix 1,0974)
    assert r.cote_ok("sell", "limit", 1.1100, 1.0974, 1.0975)[0]
    # SELL STOP : le prix doit être AU-DESSUS (cas EURUSD B : entrée 1,0975, prix 1,0974 -> pas posable)
    assert not r.cote_ok("sell", "stop", 1.0975, 1.0974, 1.0975)[0]
    # BUY STOP : le prix doit être SOUS l'entrée (cas GBPAUD B : entrée 1,9535, prix 1,9601 -> pas posable)
    assert not r.cote_ok("buy", "stop", 1.9535, 1.9600, 1.9602)[0]


def test_tp_deja_passe():
    assert r.tp_deja_passe("sell", 1.09698, 1.09640, 1.09645)      # ordre HORIIZON du 18/11
    assert not r.tp_deja_passe("buy", 2.4150, 2.3987, 2.3989)             # GBPNZD A : TP encore au-dessus
    assert r.tp_deja_passe("buy", 2.3900, 2.3987, 2.3989)


def test_lot_arrondi_vers_le_bas():
    # 250 $ de risque, 161 pip x 5,50 $ = 885,5 $ par lot -> 0,28 lot (jamais 0,29)
    assert r.lot_pour_risque(250, 885.5, 0.01, 0.01, 50) == 0.28
    assert r.lot_pour_risque(1, 885.5, 0.01, 0.01, 50) == 0.0          # trop petit
    assert r.lot_pour_risque(250, 0, 0.01, 0.01, 50) == 0.0            # SL invalide


def test_correspondance_ordre():
    assert r.correspond("GBPNZD", "buy", "limit", 2.3811, "GBPNZD", 2, 2.38110)
    assert not r.correspond("GBPNZD", "buy", "limit", 2.3811, "GBPNZD", 3, 2.38110)    # mauvais sens
    assert not r.correspond("EURUSD", "sell", "limit", 1.1100, "EURUSD", 3, 1.10071)   # ordre HORIIZON != plan A


def test_condition_sur_clotures():
    d1 = [(T(2026, 11, 16), 1.09971, 100, 120.0), (T(2026, 11, 17), 1.09995, 90, 120.0)]
    ok, txt, b = r.condition({"type": "d1_close_below", "level": 1.099}, d1, [])
    assert ok is False
    ok, txt, b = r.condition({"type": "d1_close_below", "level": 1.0998}, d1, [])
    assert ok and b[0] == T(2026, 11, 16)
    assert r.condition({"type": "none"}, [], [])[0] is True
    assert r.condition({"type": "no_position", "magic": 1}, [], [])[0] is None   # laissé à l'humain


def test_volume_faible_seulement_sur_la_derniere_cloture():
    b1 = (T(2026, 11, 16), 1.0, 80, 100.0)
    b2 = (T(2026, 11, 17), 1.0, 150, 100.0)
    assert r.volume_faible(b1, b1)          # arme ce soir avec volume faible -> attendre
    assert not r.volume_faible(b1, b2)      # une clôture de plus est passée -> OK
    assert not r.volume_faible(b2, b2)      # volume normal


def test_annonce_dans_l_heure():
    now = dt.datetime(2026, 11, 18, 18, 30, tzinfo=dt.timezone.utc)
    ev = [{"title": "FOMC Minutes", "country": "USD", "impact": "High", "when_utc": "2026-11-18T19:00:00+00:00"},
          {"title": "Lagarde", "country": "EUR", "impact": "Medium", "when_utc": "2026-11-18T18:45:00+00:00"}]
    assert r.annonce_proche({"EUR", "USD"}, now, ev)
    assert r.annonce_proche({"EUR", "GBP"}, now, ev) is None           # Medium ignoré, USD absent
    assert r.annonce_proche({"USD"}, now - dt.timedelta(hours=2), ev) is None


# ---------------------------------------------------------------- phase 2 (19/11/2026)
def test_exclusifs_lus_dans_le_texte():
    p = {"id": "B", "lot_texte": "SELL STOP 0,29 lot = 0,25 %; jamais en même temps que A"}
    assert r.exclusifs(p) == {"A"}
    assert r.exclusifs({"id": "A", "condition_texte": "POSABLE. Jamais en même temps que B."}) == {"B"}
    assert r.exclusifs({"id": "A", "exclusif_avec": ["C"]}) == {"C"}
    assert r.exclusifs({"id": "A"}) == set()


def test_stop_minuscule_refuse():
    # structure-bot 13/11 : stop 1,5 pip avec ATR D1 ~ 70 pip -> refus
    assert r.juge_sl(0.00015, 0.0070)[0] == "refus"
    # GBPAUD A : 150 pip, ATR 127 -> 1,18 ATR -> à vérifier
    assert r.juge_sl(0.0150, 0.0127)[0] == "court"
    assert r.juge_sl(0.0085, 0.0070)[0] == "ok"
    assert r.juge_sl(0.01, None)[0] == "ok"


def test_reposer_cas_gbpnzd():
    # ordre 0,39 lot, SL 2,3650 alors que le plan veut 0,28 lot -> reposer
    e, rep = r.ecarts_ordre(0.39, 0.28, 0.01, 2.3650, 2.3650, 2.4150, 2.4150, 0.0002)
    assert rep and "risque trop gros" in e[0]
    # SL déplacé sur l'ordre -> reposer
    assert r.ecarts_ordre(0.28, 0.28, 0.01, 2.3700, 2.3650, 2.4150, 2.4150, 0.0002)[1]
    # lot un peu plus petit (GBPAUD 0,23 au lieu de 0,24) -> pas d'écart (tolérance d'un pas)
    assert r.ecarts_ordre(0.23, 0.24, 0.01, 1.911, 1.911, 1.9495, 1.9495, 0.0002) == ([], False)
    # lot nettement plus petit -> signalé, pas reposé
    e, rep = r.ecarts_ordre(0.10, 0.28, 0.01, 1.0, 1.0, 1.1, 1.1, 0.0002)
    assert e and not rep


def test_garde_jour():
    assert r.garde_jour(0) == "ok"
    assert r.garde_jour(1199) == "ok"
    assert r.garde_jour(1200) == "bloque"
    assert r.garde_jour(3000) == "stop"
    assert r.garde_jour(None) == "bloque"


def test_envoi_verrouille(tmp_path):
    assert not r.envoi_autorise(tmp_path)[0]
    (tmp_path / "autorisation.json").write_text('{"envoi_reel": false}', encoding="utf-8")
    assert not r.envoi_autorise(tmp_path)[0]
    (tmp_path / "autorisation.json").write_text('{"envoi_reel": true, "valide_par": "utilisatrice"}', encoding="utf-8")
    assert r.envoi_autorise(tmp_path)[0]
