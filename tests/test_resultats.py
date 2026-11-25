"""Agrégation des essais de LLM pour l'onglet « Résultats LLM » de l'explorateur."""
from aod import resultats
from aod.data import STRATEGIE
from aod.scoring import noter
from aod.store import Store

REF = {
    "1": {"n_repondants": 3, "urgence_immediate": False, "evaluable": True, "questions": {
        STRATEGIE: {"majoritaires": ["Poursuite"], "n": 3},
        "Interruption": {"majoritaires": ["Non"], "n": 3},
        "Risque hémorragique": {"majoritaires": ["Faible"], "n": 3}}},
    "2": {"n_repondants": 3, "urgence_immediate": False, "evaluable": True, "questions": {
        STRATEGIE: {"majoritaires": ["Arrêt J-3, sans relais"], "n": 3},
        "Interruption": {"majoritaires": ["Oui"], "n": 3}}},
    "3": {"n_repondants": 0, "urgence_immediate": False, "evaluable": False, "questions": {}},
}
POURSUITE = {"risque_hemorragique": "eleve", "risque_thrombotique": "faible", "interruption": "non", "dosage": "non"}
ARRET_J3 = {"risque_hemorragique": "faible", "risque_thrombotique": "faible", "interruption": "oui",
            "derniere_prise": 3, "relais": "non", "dosage": "non"}


def _essai(store, modele, reponses, auteur="michael"):
    run = store.creer_run(modele, True, None, 1, "v1", {}, REF, auteur=auteur)
    for cas, (statut, rep) in reponses.items():
        grille = noter(REF[str(cas)], reponse=rep) if statut == "ok" else noter(REF[str(cas)], sortie="")
        store.enregistrer_reponse(run["id"], cas, statut, None, {}, grille)
    return run["id"]


def test_base_absente(tmp_path):
    essais, cellules = resultats.charger(tmp_path / "absente.sqlite")
    assert essais.empty and cellules.empty
    assert not (tmp_path / "absente.sqlite").exists()  # la lecture ne crée pas de base


def test_agregation(tmp_path):
    db = tmp_path / "benchmark.sqlite"
    store = Store(db)
    a = _essai(store, "opus", {1: ("ok", POURSUITE), 2: ("ok", ARRET_J3), 3: ("ok", POURSUITE)})
    b = _essai(store, "opus", {1: ("ok", ARRET_J3), 2: ("erreur", None)}, auteur=None)
    c = _essai(store, "haiku", {1: ("refus", None), 2: ("ok", ARRET_J3)})
    essais, cellules = resultats.charger(db)

    e = essais.set_index("id")
    # cas 3 non évaluable, erreur exclue, refus compté faux
    assert (e.loc[a, "justes"], e.loc[a, "repondus"]) == (2, 2)
    assert (e.loc[a, "questions_justes"], e.loc[a, "questions_notees"]) == (2, 3)  # interruption ×2 juste, risque hém. faux au cas 1
    assert (e.loc[c, "refus"], e.loc[a, "refus"]) == (1, 0)
    assert (e.loc[b, "justes"], e.loc[b, "repondus"], e.loc[b, "auteur"]) == (0, 1, "inconnu")
    assert (e.loc[c, "justes"], e.loc[c, "repondus"]) == (1, 2)

    det = resultats.agreger(cellules, essais, [a, b], "question").set_index(["case_id", "question"])
    s1 = det.loc[(1, STRATEGIE)]
    assert (s1["justes"], s1["notees"], s1["taux"]) == (1, 2, 0.5)
    assert s1["reponses"] == "Arrêt J-3, sans relais ; Poursuite" and s1["reference"] == "Poursuite"
    assert det.loc[(1, "Risque hémorragique"), "reponses"] == "Faible ; Élevé"
    assert det.loc[(2, STRATEGIE), "notees"] == 1  # l'erreur de l'essai b n'est pas comptée
    assert (3, STRATEGIE) not in det.index
    assert ("Dernière prise" not in det.loc[1].index)  # non évaluée : case vide

    # décocher l'essai b
    assert resultats.taux_global(cellules, [a]) == (2, 2)
    assert resultats.taux_global(cellules, [a, b]) == (2, 3)

    comp = resultats.agreger(cellules, essais, [a, b, c], "modele").set_index(["case_id", "modele"])
    assert comp.loc[(1, "haiku"), "reponses"] == "(refus)" and comp.loc[(1, "haiku"), "taux"] == 0
    assert comp.loc[(1, "opus"), "taux"] == 0.5 and comp.loc[(2, "haiku"), "taux"] == 1


def test_marque():
    assert [resultats.marque(m) for m in ["claude-opus-5-5", "Gemini-3-Pro", "gpt-5.1", "o3-mini",
                                          "mistral-large-2", "deepseek-r2", "openai/gpt-5", "mon-modele"]] == \
        ["Anthropic", "Google", "OpenAI", "OpenAI", "Mistral", "DeepSeek", "OpenAI", "Autre"]
