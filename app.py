"""Dashboard - Force macro des devises majeures.

Outil educatif : classe les devises majeures par force macro relative
(taux directeur, inflation, croissance, chomage) a partir de sources de
donnees publiques et gratuites (OECD, BIS). Ce n'est PAS un conseil en
investissement ni un generateur de signaux d'execution automatique.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from data_sources.bis_rates import fetch_policy_rate_history, summarize_policy_rate
from data_sources.common import CURRENCIES
from data_sources.oecd_macro import (
    fetch_cpi_yoy,
    fetch_gdp_growth,
    fetch_unemployment_rate,
    summarize_series,
)
from scoring.engine import build_detail_table, build_pair_matrix, build_ranking, score_currency

st.set_page_config(page_title="Force macro des devises", layout="wide")

CACHE_TTL_SECONDS = 6 * 3600  # les indicateurs macro bougent rarement plus d'une fois par jour


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_currency_data(currency: str, start_period: str):
    policy_history = fetch_policy_rate_history(currency)
    cpi = fetch_cpi_yoy(currency, start_period=start_period)
    gdp = fetch_gdp_growth(currency, start_period=start_period)
    unemployment = fetch_unemployment_rate(currency, start_period=start_period)

    return {
        "policy_rate": summarize_policy_rate(policy_history),
        "cpi_yoy": summarize_series(cpi),
        "gdp_growth_yoy": summarize_series(gdp),
        "unemployment_rate": summarize_series(unemployment),
    }


def main():
    st.title("Force macro des devises majeures")
    st.caption(
        "Outil educatif et informatif. Ne constitue pas un conseil en investissement. "
        "Sources : OECD (sdmx.oecd.org) et BIS (stats.bis.org), donnees publiques gratuites."
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
    errors = []
    with st.spinner("Recuperation des donnees macro..."):
        for ccy in selected_currencies:
            try:
                data = load_currency_data(ccy, start_period)
                currency_scores.append(
                    score_currency(
                        ccy,
                        policy_rate_summary=data["policy_rate"],
                        cpi_summary=data["cpi_yoy"],
                        gdp_summary=data["gdp_growth_yoy"],
                        unemployment_summary=data["unemployment_rate"],
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
