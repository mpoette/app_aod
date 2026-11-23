"""Tests de la notation, sur des références construites à la main (les données réelles ne sont pas versionnées)."""
import json

import pytest

from aod.scoring import extraire_json, noter


def ref_programme(**questions):
    """Référence d'un cas programmé, avec par défaut une majorité nette pour chaque question."""
    q = {
        "Stratégie globale": ["Arrêt J-5 + HBPM J-3"],
        "Risque hémorragique": ["Élevé"],
        "Risque thrombotique": ["Élevé"],
        "Interruption": ["Oui"],
        "Dernière prise": ["J-5"],
        "Relais héparine": ["HBPM"],
        "Début du relais": ["J-3"],
        "Dosage biologique": ["Non"],
    }
    q.update(questions)
    return {"n_repondants": 4, "urgence_immediate": False, "evaluable": True,
            "questions": {k: {"majoritaires": v, "n": 4} for k, v in q.items() if v is not None}}


BONNE = {"risque_hemorragique": "eleve", "risque_thrombotique": "eleve", "interruption": "oui",
         "derniere_prise": 5, "relais": "hbpm", "debut_relais": 3, "reversion": None,
         "dosage": "non", "niveau_confiance": 8}


def test_reponse_parfaite():
    g = noter(ref_programme(), sortie=json.dumps(BONNE))
    assert g["format_valide"]
    assert g["strategie"] == {"reponse": "Arrêt J-5 + HBPM J-3", "reference": ["Arrêt J-5 + HBPM J-3"],
                              "statut": "juste"}
    assert g["score"] == {"questions_justes": 7, "questions_evaluees": 7}
    assert g["questions"]["reversion"]["statut"] == "non_evaluable"
    assert g["questions"]["derniere_prise"]["reference"] == [5]


def test_egalite_toute_modalite_ex_aequo_valide():
    ref = ref_programme(**{"Risque hémorragique": ["Faible", "Élevé"],
                           "Stratégie globale": ["Arrêt J-5 + HBPM J-2", "Arrêt J-5 + HBPM J-3"]})
    for code in ("faible", "eleve"):
        g = noter(ref, reponse={**BONNE, "risque_hemorragique": code})
        assert g["questions"]["risque_hemorragique"]["statut"] == "juste"
    assert noter(ref, reponse={**BONNE, "risque_hemorragique": "tres_eleve"})[
        "questions"]["risque_hemorragique"]["statut"] == "faux"
    assert noter(ref, reponse=BONNE)["strategie"]["statut"] == "juste"


def test_sortie_avec_bloc_markdown():
    g = noter(ref_programme(), sortie="Voici :\n```json\n" + json.dumps(BONNE) + "\n```")
    assert g["format_valide"] and g["strategie"]["statut"] == "juste"


def test_json_illisible():
    g = noter(ref_programme(), sortie="Je recommande d'arrêter à J-5.")
    assert not g["format_valide"]
    assert g["strategie"]["statut"] == "faux"
    assert g["score"]["questions_justes"] == 0


def test_valeur_hors_liste_est_une_erreur_de_format():
    g = noter(ref_programme(), reponse={**BONNE, "relais": "lovenox"})
    assert not g["format_valide"]
    assert any(e.startswith("relais") for e in g["erreurs"])
    assert g["questions"]["relais"]["statut"] == "faux"


def test_question_conditionnelle_non_applicable():
    """Le LLM poursuit le traitement : « dernière prise » ne s'applique pas à lui."""
    rep = {**BONNE, "interruption": "non", "derniere_prise": None, "relais": None, "debut_relais": None}
    g = noter(ref_programme(), reponse=rep)
    assert g["format_valide"]
    assert g["strategie"]["reponse"] == "Poursuite" and g["strategie"]["statut"] == "faux"
    assert g["questions"]["interruption"]["statut"] == "faux"
    for cle in ("derniere_prise", "relais", "debut_relais"):
        assert g["questions"][cle]["statut"] == "non_applicable"


def test_champ_requis_manquant():
    rep = {k: v for k, v in BONNE.items() if k != "derniere_prise"}
    g = noter(ref_programme(), reponse=rep)
    assert not g["format_valide"]
    assert g["questions"]["derniere_prise"]["statut"] == "faux"
    assert g["strategie"]["reponse"] == "Arrêt ? + HBPM J-3"


def test_urgence_immediate():
    ref = {"n_repondants": 3, "urgence_immediate": True, "evaluable": True, "questions": {
        "Stratégie globale": {"majoritaires": ["Réversion"], "n": 3},
        "Réversion": {"majoritaires": ["Oui"], "n": 3},
        "Dosage biologique": {"majoritaires": ["Oui"], "n": 3},
        "Risque hémorragique": {"majoritaires": ["Très élevé"], "n": 3},
        "Risque thrombotique": {"majoritaires": ["Élevé"], "n": 3}}}
    rep = {"risque_hemorragique": "tres_eleve", "risque_thrombotique": "eleve", "interruption": None,
           "derniere_prise": None, "relais": None, "debut_relais": None, "reversion": "oui",
           "dosage": "oui", "niveau_confiance": 9}
    g = noter(ref, reponse=rep)
    assert g["format_valide"] and not g["avertissements"]
    assert g["strategie"]["statut"] == "juste"
    assert g["score"] == {"questions_justes": 4, "questions_evaluees": 4}
    # le LLM « programme » un arrêt alors qu'il faut opérer immédiatement
    g2 = noter(ref, reponse={**rep, "reversion": None, "interruption": "oui", "derniere_prise": 1, "relais": "non"})
    assert not g2["format_valide"] and g2["strategie"]["statut"] == "faux" and g2["avertissements"]


def test_cas_sans_reference():
    ref = {"n_repondants": 0, "urgence_immediate": False, "evaluable": False, "questions": {}}
    g = noter(ref, reponse=BONNE)
    assert not g["evaluable"]
    assert g["strategie"]["statut"] == "non_evaluable"
    assert g["score"]["questions_evaluees"] == 0


@pytest.mark.parametrize("valeur", [True, -1, 2.5, "cinq"])
def test_jour_invalide(valeur):
    g = noter(ref_programme(), reponse={**BONNE, "derniere_prise": valeur})
    assert not g["format_valide"]


def test_extraire_json_rejette_un_tableau():
    with pytest.raises(ValueError):
        extraire_json("[1, 2]")
