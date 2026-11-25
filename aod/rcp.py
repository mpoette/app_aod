"""Vignettes issues de la RCP thrombose (Data/RCP/*.xlsx).

Le fichier contient une feuille « Vignettes » : une ligne par cas, avec le texte de la vignette
et les réponses données explicitement par la RCP, codées comme dans REDCap (risque_hem,
interruption, dern_prise, relais, relais_debut, dosage…). Une cellule vide signifie que la RCP
ne s'est pas prononcée : aucune valeur n'est déduite.

Les cas sont fondus dans le jeu de données de l'étude :
- numérotés à partir de 101 (cas 1 de la RCP → cas 101), pour ne jamais entrer en collision
  avec les cas de l'étude, même si celle-ci s'agrandit ;
- marqués « RCP » dans la métadonnée « Origine » ;
- la RCP y figure comme un répondant, de rôle « rcp », sans niveau de confiance ;
- les métadonnées (Sexe, ATC, Délais, Fonction rénale, ALR) sont lues dans le fichier si une
  colonne du même nom existe, sinon déduites du texte selon les conventions de Métadonnées.xlsx ;
  l'indication, qui relève d'un jugement clinique, n'est jamais déduite.

Les colonnes de traçabilité (ligne_source, problematique_staff, decision_staff, commentaire)
ne sont pas lues.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import pandas as pd

from aod.data import QUESTIONS, STRATEGIE, _decode, _norm, _strategie, html_to_text

PREMIER_NUMERO = 100     # cas n de la RCP -> case_id 100 + n
ROLE = "rcp"
ORIGINE = "RCP"
NON_RENSEIGNE = "Non renseigné"

RISQUES = {"faible": "Faible", "eleve": "Élevé", "tres_eleve": "Très élevé", "nsp": "Je ne sais pas"}
SPECIALITES = {
    "chirurgieorthopedique": "Orthopédie",
    "chirurgieviscerale": "Viscérale",
    "chirurgievasculaire": "Vasculaire",
    "chirurgieurologique": "Urologie",
    "chirurgieorletmaxillofaciale": "ORL et maxillo-faciale",
}
MEDICAMENTS = {
    "AOD": ["eliquis", "apixaban", "xarelto", "rivaroxaban", "pradaxa", "dabigatran", "lixiana", "edoxaban"],
    "AVK": ["coumadine", "warfarine", "previscan", "fluindione", "sintrom", "acenocoumarol"],
    "Héparine": ["lovenox", "enoxaparine", "innohep", "tinzaparine", "fragmine", "dalteparine",
                 "calciparine", "heparine"],
}
# colonne facultative du fichier -> métadonnée
COLONNES_META = {"sexe": "Sexe", "atc": "ATC", "delais": "Délais", "indication": "Indication",
                 "fonctionrenale": "Fonction rénale", "alr": "ALR"}


def fichier_rcp(data_dir: Path) -> Path | None:
    fichiers = sorted((data_dir / "RCP").glob("*.xlsx"), key=lambda p: p.name)
    fichiers = [p for p in fichiers if not p.name.startswith("~$")]  # fichier verrou d'Excel
    return fichiers[-1] if fichiers else None


def _vide(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""


# --------------------------------------------------------------------------- texte

def texte_vers_html(texte: str) -> str:
    """Texte de la vignette → HTML au gabarit REDCap (liste à puces pour le traitement)."""
    lignes = [l.strip() for l in str(texte).splitlines() if l.strip()]
    blocs, para = [], []
    for ligne in lignes:
        m = re.match(r"(Son traitement comprend\s*:)\s*(.*)", ligne)
        if m and m.group(2):
            para.append(html.escape(m.group(1)))
            blocs.append("<p>" + "<br>".join(para) + "</p>")
            para = []
            items = [i.strip() for i in m.group(2).split(";") if i.strip()]
            blocs.append("<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in items) + "</ul>")
        else:
            para.append(html.escape(ligne))
    if para:
        blocs.append("<p>" + "<br>".join(para) + "</p>")
    return " ".join(blocs)


# --------------------------------------------------------------------------- métadonnées déduites

def _specialite(v) -> str:
    if _vide(v):
        return NON_RENSEIGNE
    return SPECIALITES.get(_norm(v), str(v).strip())


def _sexe(texte: str) -> str:
    mot = texte.split(" ", 1)[0]
    return mot if mot in ("Femme", "Homme") else NON_RENSEIGNE


def _atc(texte: str) -> str:
    m = re.search(r"Son traitement comprend\s*:(.*)", texte)
    traitement = _norm(m.group(1)) if m else ""
    trouves = [classe for classe, noms in MEDICAMENTS.items() if any(n in traitement for n in noms)]
    return trouves[0] if len(trouves) == 1 else NON_RENSEIGNE


def _alr(texte: str) -> str:
    t = texte.lower()
    if re.search(r"rachianesth|péridura|périmédullaire", t):
        return "APM"
    if re.search(r"locorégionale|\bblocs?\b|anesthésie locale", t):
        return "ALR"
    return "Aucune"


def _fonction_renale(texte: str) -> str:
    t = texte.lower()
    if "insuffisance rénale" in t:
        return "IRC"
    for m in re.finditer(r"(?:débit de filtration glomérulaire|clairance)[^0-9]{0,80}?(\d+)\s*ml/min", t):
        if int(m.group(1)) <= 60:
            return "IRC"
    return "Normale"


# --------------------------------------------------------------------------- chargement

def charger_rcp(data_dir: Path, meta_columns: list[str]) -> tuple[list[dict], list[dict]]:
    """Lignes « cas » et « réponses » (format de aod.data.build_dataset) pour les vignettes de la RCP."""
    chemin = fichier_rcp(data_dir)
    if chemin is None:
        return [], []
    df = pd.read_excel(chemin, sheet_name="Vignettes", dtype=object)
    colonnes = {_norm(c): c for c in df.columns}
    cases, answers = [], []
    for _, r in df.iterrows():
        if _vide(r.get("cas")) or _vide(r.get("vignette")):
            continue
        n = int(float(r["cas"]))
        cid = PREMIER_NUMERO + n
        texte = str(r["vignette"]).strip()
        html_ = texte_vers_html(texte)
        deduites = {"Sexe": _sexe(texte), "ATC": _atc(texte), "Délais": "Programmé",
                    "Indication": NON_RENSEIGNE, "Fonction rénale": _fonction_renale(texte),
                    "ALR": _alr(texte)}
        meta = {"Spécialité": _specialite(r.get(colonnes.get("specialite"))), "Origine": ORIGINE}
        for norm, nom in COLONNES_META.items():
            v = r.get(colonnes[norm]) if norm in colonnes else None
            meta[nom] = str(v).strip() if not _vide(v) else deduites[nom]
        cases.append({
            "case_id": cid, "Cas": f"Cas {cid}", "n_local": n,
            **{c: meta.get(c) for c in meta_columns},
            "Urgence immédiate": False,
            "texte": texte, "texte_prompt": html_to_text(html_), "html": html_,
            "projet": f"RCP ({chemin.name})",
        })

        dec = {}
        for q, _ in QUESTIONS:
            brut = r.get(q)
            if q in ("risque_hem", "risque_thromb"):
                dec[q] = None if _vide(brut) else RISQUES.get(str(brut).strip().lower(), str(brut).strip())
            else:
                dec[q] = _decode(q, None if _vide(brut) else str(brut).strip(), {}, n)
        if all(v is None for v in dec.values()):
            continue
        base = {"case_id": cid, "respondant": "RCP", "role": ORIGINE, "role_code": ROLE,
                "experience": None, "confiance": None, "source": "RCP"}
        for q, lab in QUESTIONS:
            if dec[q] is not None:
                answers.append({**base, "question": lab, "valeur": dec[q]})
        if strategie_complete(dec):
            answers.append({**base, "question": STRATEGIE, "valeur": _strategie(dec)})
    return cases, answers


def strategie_complete(dec: dict) -> bool:
    """La RCP a-t-elle tranché chaque élément de la stratégie ?

    Contrairement au questionnaire REDCap, où ces champs sont obligatoires, une cellule vide
    signifie ici « non précisé » : un relais vide n'est pas un « sans relais ».
    """
    if dec.get("interruption") == "Non":
        return True
    if dec.get("interruption") != "Oui" or dec.get("dern_prise") is None or dec.get("relais") is None:
        return False
    return dec["relais"] == "Non" or dec.get("relais_debut") is not None
