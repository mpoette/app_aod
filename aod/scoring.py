"""Notation des réponses de LLM par rapport à la réponse majoritaire des répondants humains.

Format de réponse attendu : l'objet JSON décrit dans Data/preprompt.txt
(risque_hemorragique, risque_thrombotique, interruption, derniere_prise, relais,
debut_relais, reversion, dosage, niveau_confiance).

Règles :
- une réponse est juste si elle fait partie des modalités majoritaires (en cas d'égalité,
  n'importe laquelle des modalités ex æquo valide) ;
- une question sans référence humaine est « non_evaluable » ;
- une question conditionnelle (dernière prise, relais, début du relais) qui ne s'applique pas
  à la réponse du LLM (ex. pas d'interruption) est « non_applicable » : c'est la stratégie
  globale qui porte alors le verdict ;
- une sortie illisible ou une valeur hors liste est une erreur de format, distincte d'une
  erreur clinique.
"""
from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

from aod.data import STRATEGIE, _strategie, summarize

# Clé JSON du LLM -> (libellé de question interne, préfixe REDCap, codes autorisés -> libellés)
OUI_NON = {"oui": "Oui", "non": "Non"}
CHAMPS: dict[str, tuple[str, str, dict[str, str] | None]] = {
    "risque_hemorragique": ("Risque hémorragique", "risque_hem",
                            {"faible": "Faible", "eleve": "Élevé", "tres_eleve": "Très élevé",
                             "nsp": "Je ne sais pas"}),
    "risque_thrombotique": ("Risque thrombotique", "risque_thromb",
                            {"faible": "Faible", "eleve": "Élevé", "nsp": "Je ne sais pas"}),
    "interruption": ("Interruption", "interruption", OUI_NON),
    "derniere_prise": ("Dernière prise", "dern_prise", None),   # entier X de J-X
    "relais": ("Relais héparine", "relais", {"non": "Non", "hbpm": "HBPM", "hnf": "HNF"}),
    "debut_relais": ("Début du relais", "relais_debut", None),  # entier X de J-X
    "reversion": ("Réversion", "reversion", OUI_NON),
    "dosage": ("Dosage biologique", "dosage", OUI_NON),
}
CONDITIONNELLES = {"derniere_prise", "relais", "debut_relais"}
JOURS = {"derniere_prise", "debut_relais"}


# --------------------------------------------------------------------------- conversions

def _jour_label(x: int) -> str:
    return "J0" if x == 0 else f"J-{x}"


def _label_vers_code(cle: str, label: str) -> Any:
    """Libellé interne (« Élevé », « J-5 ») -> code du format LLM (« eleve », 5)."""
    if cle in JOURS:
        m = re.fullmatch(r"J-?(\d+)", str(label))
        return int(m.group(1)) if m else label
    choix = CHAMPS[cle][2] or {}
    inverse = {v: k for k, v in choix.items()}
    return inverse.get(label, label)


# --------------------------------------------------------------------------- lecture de la sortie

def extraire_json(sortie: str) -> dict:
    """Extrait l'objet JSON d'une sortie texte de LLM (tolère un bloc ```json … ```)."""
    texte = sortie.strip()
    texte = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte)
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut == -1 or fin < debut:
        raise ValueError("aucun objet JSON trouvé")
    obj = json.loads(texte[debut:fin + 1])
    if not isinstance(obj, dict):
        raise ValueError("le JSON n'est pas un objet")
    return obj


def normaliser(reponse: dict) -> tuple[dict[str, Any], list[str]]:
    """Valide les valeurs (codes autorisés, entiers ≥ 0). Renvoie (valeurs normalisées, erreurs).

    Une valeur invalide est remplacée par None et signalée dans les erreurs.
    """
    valeurs: dict[str, Any] = {}
    erreurs: list[str] = []
    for cle, (_, _, choix) in CHAMPS.items():
        v = reponse.get(cle)
        if v is None:
            valeurs[cle] = None
            continue
        if cle in JOURS:
            if isinstance(v, bool) or not (isinstance(v, int) or (isinstance(v, str) and v.strip().isdigit())):
                erreurs.append(f"{cle} : entier attendu, reçu {v!r}")
                valeurs[cle] = None
                continue
            v = int(v)
            if v < 0:
                erreurs.append(f"{cle} : valeur négative {v}")
                valeurs[cle] = None
                continue
            valeurs[cle] = v
        else:
            code = str(v).strip().lower() if isinstance(v, str) else None
            if code not in (choix or {}):
                erreurs.append(f"{cle} : valeur {v!r} hors liste {sorted(choix or {})}")
                valeurs[cle] = None
                continue
            valeurs[cle] = code
    conf = reponse.get("niveau_confiance")
    if conf is not None and not (isinstance(conf, int) and not isinstance(conf, bool) and 0 <= conf <= 10):
        erreurs.append(f"niveau_confiance : entier 0-10 attendu, reçu {conf!r}")
        conf = None
    valeurs["niveau_confiance"] = conf
    return valeurs, erreurs


def _verifier_coherence(valeurs: dict, urgence_immediate: bool) -> tuple[list[str], list[str]]:
    """Champs requis selon la situation (erreurs) et champs renseignés à tort (avertissements)."""
    erreurs, avert = [], []
    requis = ["risque_hemorragique", "risque_thrombotique", "dosage"]
    if urgence_immediate:
        requis.append("reversion")
        for c in ("interruption", "derniere_prise", "relais", "debut_relais"):
            if valeurs.get(c) is not None:
                avert.append(f"{c} renseigné alors que la situation est une urgence immédiate")
    else:
        requis.append("interruption")
        if valeurs.get("reversion") is not None:
            avert.append("reversion renseigné hors urgence immédiate")
        if valeurs.get("interruption") == "oui":
            requis += ["derniere_prise", "relais"]
            if valeurs.get("relais") in ("hbpm", "hnf"):
                requis.append("debut_relais")
    for c in requis:
        if valeurs.get(c) is None:
            erreurs.append(f"{c} : champ requis manquant ou invalide")
    return erreurs, avert


# --------------------------------------------------------------------------- référence

def construire_reference(cases: pd.DataFrame, answers: pd.DataFrame,
                         roles: list[str] | None = None, min_repondants: int = 1) -> dict[str, dict]:
    """Référence par cas : modalités majoritaires par question, calculées sur les rôles choisis.

    `roles` : codes REDCap (interne_ar, mar_vasculaire, mar_autre, expert_clot, expert_gihp)
    et « rcp » (décision de la RCP thrombose) ;
    None = tous les répondants. Clés du dictionnaire : identifiant du cas sous forme de texte
    (pour être sérialisable en JSON tel quel).
    """
    a = answers if not roles else answers[answers["role_code"].isin(roles)]
    summ = summarize(a)
    n_rep = a.groupby("case_id")["respondant"].nunique()
    ref: dict[str, dict] = {}
    for _, c in cases.iterrows():
        cid = int(c["case_id"])
        s = summ[summ["case_id"] == cid]
        questions = {r["question"]: {"majoritaires": list(r["majoritaires"]), "n": int(r["n"])}
                     for _, r in s.iterrows()}
        n = int(n_rep.get(cid, 0))
        ref[str(cid)] = {
            "n_repondants": n,
            "urgence_immediate": bool(c["Urgence immédiate"]),
            "evaluable": STRATEGIE in questions and n >= min_repondants,
            "questions": questions,
        }
    return ref


# --------------------------------------------------------------------------- notation

def noter(ref_cas: dict, sortie: str | None = None, reponse: dict | None = None) -> dict:
    """Grille de correction d'une réponse de LLM pour un cas.

    `sortie` : texte brut renvoyé par le LLM (le JSON y est extrait) ; ou `reponse` : objet déjà décodé.
    """
    erreurs: list[str] = []
    avert: list[str] = []
    if reponse is None:
        try:
            reponse = extraire_json(sortie or "")
        except (ValueError, json.JSONDecodeError) as e:
            erreurs.append(f"JSON illisible : {e}")
            reponse = {}
    valeurs, err = normaliser(reponse)
    erreurs += err
    if reponse:
        err, avert = _verifier_coherence(valeurs, ref_cas["urgence_immediate"])
        deja = {e.split(" :")[0] for e in erreurs}  # champ déjà signalé comme invalide
        erreurs += [e for e in err if e.split(" :")[0] not in deja]

    # stratégie globale, construite comme pour les humains
    internes = {}
    for cle, (label, prefixe, choix) in CHAMPS.items():
        v = valeurs.get(cle)
        internes[prefixe] = None if v is None else (_jour_label(v) if cle in JOURS else (choix or {})[v])
    strat = _strategie(internes)
    ref_q = ref_cas["questions"]
    ref_strat = ref_q.get(STRATEGIE, {}).get("majoritaires", [])
    grille_strat = {
        "reponse": strat,
        "reference": ref_strat,
        "statut": "non_evaluable" if not ref_strat else ("juste" if strat in ref_strat else "faux"),
    }

    questions = {}
    for cle, (label, prefixe, _) in CHAMPS.items():
        v = valeurs.get(cle)
        ref = ref_q.get(label)
        reference = [_label_vers_code(cle, m) for m in ref["majoritaires"]] if ref else []
        if not ref:
            statut = "non_evaluable"
        elif v is None and cle in CONDITIONNELLES and not _declenche(cle, valeurs):
            statut = "non_applicable"
        elif v is None:
            statut = "faux"
        else:
            statut = "juste" if v in reference else "faux"
        questions[cle] = {"libelle": label, "reponse": v, "reference": reference,
                          "n_reference": ref["n"] if ref else 0, "statut": statut}

    evaluees = [q for q in questions.values() if q["statut"] in ("juste", "faux")]
    return {
        "evaluable": ref_cas["evaluable"],
        "format_valide": not erreurs,
        "erreurs": erreurs,
        "avertissements": avert,
        "strategie": grille_strat,
        "questions": questions,
        "niveau_confiance": valeurs.get("niveau_confiance"),
        "score": {
            "questions_justes": sum(q["statut"] == "juste" for q in evaluees),
            "questions_evaluees": len(evaluees),
        },
    }


def _declenche(cle: str, valeurs: dict) -> bool:
    """La question conditionnelle s'applique-t-elle compte tenu des réponses du LLM ?"""
    if cle in ("derniere_prise", "relais"):
        return valeurs.get("interruption") == "oui"
    if cle == "debut_relais":
        return valeurs.get("relais") in ("hbpm", "hnf")
    return True


# --------------------------------------------------------------------------- bilan d'un essai

def bilan(grilles: list[dict], reference: dict[str, dict], cases: pd.DataFrame) -> dict:
    """Synthèse d'un essai : taux de stratégies justes, par question, par spécialité, par origine."""
    n_eval = sum(r["evaluable"] for r in reference.values())
    # une erreur technique (réseau, clé…) n'est pas une réponse du modèle : elle est exclue du calcul
    # (relancer le cas) ; un refus, lui, compte comme une stratégie non trouvée
    notees = [g for g in grilles if g["statut"] != "erreur" and g["grille"]["evaluable"]]
    refus = sum(g["statut"] == "refus" for g in grilles)
    erreurs_api = sum(g["statut"] == "erreur" for g in grilles)
    format_ko = sum(not g["grille"]["format_valide"] for g in notees if g["statut"] == "ok")
    strat_justes = sum(g["grille"]["strategie"]["statut"] == "juste" for g in notees)

    par_question = {}
    for cle, (label, _, _) in CHAMPS.items():
        st = [g["grille"]["questions"][cle]["statut"] for g in notees]
        n = sum(s in ("juste", "faux") for s in st)
        par_question[cle] = {"libelle": label, "justes": st.count("juste"), "evaluees": n,
                             "taux": round(st.count("juste") / n, 3) if n else None}

    def par(colonne: str) -> dict[str, dict]:
        groupe = cases.set_index("case_id")[colonne].to_dict() if colonne in cases else {}
        out: dict[str, dict] = {}
        for g in notees:
            d = out.setdefault(groupe.get(int(g["cas"]), "?"), {"justes": 0, "repondus": 0})
            d["repondus"] += 1
            d["justes"] += g["grille"]["strategie"]["statut"] == "juste"
        for d in out.values():
            d["taux"] = round(d["justes"] / d["repondus"], 3) if d["repondus"] else None
        return out

    return {
        "cas_evaluables": n_eval,
        "cas_repondus": len(notees),
        "strategie": {"justes": strat_justes, "taux": round(strat_justes / len(notees), 3) if notees else None},
        "format_invalide": format_ko,
        "refus": refus,
        "erreurs_api": erreurs_api,
        "par_question": par_question,
        "par_specialite": par("Spécialité"),
        "par_origine": par("Origine"),
    }
