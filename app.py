"""Dashboard - Force macro des devises majeures.

Outil educatif : classe les devises majeures par force macro relative
(taux directeur, inflation, croissance, chomage, positionnement institutionnel
COT) a partir de sources de donnees publiques et gratuites (OECD, BIS, CFTC).
Ce n'est PAS un conseil en investissement ni un generateur de signaux
d'execution automatique.
"""

import os
import re
import time
from glob import glob

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from data_sources.bis_rates import fetch_policy_rate_history, summarize_policy_rate
from data_sources.common import CURRENCIES
from data_sources.cot_report import classify_momentum, fetch_cot_history, summarize_cot_momentum
from data_sources.cot_coherence import check_pair_coherence, fetch_tff_positioning
from data_sources.oecd_macro import (
    fetch_cpi_yoy,
    fetch_gdp_growth,
    fetch_unemployment_rate,
    summarize_series,
)
from data_sources.leveraged_funds_eurusd import build_report as build_leveraged_funds_report
from data_sources.technical import build_technical_summary
from data_sources.vsa import FLAG_POLARITY, FUTURES_TICKER, summarize_vsa
from scoring.engine import build_detail_table, build_pair_matrix, build_ranking, score_currency
from scoring.history import build_multi_currency_history

st.set_page_config(page_title="Force macro des devises", layout="wide")

CACHE_TTL_SECONDS = 6 * 3600  # les indicateurs macro bougent rarement plus d'une fois par jour
TECHNICAL_CACHE_TTL_SECONDS = 3600  # les prix bougent plus vite que la macro
COT_WEEKS_BACK = 26
SCORE_HISTORY_WEEKS = 12
LEVERAGED_FUNDS_CACHE_TTL_SECONDS = 6 * 3600

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


@st.cache_data(ttl=TECHNICAL_CACHE_TTL_SECONDS, show_spinner=False)
def load_technical(base: str, quote: str):
    return build_technical_summary(base, quote)


@st.cache_data(ttl=TECHNICAL_CACHE_TTL_SECONDS, show_spinner=False)
def load_vsa(currency: str):
    return summarize_vsa(currency)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_tff_positioning(currency: str):
    return fetch_tff_positioning(currency)


@st.cache_data(ttl=LEVERAGED_FUNDS_CACHE_TTL_SECONDS, show_spinner=False)
def load_leveraged_funds_report():
    return build_leveraged_funds_report()


JOURNAL_DIR = "journal"


def _parse_journal_entry(filepath: str) -> dict:
    """Parse un fichier journal (front-matter YAML simple + corps Markdown)."""
    with open(filepath, encoding="utf-8") as f:
        text = f.read()

    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    meta, body = {}, text
    if match:
        fm_text, body = match.groups()
        for line in fm_text.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()

    pair_folder = os.path.basename(os.path.dirname(filepath))
    image_name = meta.get("image")
    image_path = None
    if image_name and image_name.lower() != "null":
        candidate = os.path.join(os.path.dirname(filepath), image_name)
        if os.path.exists(candidate):
            image_path = candidate

    return {
        "pair_folder": pair_folder,
        "pair_label": meta.get("pair", pair_folder),
        "date": meta.get("date", ""),
        "type": meta.get("type", ""),
        "image_path": image_path,
        "body": body.strip(),
    }


def load_journal_entries() -> list[dict]:
    return [_parse_journal_entry(fp) for fp in sorted(glob(os.path.join(JOURNAL_DIR, "*", "*.md")))]


def render_journal():
    st.title("Journal de trading")
    st.caption(
        "Historique chronologique de toutes les analyses realisees, classees par paire. "
        "Outil educatif et informatif, ne constitue pas un conseil en investissement."
    )

    entries = load_journal_entries()
    if not entries:
        st.info("Aucune entree pour le moment.")
        return

    pair_folders = sorted({e["pair_folder"] for e in entries})
    tabs = st.tabs(pair_folders)
    for tab, pair_folder in zip(tabs, pair_folders):
        with tab:
            pair_entries = sorted(
                (e for e in entries if e["pair_folder"] == pair_folder),
                key=lambda e: e["date"],
                reverse=True,
            )
            for i, entry in enumerate(pair_entries):
                label = f"{entry['date']} — {entry['pair_label']} ({entry['type']})"
                with st.expander(label, expanded=(i == 0)):
                    if entry["image_path"]:
                        st.image(entry["image_path"], use_container_width=True)
                    st.markdown(entry["body"])


def render_dashboard(selected_currencies: list[str], start_period: str):
    st.title("Force macro des devises majeures")
    st.caption(
        "Outil educatif et informatif. Ne constitue pas un conseil en investissement. "
        "Sources : OECD (sdmx.oecd.org), BIS (stats.bis.org) et CFTC (publicreporting.cftc.gov), "
        "donnees publiques gratuites."
    )

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

    st.divider()
    st.subheader("Volume institutionnel (VSA) — futures CME")
    st.caption(
        "Le forex spot n'a pas de volume centralise fiable : on utilise le volume reel des futures "
        "CME (le prix suit tres etroitement le spot par arbitrage) pour reperer des patterns Volume "
        "Spread Analysis (climax, no demand/supply, spring, up-thrust, effort sans resultat). "
        "Lecture d'aide, pas un signal automatique fiable a 100% — la VSA reste discretionnaire."
    )
    vsa_summaries = {}
    vsa_rows = []
    for ccy in selected_currencies:
        if ccy not in FUTURES_TICKER:
            vsa_rows.append({"currency": ccy, "dernier signal": "indisponible (pas de future USD direct)", "au": None, "polarite": "neutral"})
            continue
        v = load_vsa(ccy)
        vsa_summaries[ccy] = v
        if not v.get("available"):
            vsa_rows.append({"currency": ccy, "dernier signal": "donnees indisponibles", "au": None, "polarite": "neutral"})
            continue
        flag = v.get("latest_flag") or "aucun signal recent"
        vsa_rows.append(
            {
                "currency": ccy,
                "dernier signal": flag,
                "au": v.get("latest_flag_date"),
                "polarite": FLAG_POLARITY.get(v.get("latest_flag"), "neutral"),
            }
        )
    vsa_df = pd.DataFrame(vsa_rows)
    VSA_COLORS = {"bullish": "#66bd63", "bearish": "#d73027", "neutral": "#999999"}

    def _highlight_vsa(row):
        color = VSA_COLORS.get(vsa_df.loc[row.name, "polarite"], "#ffffff")
        return [f"background-color: {color}; color: white" if col == "dernier signal" else "" for col in row.index]

    st.dataframe(vsa_df.drop(columns=["polarite"]).style.apply(_highlight_vsa, axis=1), use_container_width=True)
    st.caption(
        "spring / no supply / climax baissier = plutot favorable a la devise. "
        "up-thrust / no demand / climax haussier = plutot defavorable. "
        "USD absent : pas de future CME direct ni de proxy volume fiable trouve."
    )

    st.divider()
    st.subheader("Confirmation technique par paire")
    st.caption(
        "La macro et le COT donnent la DIRECTION. Cette section donne le TIMING : "
        "structure de marche, moyennes mobiles, RSI et niveaux cles sur la paire choisie. "
        "Prix quotidiens Yahoo Finance — le forex spot n'a pas de volume centralise fiable."
    )

    # Suggestion par defaut : la case la plus verte de la heatmap (plus gros ecart macro)
    matrix_no_diag = matrix.copy()
    for c in matrix_no_diag.columns:
        matrix_no_diag.loc[c, c] = float("-inf")
    best_base, best_quote = matrix_no_diag.stack().idxmax()

    col_a, col_b = st.columns(2)
    with col_a:
        tech_base = st.selectbox("Devise de base", options=selected_currencies, index=selected_currencies.index(best_base))
    with col_b:
        quote_options = [c for c in selected_currencies if c != tech_base]
        default_quote_idx = quote_options.index(best_quote) if best_quote in quote_options else 0
        tech_quote = st.selectbox("Devise de cotation", options=quote_options, index=default_quote_idx)

    tech = load_technical(tech_base, tech_quote)

    if not tech.get("available"):
        st.info(f"Donnees de prix indisponibles pour {tech_base}/{tech_quote} pour le moment.")
    else:
        price_df = tech["price_history"]
        fig_price = go.Figure()
        fig_price.add_trace(
            go.Candlestick(
                x=price_df["date"], open=price_df["open"], high=price_df["high"],
                low=price_df["low"], close=price_df["close"], name=tech["pair"],
            )
        )
        fig_price.add_trace(go.Scatter(x=price_df["date"], y=price_df["close"].rolling(20).mean(), name="MM20", line=dict(width=1)))
        fig_price.add_trace(go.Scatter(x=price_df["date"], y=price_df["close"].rolling(50).mean(), name="MM50", line=dict(width=1)))
        fig_price.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=450, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig_price, use_container_width=True)

        tcol1, tcol2, tcol3, tcol4 = st.columns(4)
        tcol1.metric("Structure", tech["structure"])
        tcol2.metric("Moyennes mobiles", tech["ma_alignment"])
        tcol3.metric("RSI (14)", tech["rsi"], tech["rsi_flag"] or "neutre")
        tcol4.metric("Biais technique", tech["technical_bias"])
        if tech.get("key_level_note"):
            st.caption(f"⚠️ {tech['key_level_note']} — zone de reaction possible.")

        # Confluence : macro (heatmap) + COT des 2 jambes + VSA des 2 jambes + technique
        macro_bias = matrix.loc[tech_base, tech_quote]
        base_cot = classify_momentum(cot_summaries.get(tech_base, {}))
        quote_cot = classify_momentum(cot_summaries.get(tech_quote, {}))
        base_vsa = vsa_summaries.get(tech_base, {}).get("latest_flag") or "aucun signal"
        quote_vsa = vsa_summaries.get(tech_quote, {}).get("latest_flag") or "aucun signal"
        st.markdown(
            f"**Confluence {tech['pair']}** — Macro : biais {'haussier' if macro_bias > 0 else 'baissier' if macro_bias < 0 else 'neutre'} "
            f"({macro_bias:+.2f}) · COT {tech_base} : *{base_cot}* / VSA {tech_base} : *{base_vsa}* · "
            f"COT {tech_quote} : *{quote_cot}* / VSA {tech_quote} : *{quote_vsa}* · "
            f"Technique : *{tech['technical_bias']}*"
        )

        # Controle de coherence COT inter-categories (voir data_sources/cot_coherence.py) -
        # ajoute le 2026-08-31 suite a un cas reel ou le rapport Legacy (Non-Commercial)
        # divergeait des Leveraged Funds ET Asset Managers du rapport TFF sur l'AUD.
        try:
            tff_base_df = load_tff_positioning(tech_base)
            tff_quote_df = load_tff_positioning(tech_quote)
            coherence = check_pair_coherence(
                tech_base, tech_quote, tff_base_df, tff_quote_df,
                cot_summaries.get(tech_base, {}).get("level_pct_oi"),
                cot_summaries.get(tech_quote, {}).get("level_pct_oi"),
            )
            if coherence.get("available"):
                signs_str = " · ".join(f"{cat} : *{s}*" for cat, s in coherence["signs"].items())
                if coherence["coherent"]:
                    st.success(f"**Cohérence COT inter-catégories : OK** — {signs_str}")
                else:
                    st.warning(
                        f"**Divergence COT inter-catégories** — les catégories d'acteurs ne sont "
                        f"pas d'accord entre elles, à traiter avec prudence, pas comme une "
                        f"confirmation nette : {signs_str}"
                    )
            else:
                st.caption("Contrôle de cohérence COT inter-catégories : données TFF indisponibles pour le moment.")
        except Exception as exc:
            st.caption(f"Contrôle de cohérence COT inter-catégories indisponible : {exc}")

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


DIVERGENCE_LABELS = {
    "divergence_baissiere": ("warning", "Divergence baissiere prix / COT"),
    "divergence_haussiere": ("warning", "Divergence haussiere prix / COT"),
    "convergence": ("success", "Convergence prix / COT"),
    "neutre": ("info", "Pas de lecture nette"),
    "indetermine": ("info", "Structure pas encore etablie"),
}


def render_leveraged_funds_eurusd():
    st.title("COT Leveraged Funds — EUR/USD")
    st.caption(
        "Lecture approfondie du positionnement des Leveraged Funds (hedge funds, CTA, gestion "
        "systematique) sur le contrat Euro FX (CFTC, rapport Traders in Financial Futures). "
        "Le COT definit le contexte (biais swing), pas l'entree — l'entree vient du graphique. "
        "Outil educatif, ne constitue pas un conseil en investissement."
    )

    if st.button("Forcer le rafraichissement des donnees COT/prix"):
        st.cache_data.clear()

    with st.spinner("Recuperation de l'historique CFTC (5 ans) et du prix EUR/USD..."):
        try:
            report = load_leveraged_funds_report()
        except Exception as exc:
            st.error(f"Impossible de recuperer les donnees CFTC : {exc}")
            return

    if not report.get("available"):
        st.warning(report.get("reason", "Donnees indisponibles pour le moment."))
        return

    if report["lag_warning"]:
        st.warning(report["lag_warning"])

    st.caption(
        f"Dernier rapport COT integre : **{report['as_of']}** · "
        f"{report['history_weeks_used']} semaines d'historique utilisees pour le percentile."
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Net Position (contrats)", f"{report['net_pair']:+,.0f}".replace(",", " "))
    col2.metric(
        "Weekly Change",
        f"{report['weekly_change']:+,.0f}".replace(",", " ") if report["weekly_change"] is not None else "—",
    )
    col3.metric(
        "Delta 4 semaines",
        f"{report['delta_4w']:+,.0f}".replace(",", " ") if report["delta_4w"] is not None else "—",
    )
    col4.metric("Percentile (historique utilise)", f"{report['percentile']:.0f}e")
    if report["net_pair_pct_oi"] is not None:
        st.caption(f"Net Position / Open Interest (EUR − USD) : {report['net_pair_pct_oi']:+.2f} pts — comparabilite dans le temps (section 5.2 du guide).")

    st.divider()
    st.subheader("D'ou vient le mouvement ? (decomposition Longs/Shorts sur 4 semaines)")
    st.caption(
        "Distingue une accumulation longue reelle d'un simple short covering, ou une nouvelle "
        "vente d'une reduction de longs — evite de traiter toute hausse de la position nette "
        "comme un achat agressif (section 3.3 du guide)."
    )
    fcol1, fcol2 = st.columns(2)
    for col, flow in ((fcol1, report["flow_base"]), (fcol2, report["flow_quote"])):
        with col:
            st.markdown(f"**{flow['currency']}** — {flow['classification']}")
            dl = flow["delta_longs_4w"]
            ds = flow["delta_shorts_4w"]
            st.write(
                f"Δ Longs 4S : {dl:+,.0f}".replace(",", " ") if dl is not None else "Δ Longs 4S : —"
            )
            st.write(
                f"Δ Shorts 4S : {ds:+,.0f}".replace(",", " ") if ds is not None else "Δ Shorts 4S : —"
            )

    st.divider()
    st.subheader("Grille d'interpretation operationnelle")
    interp = report["interpretation"]
    st.info(f"**{interp['situation']}**\n\nLecture probable : {interp['lecture']}\n\nReaction de methode : {interp['reaction']}")

    st.divider()
    st.subheader("Divergence / convergence prix ↔ COT")
    st.caption(
        "Compare la structure de marche du prix EUR/USD (BOS/CHoCH, meme detection que Bot "
        "Trading FTMO / Gold Swing Confluence System) a la structure du Net Position Leveraged "
        "Funds sur la meme fenetre hebdomadaire (section 3.5 du guide)."
    )
    div = report["divergence"]
    if div.get("available"):
        kind, label = DIVERGENCE_LABELS.get(div["verdict"], ("info", div["verdict"]))
        getattr(st, kind)(f"**{label}** — {div['message']}\n\nBiais prix : *{div['price_bias']}* · Biais COT : *{div['cot_bias']}*")
    else:
        st.info("Pas assez de donnees pour comparer les structures sur cette fenetre.")

    st.divider()
    st.subheader("Net Position — historique complet")
    history = report["history"]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.05)
    fig.add_trace(
        go.Scatter(x=history["date"], y=history["net_pair"], name="Net Position", line=dict(color="#1f77b4")),
        row=1, col=1,
    )
    fig.add_hline(y=0, line_dash="dot", line_color="gray", row=1, col=1)
    weekly_colors = ["#2ca02c" if v >= 0 else "#d62728" for v in history["weekly_change"].fillna(0)]
    fig.add_trace(
        go.Bar(x=history["date"], y=history["weekly_change"], name="Weekly Change", marker_color=weekly_colors),
        row=2, col=1,
    )
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=520, showlegend=False)
    fig.update_yaxes(title_text="Net Position (contrats)", row=1, col=1)
    fig.update_yaxes(title_text="Weekly Change", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Fiche hebdomadaire (8 dernieres semaines)")
    recent = report["recent_table"].rename(
        columns={"date": "Semaine", "net_pair": "Net Position", "weekly_change": "Weekly Change", "delta_4w": "Delta 4S"}
    )
    st.dataframe(recent, use_container_width=True, hide_index=True)

    st.divider()
    st.caption(
        "Limites methodologiques (section 5 du guide) : le COT porte sur les futures Euro FX "
        "declares (pas tout le marche spot/forward/OTC), la categorie Leveraged Funds est "
        "heterogene, les positions sont agregees, et les donnees ont un decalage d'au moins "
        "3 jours (positions arretees le mardi, publiees le vendredi). Un evenement macro peut "
        "invalider rapidement un biais COT — le positionnement reste secondaire par rapport a "
        "la gestion du risque et a la reaction effective du prix."
    )


def main():
    with st.sidebar:
        st.header("Parametres")
        view = st.radio("Vue", ["Dashboard macro", "COT Leveraged Funds EUR/USD", "Journal de trading"])

        selected_currencies, start_period = CURRENCIES, "2023-01"
        if view == "Dashboard macro":
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

    if view == "Dashboard macro":
        render_dashboard(selected_currencies, start_period)
    elif view == "COT Leveraged Funds EUR/USD":
        render_leveraged_funds_eurusd()
    else:
        render_journal()


if __name__ == "__main__":
    main()
