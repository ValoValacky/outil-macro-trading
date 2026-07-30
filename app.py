"""Dashboard - Force macro des devises majeures.

Outil educatif : classe les devises majeures par force macro relative
(taux directeur, inflation, croissance, chomage, positionnement institutionnel
COT) a partir de sources de donnees publiques et gratuites (OECD, BIS, CFTC).
Ce n'est PAS un conseil en investissement ni un generateur de signaux
d'execution automatique.
"""

import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data_sources.bis_rates import fetch_policy_rate_history, summarize_policy_rate
from data_sources.common import CURRENCIES
from data_sources.cot_report import classify_momentum, fetch_cot_history, summarize_cot_momentum
from data_sources.oecd_macro import (
    fetch_cpi_yoy,
    fetch_gdp_growth,
    fetch_unemployment_rate,
    summarize_series,
)
from scoring.engine import build_detail_table, build_pair_matrix, build_ranking, score_currency
from scoring.history import build_multi_currency_history

st.set_page_config(page_title="Force macro des devises", layout="wide")

CACHE_TTL_SECONDS = 6 * 3600  # les indicateurs macro bougent rarement plus d'une fois par jour
COT_WEEKS_BACK = 26
SCORE_HISTORY_WEEKS = 12

# Couleurs pour la lecture rapide de la dynamique COT (voir classify_momentum)
MOMENTUM_COLORS = {
    "retournement haussier": "#1a9850",
    "renforcement haussier": "#66bd63",
    "affaiblissement haussier": "#fee08b",
    "affaiblissement baissier": "#fdae61",
    "renforcement baissier": "#d73027",
    "retournement baissier": "#a50026",
    "indisponible": "#999999",
}


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_currency_raw(currency: str, start_period: str):
    """Recupere les series historiques brutes (une seule fois, mises en cache) -
    utilisees a la fois pour le score actuel et pour la reconstruction historique.

    Un leger espacement entre les appels OECD (meme hote) reduit le risque de
    declencher son rate-limit, notamment sur les hebergements gratuits a IP
    partagee (Streamlit Community Cloud) ou le quota peut deja etre entame
    par d'autres applications."""
    policy = fetch_policy_rate_history(currency)
    cpi = fetch_cpi_yoy(currency, start_period=start_period)
    time.sleep(1)
    gdp = fetch_gdp_growth(currency, start_period=start_period)
    time.sleep(1)
    unemployment = fetch_unemployment_rate(currency, start_period=start_period)
    cot = fetch_cot_history(currency, weeks_back=COT_WEEKS_BACK)

    return {
        "policy": policy,
        "cpi": cpi,
        "gdp": gdp,
        "unemployment": unemployment,
        "cot": cot,
    }


def main():
    st.title("Force macro des devises majeures")
    st.caption(
        "Outil educatif et informatif. Ne constitue pas un conseil en investissement. "
        "Sources : OECD (sdmx.oecd.org), BIS (stats.bis.org) et CFTC (publicreporting.cftc.gov), "
        "donnees publiques gratuites."
    )

    with st.sidebar:
        st.header("Parametres")
        selected_currencies = st.multiselect(
            "Devises a analyser", options=CURRENCIES, default=CURRENCIES
        )
        start_period = st.text_input("Debut de l'historique (AAAA-MM)", value="2023-01")
        st.caption(
            "Les donnees sont mises en cache "
            f"{CACHE_TTL_SECONDS // 3600}h pour respecter les limites de requetes "
            "des API publiques."
        )
        if st.button("Forcer le rafraichissement"):
            st.cache_data.clear()

    if not selected_currencies:
        st.warning("Selectionne au moins une devise dans le panneau de gauche.")
        return

    currency_scores = []
    cot_summaries = {}
    raw_data = {}
    errors = []
    with st.spinner("Recuperation des donnees macro et COT..."):
        for ccy in selected_currencies:
            try:
                raw = load_currency_raw(ccy, start_period)
                raw_data[ccy] = raw
                cot_summary = summarize_cot_momentum(raw["cot"])
                cot_summaries[ccy] = cot_summary
                currency_scores.append(
                    score_currency(
                        ccy,
                        policy_rate_summary=summarize_policy_rate(raw["policy"]),
                        cpi_summary=summarize_series(raw["cpi"]),
                        gdp_summary=summarize_series(raw["gdp"]),
                        unemployment_summary=summarize_series(raw["unemployment"]),
                        cot_summary=cot_summary,
                    )
                )
            except Exception as exc:
                errors.append((ccy, str(exc)))

    if errors:
        with st.expander(f"{len(errors)} devise(s) en erreur (source externe indisponible)"):
            for ccy, msg in errors:
                st.write(f"**{ccy}** : {msg}")

    if not currency_scores:
        st.error("Aucune donnee disponible pour le moment.")
        return

    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Classement des devises")
        ranking = build_ranking(currency_scores)
        st.dataframe(ranking, use_container_width=True)
        st.caption("Score composite = somme ponderee des scores par indicateur (echelle indicative -3 a +3 par indicateur).")

    with col2:
        st.subheader("Biais directionnel par paire (base - quote)")
        matrix = build_pair_matrix(currency_scores)
        fig = px.imshow(
            matrix,
            text_auto=True,
            color_continuous_scale="RdYlGn",
            zmin=-3,
            zmax=3,
            aspect="auto",
        )
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=420)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Lecture : positif = biais haussier probable sur BASE/QUOTE, negatif = biais baissier. Ligne = devise de base, colonne = devise de cotation.")

    st.subheader("Detail par indicateur (score -3 a +3)")
    detail = build_detail_table(currency_scores)
    st.dataframe(detail, use_container_width=True)

    st.divider()
    st.subheader("Positionnement institutionnel (COT) — dynamique, pas niveau brut")
    st.caption(
        "Positions nettes des grands speculateurs (CFTC, futures, hebdomadaire) en % de l'open interest. "
        "Ce qui compte : la tendance sur 4/12 semaines et un eventuel franchissement du zero "
        "(retournement net long <-> net short), pas seulement le niveau du moment."
    )
    cot_rows = []
    for ccy in selected_currencies:
        s = cot_summaries.get(ccy, {})
        label = classify_momentum(s)
        cot_rows.append(
            {
                "currency": ccy,
                "niveau (% OI)": s.get("level_pct_oi"),
                "variation 4 sem.": s.get("change_4w"),
                "variation 12 sem.": s.get("change_12w"),
                "dynamique": label,
                "au": s.get("as_of"),
            }
        )
    cot_df = pd.DataFrame(cot_rows)

    def _highlight_momentum(row):
        color = MOMENTUM_COLORS.get(row["dynamique"], "#ffffff")
        return [f"background-color: {color}; color: white" if col == "dynamique" else "" for col in row.index]

    st.dataframe(cot_df.style.apply(_highlight_momentum, axis=1), use_container_width=True)
    st.caption(
        "retournement = le net vient de changer de signe sur les 12 dernieres semaines (signal le plus fort). "
        "renforcement = le biais actuel (haussier ou baissier) s'accentue. "
        "affaiblissement = le biais actuel perd de la force, sans avoir encore franchi le zero."
    )

    st.divider()
    st.subheader(f"Historique du score composite ({SCORE_HISTORY_WEEKS} dernieres semaines)")
    st.caption(
        "Reconstruit a partir des memes series historiques (pas de stockage separe) : "
        "permet de voir si une devise devient forte/faible MAINTENANT, ou si c'est deja ancien."
    )
    history_df = build_multi_currency_history(raw_data, weeks_back=SCORE_HISTORY_WEEKS)
    if not history_df.empty:
        fig_hist = go.Figure()
        for ccy in history_df.columns:
            fig_hist.add_trace(go.Scatter(x=history_df.index, y=history_df[ccy], mode="lines+markers", name=ccy))
        fig_hist.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            height=420,
            xaxis_title="Date (rapport COT)",
            yaxis_title="Score composite",
            legend_title="Devise",
        )
        st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.info("Historique indisponible pour le moment.")

    st.subheader("Donnees brutes")
    for cs in currency_scores:
        with st.expander(cs.currency):
            for ind in cs.indicators:
                st.write(
                    f"**{ind.indicator}** — niveau: {ind.level} | "
                    f"variation: {ind.change} | score: {ind.score} | au {ind.as_of}"
                )

    st.divider()
    st.caption(
        "Cet outil ne fournit aucune recommandation d'achat ou de vente. "
        "Il propose une grille de lecture macro a des fins pedagogiques uniquement."
    )


if __name__ == "__main__":
    main()
