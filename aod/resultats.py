"""Résultats des essais de LLM (base de l'API) mis en forme pour l'explorateur.

Les grilles enregistrées par l'API sont reprises telles quelles : chaque réponse a été corrigée
au moment de sa soumission, par rapport à la référence figée à la création de son essai.
Mêmes règles que le bilan de l'API (aod.scoring.bilan) :
- une erreur d'appel n'est pas une réponse du modèle : elle est exclue ;
- un refus compte comme une réponse fausse ;
- seuls les cas évaluables de l'essai sont comptés ;
- une question « non évaluable » ou « non applicable » ne compte ni juste ni fausse.

Plusieurs essais d'un même modèle sont agrégés en additionnant leurs réponses : une case de la
carte vaut « réponses justes / réponses notées » sur l'ensemble des essais retenus.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

from aod.data import STRATEGIE
from aod.scoring import CHAMPS, JOURS, _jour_label
from aod.store import Store

# clé de la grille -> libellé de colonne (même libellé que les questions des répondants humains)
QUESTIONS_LLM = [("strategie", STRATEGIE)] + [(cle, label) for cle, (label, _, _) in CHAMPS.items()]
NOTEES = ("juste", "faux")
COLONNES_CELLULES = ["run_id", "case_id", "question", "statut_reponse", "statut", "reponse", "reference"]

# Marque d'un modèle, d'après le début de son nom (insensible à la casse) ; sinon « Autre »
MARQUES = {
    "Anthropic": ("claude",),
    "Google": ("gemini", "gemma"),
    "OpenAI": ("gpt", "o1", "o3", "o4", "chatgpt"),
    "Mistral": ("mistral", "magistral", "codestral", "ministral", "pixtral"),
    "DeepSeek": ("deepseek",),
    "Meta": ("llama",),
    "xAI": ("grok",),
    "Alibaba": ("qwen", "qwq"),
}
AUTRE = "Autre"


def marque(modele: str) -> str:
    nom = str(modele).strip().lower().split("/")[-1]  # « openai/gpt-5 » -> « gpt-5 »
    for m, prefixes in MARQUES.items():
        if nom.startswith(prefixes):
            return m
    return AUTRE


def _libelle(cle: str, code) -> str:
    """Code du format LLM (« eleve », 5) -> libellé lisible (« Élevé », « J-5 »)."""
    if code is None:
        return "—"
    if cle == "strategie":
        return str(code)
    if cle in JOURS:
        return _jour_label(code) if isinstance(code, int) else str(code)
    return (CHAMPS[cle][2] or {}).get(code, str(code))


def charger(chemin: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(essais, cellules) lus dans la base de l'API ; deux tables vides si elle n'existe pas.

    `cellules` : une ligne par essai × cas × question, pour les seules réponses comptées.
    """
    if not chemin.exists():
        return pd.DataFrame(), pd.DataFrame(columns=COLONNES_CELLULES)
    store = Store(chemin)
    runs = store.runs()
    lignes = []
    for r in store.reponses():
        g = r["grille"]
        if r["statut"] == "erreur" or not g["evaluable"]:
            continue
        for cle, label in QUESTIONS_LLM:
            q = g["strategie"] if cle == "strategie" else g["questions"][cle]
            lignes.append({
                "run_id": r["run_id"], "case_id": int(r["cas"]), "question": label,
                "statut_reponse": r["statut"], "statut": q["statut"],
                "reponse": "(refus)" if r["statut"] == "refus" else _libelle(cle, q["reponse"]),
                "reference": " / ".join(_libelle(cle, m) for m in q["reference"]) or "—",
            })
    cellules = pd.DataFrame(lignes, columns=COLONNES_CELLULES)

    def compter(c: pd.DataFrame, notees: str, justes: str) -> pd.DataFrame:
        c = c[c["statut"].isin(NOTEES)]
        return c.groupby("run_id")["statut"].agg(**{notees: "size", justes: lambda s: int((s == "juste").sum())})

    strat = cellules[cellules["question"] == STRATEGIE]
    par_run = pd.concat([
        compter(strat, "repondus", "justes"),
        compter(cellules[cellules["question"] != STRATEGIE], "questions_notees", "questions_justes"),
        (strat["statut_reponse"] == "refus").groupby(strat["run_id"]).sum().rename("refus"),
    ], axis=1)
    essais = pd.DataFrame([{
        "id": r["id"], "cree_le": pd.Timestamp(r["cree_le"]), "marque": marque(r["modele"]),
        "modele": r["modele"], "auteur": r["auteur"] or "inconnu", "preprompt": r["preprompt"],
        "reference": "tous les rôles" if not r["roles"] else ", ".join(r["roles"]),
        "min_repondants": r["min_repondants"], "version_donnees": r["version_donnees"],
        "parametres": ", ".join(f"{k}={v}" for k, v in r["parametres"].items()),
    } for r in runs])
    if essais.empty:
        return essais, cellules
    essais = essais.join(par_run, on="id")
    for col in ("repondus", "justes", "questions_notees", "questions_justes", "refus"):
        essais[col] = essais[col].fillna(0).astype(int)
    essais["taux"] = essais["justes"] / essais["repondus"].where(essais["repondus"] > 0)
    essais["taux_questions"] = essais["questions_justes"] / essais["questions_notees"].where(
        essais["questions_notees"] > 0)
    return essais, cellules


def agreger(cellules: pd.DataFrame, essais: pd.DataFrame, run_ids: list[str], colonne: str) -> pd.DataFrame:
    """Taux de réponses justes par cas × `colonne` (« question » ou « modele ») sur les essais retenus.

    Colonnes : case_id, <colonne>, justes, notees, taux, reponses (« Élevé ×2, Faible ×1 »), reference.
    """
    c = cellules[cellules["run_id"].isin(run_ids) & cellules["statut"].isin(NOTEES)]
    if colonne == "modele":
        c = c[c["question"] == STRATEGIE].merge(essais[["id", "modele"]], left_on="run_id", right_on="id")
    sortie = ["case_id", colonne, "justes", "notees", "taux", "reponses", "reference"]
    if c.empty:
        return pd.DataFrame(columns=sortie)

    def resume(g: pd.DataFrame) -> pd.Series:
        justes = int((g["statut"] == "juste").sum())
        return pd.Series({
            "justes": justes, "notees": len(g), "taux": justes / len(g),
            # par fréquence, puis par ordre alphabétique ; « ; » car une stratégie contient déjà une virgule
            "reponses": " ; ".join(f"{v} ×{n}" if n > 1 else v for v, n in
                                   sorted(Counter(g["reponse"]).items(), key=lambda vn: (-vn[1], vn[0]))),
            "reference": " ou ".join(dict.fromkeys(g["reference"])),  # varie si les références figées diffèrent
        })

    return c.groupby(["case_id", colonne]).apply(resume, include_groups=False).reset_index()[sortie]


def taux_global(cellules: pd.DataFrame, run_ids: list[str], question: str = STRATEGIE) -> tuple[int, int]:
    """(justes, notées) pour une question sur l'ensemble des essais retenus."""
    c = cellules[cellules["run_id"].isin(run_ids) & (cellules["question"] == question)
                 & cellules["statut"].isin(NOTEES)]
    return int((c["statut"] == "juste").sum()), len(c)
