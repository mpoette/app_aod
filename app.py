"""Explorateur des vignettes cliniques « LLM et AOD ».

Lancer :  uv run streamlit run app.py
Dossier de données : variable AOD_DATA_DIR, sinon ./Data (à côté de app.py), sinon ../Data.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from aod.data import (
    META_COLUMNS, QUESTIONS, STRATEGIE, build_dataset, sort_values, summarize,
)
from aod import resultats

_HERE = Path(__file__).resolve().parent
# base des essais de l'API (même emplacement que celui de api.py)
STATE_DB = Path(os.environ.get("AOD_STATE_DIR", _HERE / "state")) / "benchmark.sqlite"


def _find_data_dir() -> Path:
    if os.environ.get("AOD_DATA_DIR"):
        return Path(os.environ["AOD_DATA_DIR"]).expanduser()
    for cand in (_HERE / "Data", _HERE.parent / "Data"):
        if any(cand.glob("*.xlsx")):
            return cand
    return _HERE / "Data"


DATA_DIR = _find_data_dir()

st.set_page_config(page_title="Vignettes AOD", page_icon="🩸", layout="wide")

# ----------------------------------------------------------------------------- palette
# Palette catégorielle validée (ordre fixe, jamais cyclé) + rampe séquentielle bleue.
CAT = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"],
}
ORDINAL = {"light": ["#86b6ef", "#2a78d6", "#1c5cab"], "dark": ["#6da7ec", "#3987e5", "#184f95"]}
# Jours J0…J-8 : rampe séquentielle bleue (plus foncé = arrêt plus précoce)
DAYS = {
    "light": ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281"],
    "dark": ["#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95"],
}
NEUTRAL = {"light": "#b5b4ae", "dark": "#6b6a64"}
SURFACE = {"light": "#ffffff", "dark": "#0e1117"}
SEQ_SCALE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def theme_mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def text_on(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lum = 0.2126 * r ** 2.2 + 0.7152 * g ** 2.2 + 0.0722 * b ** 2.2
    return "#0b0b0b" if lum > 0.33 else "#ffffff"


def colors_for(question: str, modalities: list, mode: str) -> dict:
    """Couleur stable par modalité : ordinale pour les risques, catégorielle sinon, gris pour NSP."""
    out, k = {}, 0
    ref = [m for m in ORDER.get(question, []) if not str(m).lower().startswith("je ne sais")]
    for v in modalities:
        if str(v).lower().startswith("je ne sais"):
            out[v] = NEUTRAL[mode]
        elif question in ("Risque hémorragique", "Risque thrombotique") and v in ref:
            ramp = ORDINAL[mode] if len(ref) >= 3 else [ORDINAL[mode][0], ORDINAL[mode][2]]
            out[v] = ramp[min(ref.index(v), len(ramp) - 1)]
        elif question in ("Dernière prise", "Début du relais") and str(v)[:1] == "J":
            day = int(str(v)[2:] or 0) if str(v) != "J0" else 0
            out[v] = DAYS[mode][min(day, len(DAYS[mode]) - 1)]
        elif v in ref:  # Oui/Non, Non/HBPM/HNF : couleur fixe selon la liste de modalités
            out[v] = CAT[mode][ref.index(v)]
        else:
            out[v] = CAT[mode][k] if k < len(CAT[mode]) else NEUTRAL[mode]
            k += 1
    return out


# ----------------------------------------------------------------------------- données
@st.cache_data(show_spinner="Lecture des fichiers REDCap…")
def load(data_dir: str, _mtime: float):
    return build_dataset(Path(data_dir))


def data_mtime(d: Path) -> float:
    return max((p.stat().st_mtime for p in d.rglob("*") if p.is_file()), default=0)


@st.cache_data(show_spinner="Lecture des essais de LLM…")
def load_runs(path: str, _mtime: float):
    return resultats.charger(Path(path))


if not DATA_DIR.exists():
    st.error(f"Dossier de données introuvable : `{DATA_DIR}`. Définissez `AOD_DATA_DIR`.")
    st.stop()

ds = load(str(DATA_DIR), data_mtime(DATA_DIR))
cases, answers_all, ORDER = ds.cases, ds.answers, ds.choices_order
MODE = theme_mode()

# ----------------------------------------------------------------------------- barre latérale
with st.sidebar:
    st.header("Filtres")
    q_text = st.text_input("Recherche dans la vignette", placeholder="ex. Pradaxa, carotide…")
    only_answered = st.toggle("Uniquement les cas avec réponses", value=True)

    filters = {}
    for col in META_COLUMNS:
        opts = sorted(cases[col].dropna().unique().tolist())
        filters[col] = st.multiselect(col, opts, default=[], placeholder="Toutes")

    st.divider()
    st.header("Répondants")
    roles = sorted(answers_all["role"].dropna().unique().tolist())
    sel_roles = st.multiselect("Rôles inclus", roles, default=roles)
    min_rep = st.number_input("Nombre minimal de répondants par cas", 0, 50, 0,
                              help="Avec 1 seul répondant, l'accord vaut toujours 100 %.")

    st.divider()
    st.header("Tri")
    SORT_KEYS = ["N° du cas", *META_COLUMNS, "Accord sur la stratégie", "Accord moyen",
                 "Confiance moyenne", "Nombre de répondants"]
    sort_by = st.multiselect("Trier par (ordre de priorité)", SORT_KEYS, default=["N° du cas"])
    descending = st.toggle("Ordre décroissant", value=False)
    st.caption(f"Données : `{DATA_DIR.name}/` · {cases['Spécialité'].nunique()} spécialités")

# ----------------------------------------------------------------------------- calculs filtrés
answers = answers_all[answers_all["role"].isin(sel_roles)]
summ = summarize(answers)
core = summ[summ["question"] != STRATEGIE]

per_case = (
    cases.set_index("case_id")
    .join(core.groupby("case_id")["accord"].mean().rename("Accord moyen"))
    .join(summ[summ["question"] == STRATEGIE].set_index("case_id")[["majoritaire", "accord"]]
          .rename(columns={"majoritaire": "Stratégie majoritaire", "accord": "Accord sur la stratégie"}))
    .join(answers.drop_duplicates(["case_id", "respondant"]).groupby("case_id")
          .agg(**{"Nombre de répondants": ("respondant", "nunique"), "Confiance moyenne": ("confiance", "mean")}))
    .reset_index()
)
per_case["Nombre de répondants"] = per_case["Nombre de répondants"].fillna(0).astype(int)
per_case["N° du cas"] = per_case["case_id"]

view = per_case.copy()
if only_answered:
    view = view[view["Nombre de répondants"] > 0]
if min_rep:
    view = view[view["Nombre de répondants"] >= min_rep]
for col, vals in filters.items():
    if vals:
        view = view[view[col].isin(vals)]
if q_text:
    view = view[view["texte"].str.contains(q_text, case=False, na=False)]
if sort_by:
    view = view.sort_values(sort_by, ascending=not descending, na_position="last", kind="stable")

# ----------------------------------------------------------------------------- en-tête
st.title("Gestion préopératoire des anticoagulants — vignettes cliniques")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Cas affichés", f"{len(view)} / {len(cases)}")
k2.metric("Répondants", answers[answers["case_id"].isin(view["case_id"])]["respondant"].nunique())
acc = view["Accord moyen"].mean()
k3.metric("Accord moyen", "—" if pd.isna(acc) else f"{acc:.0%}",
          help="Part de la réponse majoritaire, moyennée sur les questions puis sur les cas.")
conf = view["Confiance moyenne"].mean()
k4.metric("Confiance moyenne", "—" if pd.isna(conf) else f"{conf:.1f} / 10")

if view.empty:
    st.info("Aucun cas ne correspond aux filtres.")
    st.stop()


# ----------------------------------------------------------------------------- graphiques
def dispersion_fig(cid: int) -> go.Figure | None:
    s = summ[summ["case_id"] == cid]
    if s.empty:
        return None
    labels = [STRATEGIE] + [lab for _, lab in QUESTIONS]
    s = s.set_index("question").reindex([q for q in labels if q in set(s["question"])])
    fig = go.Figure()
    y_order = list(s.index)[::-1]
    for q, row in s.iterrows():
        dist, n = row["distribution"], row["n"]
        mods = sort_values(q, dist.keys(), ORDER)
        cmap = colors_for(q, mods, MODE)
        for v in mods:
            c = dist[v]
            fig.add_bar(
                y=[q], x=[c / n], orientation="h", name=str(v), showlegend=False,
                marker=dict(color=cmap[v], line=dict(color=SURFACE[MODE], width=2)),
                text=f"{v} · {c}", textposition="inside", insidetextanchor="middle",
                textfont=dict(color=text_on(cmap[v]), size=12),
                hovertemplate=f"<b>{q}</b><br>{v}<br>{c} / {n} répondant(s) — {c / n:.0%}<extra></extra>",
            )
    fig.update_layout(
        barmode="stack", height=46 * len(s) + 50, margin=dict(l=0, r=10, t=10, b=30),
        xaxis=dict(range=[0, 1], tickformat=".0%", showgrid=True, gridcolor="rgba(128,128,128,0.15)",
                   zeroline=False, fixedrange=True),
        yaxis=dict(categoryorder="array", categoryarray=y_order, fixedrange=True),
        bargap=0.35, uniformtext=dict(minsize=9, mode="hide"),
        hoverlabel=dict(namelength=-1),
    )
    return fig


def answers_table(cid: int) -> pd.DataFrame:
    a = answers[answers["case_id"] == cid]
    if a.empty:
        return pd.DataFrame()
    t = a.pivot_table(index=["respondant", "role"], columns="question", values="valeur", aggfunc="first")
    cols = [STRATEGIE] + [lab for _, lab in QUESTIONS]
    t = t[[c for c in cols if c in t.columns]]
    conf_ = a.drop_duplicates("respondant").set_index(["respondant", "role"])["confiance"]
    t["Confiance"] = conf_
    return t.reset_index().rename(columns={"respondant": "Répondant", "role": "Rôle"})


def summary_table(cid: int) -> pd.DataFrame:
    s = summ[summ["case_id"] == cid].set_index("question")
    rows = []
    for q in [STRATEGIE] + [lab for _, lab in QUESTIONS]:
        if q in s.index:
            r = s.loc[q]
            rows.append({"Question": q, "Réponse majoritaire": r["majoritaire"], "Accord": r["accord"],
                         "n": int(r["n"])})
    return pd.DataFrame(rows)


def pct(x) -> str:
    return "—" if pd.isna(x) else f"{x:.0%}"


BADGE_COLORS = {"Spécialité": "violet", "ATC": "blue", "Délais": "orange", "Indication": "green",
                "Fonction rénale": "gray", "ALR": "gray", "Sexe": "gray", "Origine": "red"}

# ----------------------------------------------------------------------------- onglets
tab_v, tab_o, tab_l, tab_m = st.tabs(["🗂️ Vignettes", "📊 Vue d'ensemble", "🤖 Résultats LLM", "ℹ️ Méthode"])

with tab_v:
    t1, t2, t3 = st.columns([2, 2, 3], vertical_alignment="bottom")
    expand_all = t1.toggle("Tout déplier", value=False)
    per_page = t2.selectbox("Cas par page", [10, 20, 50], index=1)
    n_pages = max(1, -(-len(view) // per_page))
    page = t3.number_input(f"Page (sur {n_pages})", 1, n_pages, 1) if n_pages > 1 else 1
    for _, c in view.iloc[(page - 1) * per_page: page * per_page].iterrows():
        cid = int(c["case_id"])
        n_rep = int(c["Nombre de répondants"])
        if not n_rep:
            suffixe = " — pas de réponse"
        elif pd.isna(c["Stratégie majoritaire"]):
            suffixe = " — stratégie non tranchée"  # possible pour la RCP, qui ne précise pas toujours tout
        else:
            suffixe = f" — stratégie : {c['Stratégie majoritaire']} ({pct(c['Accord sur la stratégie'])})"
        title = (f"**Cas {cid}**{' (RCP)' if c['Origine'] == 'RCP' else ''} · {c['Spécialité']} · "
                 f"{c['ATC']} · {c['Délais']} · {c['Indication']}" + suffixe)
        with st.expander(title, expanded=expand_all):
            st.markdown(" ".join(f":{BADGE_COLORS[m]}-badge[{m} : {c[m]}]" for m in META_COLUMNS)
                        + (" :red-badge[Urgence immédiate]" if c["Urgence immédiate"] else ""))
            left, right = st.columns([5, 6], gap="large")
            with left:
                st.markdown("##### Vignette")
                if c["html"]:
                    st.html(f"<div style='line-height:1.55'>{c['html']}</div>")
                else:
                    st.caption("Texte de la vignette non disponible (pas de fichier REDCap pour cette spécialité).")
                if c["Origine"] == "RCP":
                    st.caption("Vignette de la RCP thrombose : la référence est la décision de la RCP, "
                               "pour les seules questions qu'elle a tranchées explicitement.")
                if n_rep:
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Répondants", n_rep)
                    m2.metric("Accord moyen", pct(c["Accord moyen"]))
                    m3.metric("Confiance", "—" if pd.isna(c["Confiance moyenne"])
                              else f"{c['Confiance moyenne']:.1f}")
            with right:
                if n_rep:
                    st.markdown("##### Réponse majoritaire")
                    st.dataframe(
                        summary_table(cid), hide_index=True, width="stretch",
                        column_config={"Accord": st.column_config.ProgressColumn(
                            "Accord", format="percent", min_value=0, max_value=1,
                            help="Part des répondants ayant donné la réponse majoritaire")},
                    )
            if n_rep:
                st.markdown("##### Dispersion des réponses")
                fig = dispersion_fig(cid)
                if fig is not None:
                    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False},
                                    key=f"disp_{cid}")
                with st.popover("Réponses individuelles", width="content"):
                    st.dataframe(answers_table(cid), hide_index=True, width="stretch")

with tab_o:
    st.subheader("Tableau récapitulatif")
    wide_majo = summ.pivot(index="case_id", columns="question", values="majoritaire")
    qcols = [lab for _, lab in QUESTIONS if lab in wide_majo.columns]
    table = (view[["case_id", *META_COLUMNS, "Nombre de répondants", "Stratégie majoritaire",
                   "Accord sur la stratégie", "Accord moyen", "Confiance moyenne"]]
             .merge(wide_majo[qcols], left_on="case_id", right_index=True, how="left")
             .rename(columns={"case_id": "Cas"}))
    st.dataframe(
        table, hide_index=True, width="stretch", height=min(38 * len(table) + 40, 640),
        column_config={
            "Accord sur la stratégie": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
            "Accord moyen": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
            "Confiance moyenne": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    st.download_button("Télécharger (CSV)", table.to_csv(index=False, sep=";").encode("utf-8-sig"),
                       "synthese_vignettes.csv", "text/csv")

    answered = view[view["Nombre de répondants"] > 0]
    if not answered.empty:
        st.subheader("Carte de l'accord par cas et par question")
        st.caption("Part de la réponse majoritaire (100 % = unanimité). Case vide = question non posée.")
        qs = [STRATEGIE] + qcols
        acc_w = summ.pivot(index="case_id", columns="question", values="accord").reindex(
            index=answered["case_id"], columns=qs)
        maj_w = wide_majo.reindex(index=answered["case_id"], columns=qs)
        n_w = summ.pivot(index="case_id", columns="question", values="n").reindex(
            index=answered["case_id"], columns=qs)
        ylab = [f"Cas {i}" for i in acc_w.index]
        hm = go.Figure(go.Heatmap(
            z=acc_w.values, x=qs, y=ylab, zmin=0, zmax=1, xgap=2, ygap=2,
            colorscale=[[i / (len(SEQ_SCALE) - 1), col] for i, col in enumerate(SEQ_SCALE)],
            customdata=np.dstack([maj_w.fillna("—").values.astype(object),
                                  n_w.fillna(0).astype(int).values.astype(object)]),
            hovertemplate="<b>%{y} — %{x}</b><br>Majoritaire : %{customdata[0]}<br>"
                          "Accord : %{z:.0%} (n = %{customdata[1]})<extra></extra>",
            colorbar=dict(title="Accord", tickformat=".0%", thickness=10),
        ))
        hm.update_layout(height=max(320, 24 * len(acc_w) + 120), margin=dict(l=0, r=0, t=10, b=0),
                         yaxis=dict(autorange="reversed"), xaxis=dict(side="top"))
        st.plotly_chart(hm, width="stretch", config={"displayModeBar": False})

        st.subheader("Accord selon une métadonnée")
        gcol = st.selectbox("Regrouper par", META_COLUMNS, index=2)
        metric = st.radio("Mesure", ["Accord moyen", "Accord sur la stratégie", "Confiance moyenne"],
                          horizontal=True)
        g = answered.groupby(gcol).agg(val=(metric, "mean"), n=("case_id", "count")).reset_index()
        is_pct = metric != "Confiance moyenne"
        bar = go.Figure(go.Bar(
            x=g[gcol], y=g["val"], marker=dict(color=CAT[MODE][0], cornerradius=4),
            text=[(f"{v:.0%}" if is_pct else f"{v:.1f}") for v in g["val"]], textposition="outside",
            customdata=g["n"],
            hovertemplate="<b>%{x}</b><br>" + metric + " : %{text}<br>%{customdata} cas<extra></extra>",
        ))
        bar.update_layout(height=320, margin=dict(l=0, r=0, t=20, b=0), bargap=0.5,
                          yaxis=dict(range=[0, 1.1] if is_pct else [0, 11], tickformat=".0%" if is_pct else None,
                                     gridcolor="rgba(128,128,128,0.15)", title=None),
                          xaxis=dict(title=None))
        st.plotly_chart(bar, width="stretch", config={"displayModeBar": False})

def llm_heatmap(agg: pd.DataFrame, colonne: str, x_order: list, case_order: list) -> go.Figure:
    """Carte cas × (questions ou modèles) : part des réponses justes sur les essais retenus."""
    w = {v: agg.pivot(index="case_id", columns=colonne, values=v).reindex(index=case_order, columns=x_order)
         for v in ("taux", "justes", "notees", "reponses", "reference")}
    custom = np.dstack([w["justes"].fillna(0).astype(int).values.astype(object),
                        w["notees"].fillna(0).astype(int).values.astype(object),
                        w["reponses"].fillna("—").values.astype(object),
                        w["reference"].fillna("—").values.astype(object)])
    fig = go.Figure(go.Heatmap(
        z=w["taux"].values.astype(float), x=x_order, y=[f"Cas {i}" for i in case_order],
        zmin=0, zmax=1, xgap=2, ygap=2,
        colorscale=[[i / (len(SEQ_SCALE) - 1), col] for i, col in enumerate(SEQ_SCALE)],
        customdata=custom,
        hovertemplate="<b>%{y} — %{x}</b><br>Juste : %{customdata[0]} / %{customdata[1]} (%{z:.0%})"
                      "<br>Réponse(s) du LLM : %{customdata[2]}<br>Référence : %{customdata[3]}<extra></extra>",
        colorbar=dict(title="Juste", tickformat=".0%", thickness=10),
    ))
    fig.update_layout(height=max(320, 24 * len(case_order) + 120), margin=dict(l=0, r=0, t=10, b=0),
                      yaxis=dict(autorange="reversed"), xaxis=dict(side="top"))
    return fig


# Couleur d'une marque : un emplacement fixe de la palette catégorielle (la couleur suit la marque,
# jamais son rang), et une pastille de couleur proche pour le tableau des essais.
MARQUE_STYLE = {"Google": (0, "🔵"), "Anthropic": (1, "🟠"), "OpenAI": (2, "🟢"), "Mistral": (3, "🟡"),
                "Meta": (4, "🩷"), "Alibaba": (5, "🟩"), "DeepSeek": (6, "🟣"), "xAI": (7, "🔴")}


def couleur_marque(m: str) -> str:
    return CAT[MODE][MARQUE_STYLE[m][0]] if m in MARQUE_STYLE else NEUTRAL[MODE]


with tab_l:
    essais, cellules = load_runs(str(STATE_DB), STATE_DB.stat().st_mtime if STATE_DB.exists() else 0)
    if essais.empty:
        st.info("Aucun essai de LLM pour l'instant. Les essais sont créés par l'API de benchmark "
                "(`POST /runs`) et apparaissent ici dès leurs premières réponses.")
    else:
        essais = essais.assign(jour=essais["cree_le"].dt.tz_convert("Europe/Paris").dt.tz_localize(None))
        st.subheader("Essais")
        f1, f2, f3, f4, f5 = st.columns([2, 3, 2, 1.3, 2])
        f_marque = f1.multiselect("Marque", sorted(essais["marque"].unique()), placeholder="Toutes")
        e = essais[essais["marque"].isin(f_marque)] if f_marque else essais
        f_modele = f2.multiselect("Modèle", sorted(e["modele"].unique()), placeholder="Tous")
        f_auteur = f3.multiselect("Utilisateur", sorted(essais["auteur"].unique()), placeholder="Tous")
        f_pre = f4.selectbox("Préprompt", ["Tous", "Avec", "Sans"])
        d0, d1 = essais["jour"].min().date(), essais["jour"].max().date()
        f_dates = f5.date_input("Période", (d0, d1), min_value=d0, max_value=d1, format="DD/MM/YYYY")
        if f_modele:
            e = e[e["modele"].isin(f_modele)]
        if f_auteur:
            e = e[e["auteur"].isin(f_auteur)]
        if f_pre != "Tous":
            e = e[e["preprompt"] == (f_pre == "Avec")]
        if len(f_dates) == 2:
            e = e[e["jour"].dt.date.between(*f_dates)]
        candidats = e.sort_values("cree_le", ascending=False)

        exclus = st.session_state.setdefault("llm_exclus", set())  # survit au changement de filtres
        grille = pd.DataFrame({
            "Inclure": ~candidats["id"].isin(exclus),
            "Marque": [f"{MARQUE_STYLE.get(m, (0, '⚪'))[1]} {m}" for m in candidats["marque"]],
            "Modèle": candidats["modele"], "Date": candidats["jour"], "Utilisateur": candidats["auteur"],
            "Stratégie juste": candidats["taux"], "Questions justes": candidats["taux_questions"],
            "Cas notés": candidats["repondus"], "Refus": candidats["refus"],
            "Préprompt": candidats["preprompt"], "Référence": candidats["reference"],
            "Min. répondants": candidats["min_repondants"], "Paramètres": candidats["parametres"],
            "Essai": candidats["id"],
        })
        cle = hashlib.md5("|".join(candidats["id"]).encode()).hexdigest()[:10]
        edit = st.data_editor(
            grille, hide_index=True, width="stretch", key=f"llm_essais_{cle}",
            height=min(36 * len(grille) + 40, 400),
            disabled=[c for c in grille.columns if c != "Inclure"],
            column_config={
                "Inclure": st.column_config.CheckboxColumn(width="small"),
                "Date": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
                "Stratégie juste": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                "Questions justes": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
            },
        )
        st.caption(f"{len(candidats)} essai(s) sur {len(essais)} après filtres. Décochez un essai pour l'exclure ; "
                   "les essais d'un même modèle sont additionnés. Scores du tableau : sur tous les cas de l'essai.")
        retenus = edit.loc[edit["Inclure"], "Essai"].tolist()
        exclus.difference_update(retenus)
        exclus.update(edit.loc[~edit["Inclure"], "Essai"])

        sel = candidats[candidats["id"].isin(retenus)]
        if sel[["reference", "min_repondants", "preprompt"]].drop_duplicates().shape[0] > 1:
            st.warning("Les essais retenus n'ont pas tous les mêmes réglages (référence, nombre minimal de "
                       "répondants ou préprompt) : les résultats mélangent des conditions différentes.")

        cel = cellules[cellules["case_id"].isin(set(view["case_id"]))]  # filtres de cas de la barre latérale
        agg_m = resultats.agreger(cel, essais, retenus, "modele")
        if not retenus:
            st.info("Aucun essai retenu.")
        elif agg_m.empty:
            st.info("Aucune réponse notée parmi les cas affichés (voir les filtres de la barre latérale).")
        else:
            j_s, n_s = resultats.taux_global(cel, retenus)
            jq = nq = 0
            for _, lab in QUESTIONS:
                j, n = resultats.taux_global(cel, retenus, lab)
                jq, nq = jq + j, nq + n
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Essais retenus", len(retenus))
            m2.metric("Modèles", sel["modele"].nunique())
            m3.metric("Stratégie juste", pct(j_s / n_s), help=f"{j_s} / {n_s} réponses, cas affichés")
            m4.metric("Questions justes", pct(jq / nq if nq else np.nan),
                      help=f"{jq} / {nq} réponses aux questions évaluées, cas affichés")

            g = (agg_m.groupby("modele")[["justes", "notees"]].sum()
                 .assign(taux=lambda d: d["justes"] / d["notees"])
                 .join(sel.groupby("modele").agg(marque=("marque", "first"), essais=("id", "size")))
                 .sort_values("taux", ascending=False))
            st.subheader("Stratégie juste par modèle")
            bar = go.Figure()
            for m in sorted(g["marque"].unique()):  # une série par marque : couleur et légende
                gm = g[g["marque"] == m]
                bar.add_bar(
                    y=gm.index, x=gm["taux"], orientation="h", name=m,
                    marker=dict(color=couleur_marque(m), cornerradius=4),
                    text=[f"{v:.0%}" for v in gm["taux"]], textposition="outside",
                    customdata=np.stack([gm["justes"], gm["notees"], gm["essais"]], axis=-1),
                    hovertemplate="<b>%{y}</b><br>%{customdata[0]} / %{customdata[1]} stratégies justes"
                                  "<br>%{customdata[2]} essai(s)<extra></extra>",
                )
            bar.update_layout(
                height=40 * len(g) + 90, margin=dict(l=0, r=0, t=30, b=0), bargap=0.35,
                xaxis=dict(range=[0, 1.12], tickformat=".0%", gridcolor="rgba(128,128,128,0.15)"),
                yaxis=dict(categoryorder="array", categoryarray=list(g.index)[::-1]),
                legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, title=None),
            )
            st.plotly_chart(bar, width="stretch", config={"displayModeBar": False})

            if len(g) > 1:
                st.subheader("Carte de la stratégie juste par cas et par modèle")
                st.caption("Part des réponses retenues où le modèle a donné une stratégie majoritaire.")
                ordre = [c for c in view["case_id"] if c in set(agg_m["case_id"])]
                st.plotly_chart(llm_heatmap(agg_m, "modele", list(g.index), ordre), width="stretch",
                                config={"displayModeBar": False})

            st.subheader("Détail par question")
            modele = st.selectbox("Modèle", list(g.index)) if len(g) > 1 else g.index[0]
            ids = sel.loc[sel["modele"] == modele, "id"].tolist()
            agg_q = resultats.agreger(cel, essais, ids, "question")
            st.caption(f"{modele} · {len(ids)} essai(s). Part des réponses qui font partie des réponses "
                       "majoritaires (avec un seul essai, 0 ou 100 %). Case vide = question non évaluée.")
            qs = [q for q in [STRATEGIE] + [lab for _, lab in QUESTIONS] if q in set(agg_q["question"])]
            ordre = [c for c in view["case_id"] if c in set(agg_q["case_id"])]
            st.plotly_chart(llm_heatmap(agg_q, "question", qs, ordre), width="stretch",
                            config={"displayModeBar": False})
            st.download_button("Télécharger le détail (CSV)",
                               agg_q.to_csv(index=False, sep=";").encode("utf-8-sig"),
                               f"resultats_{modele}.csv", "text/csv")

with tab_m:
    st.markdown(f"""
**Sources** (dossier `{DATA_DIR}`)
- `Métadonnées.xlsx` : caractéristiques des {len(cases)} cas (Sexe, ATC, Délais, Indication, Fonction rénale, ALR, spécialité).
- Un sous-dossier par spécialité contenant l'export REDCap : dictionnaire `*.REDCap.xml` (texte des vignettes,
  questions et modalités) et réponses `*_DATA_*.csv`. Le *n*-ième cas d'un projet REDCap est associé au
  *n*-ième « Cas clinique » de la même spécialité dans le fichier de métadonnées.

**Indicateurs**
- *Réponse majoritaire* : modalité la plus fréquente ; en cas d'égalité, toutes les modalités ex æquo sont affichées.
- *Accord* : part des répondants ayant donné la réponse majoritaire (100 % = unanimité, 1/k = dispersion maximale).
- *Accord moyen* : moyenne de l'accord sur les questions posées pour le cas (hors stratégie globale).
- *Stratégie globale* : combinaison interruption + jour de dernière prise + relais (+ jour de début), ou réversion en urgence immédiate.
- Les questions conditionnelles (dernière prise, relais…) ne sont comptées que pour les répondants concernés (*n* affiché).
- Seuls les cas marqués « Complete » dans REDCap sont comptés.

**Vignettes de la RCP thrombose** (`Data/RCP/*.xlsx`, métadonnée *Origine* = « RCP », cas 101 et suivants)
- Le cas *n* du fichier de la RCP devient le cas 100 + *n*. La RCP y figure comme un répondant unique
  (rôle « RCP », sans niveau de confiance) : l'accord y vaut donc toujours 100 %.
- Seules les réponses écrites explicitement par la RCP sont retenues ; une question qu'elle n'a pas tranchée
  reste sans réponse, et la stratégie globale n'est retenue que si tous ses éléments sont précisés.
- Sexe, ATC, Délais, Fonction rénale et ALR sont lus dans le fichier s'il en a la colonne, sinon déduits
  du texte selon les conventions de `Métadonnées.xlsx` ; l'indication n'est jamais déduite.

**Résultats LLM** (essais soumis par l'API de benchmark)
- Chaque réponse est corrigée à sa soumission, par rapport à la référence figée à la création de son essai :
  le choix des rôles de la barre latérale ne la modifie pas ; les autres filtres choisissent les cas affichés.
- *Juste* : la réponse du LLM fait partie des réponses majoritaires. Un refus compte comme faux ; une erreur
  d'appel est exclue ; seuls les cas évaluables de l'essai sont comptés.
- Plusieurs essais d'un même modèle sont additionnés : une case vaut « réponses justes / réponses notées ».

**Mettre à jour** : remplacez/ajoutez les exports dans `Data/<Spécialité>/` (le plus récent est utilisé) puis rechargez la page.
""")
