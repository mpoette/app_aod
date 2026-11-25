"""Vignettes de la RCP thrombose : tests sur un petit fichier fictif (sans les données de l'étude)."""
import importlib
import json

import pandas as pd
import pytest

from aod.data import STRATEGIE, build_dataset, html_to_text
from aod.rcp import charger_rcp, texte_vers_html
from aod.scoring import construire_reference

SECRET = "Dr Untel, ligne 42 du fichier source"
VIGNETTE = ("Femme de 52 ans ayant un poids de 68 kg pour 1,65 m. Vue en consultation d’anesthésie pour une PTH.\n"
            "Ses antécédents sont une insuffisance rénale chronique.\n"
            "Son traitement comprend : Eliquis 5 mg matin et soir ; atorvastatine 40 mg le soir\n"
            "L’intervention est prévue dans 2 semaines sous rachianesthésie.")
AUTRE = ("Homme de 70 ans. Vu en consultation d’anesthésie.\nSon traitement comprend : Coumadine 3 mg le soir\n"
         "L’intervention est prévue dans 1 semaine sous anesthésie locale.")


def _fichier(data_dir, lignes, **colonnes_en_plus):
    (data_dir / "RCP").mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(lignes, columns=["cas", "specialite", "vignette", "risque_hem", "risque_thromb",
                                       "interruption", "dern_prise", "relais", "relais_debut", "dosage",
                                       "ligne_source", "problematique_staff", "decision_staff", "commentaire"])
    for nom, valeurs in colonnes_en_plus.items():
        df[nom] = valeurs
    df.to_excel(data_dir / "RCP" / "Vignettes_RCP.xlsx", sheet_name="Vignettes", index=False)


LIGNES = [
    # cas, spécialité, vignette, r_hem, r_thromb, interruption, dern_prise, relais, relais_debut, dosage, traçabilité…
    [1, "Chirurgie orthopédique", VIGNETTE, None, None, 1, 3, 0, None, None, 42, SECRET, SECRET, SECRET],
    [2, "Chirurgie vasculaire", AUTRE, "faible", None, 0, None, None, None, 1, 43, SECRET, SECRET, SECRET],
    [3, "Gynécologie", VIGNETTE, None, None, 1, 3, None, None, None, 44, SECRET, SECRET, SECRET],  # relais non précisé
    [4, "Chirurgie viscérale", VIGNETTE, None, None, 1, 5, 1, None, None, 45, SECRET, SECRET, SECRET],  # début non précisé
    [5, "Chirurgie viscérale", VIGNETTE, None, None, 1, 5, 1, 4, None, 46, SECRET, SECRET, SECRET],
]
META = ["Spécialité", "Sexe", "ATC", "Délais", "Indication", "Fonction rénale", "ALR", "Origine"]


def test_texte_au_gabarit_redcap():
    t = html_to_text(texte_vers_html(VIGNETTE))
    assert "Son traitement comprend :\n- Eliquis 5 mg matin et soir\n- atorvastatine 40 mg le soir\n" in t
    assert t.endswith("sous rachianesthésie.")
    assert "<script>" not in texte_vers_html("a <script>")


def test_cas_metadonnees_et_reponses(tmp_path):
    _fichier(tmp_path, LIGNES, Indication=["MTEV", None, None, None, None])
    cases, answers = charger_rcp(tmp_path, META)
    c = {r["case_id"]: r for r in cases}
    assert sorted(c) == [101, 102, 103, 104, 105]
    assert {k: c[101][k] for k in META} == {
        "Spécialité": "Orthopédie", "Sexe": "Femme", "ATC": "AOD", "Délais": "Programmé",
        "Indication": "MTEV", "Fonction rénale": "IRC", "ALR": "APM", "Origine": "RCP"}
    assert (c[102]["Spécialité"], c[102]["ATC"], c[102]["ALR"], c[102]["Indication"]) == \
        ("Vasculaire", "AVK", "ALR", "Non renseigné")  # l'indication n'est jamais déduite

    strat = {a["case_id"]: a["valeur"] for a in answers if a["question"] == STRATEGIE}
    # stratégie retenue seulement si la RCP a tout précisé ; relais vide ≠ « sans relais »
    assert strat == {101: "Arrêt J-3, sans relais", 102: "Poursuite", 105: "Arrêt J-5 + HBPM J-4"}
    q102 = {a["question"]: a["valeur"] for a in answers if a["case_id"] == 102}
    assert q102 == {"Risque hémorragique": "Faible", "Interruption": "Non", "Dosage biologique": "Oui",
                    STRATEGIE: "Poursuite"}
    assert {a["role_code"] for a in answers} == {"rcp"} and all(a["confiance"] is None for a in answers)
    # les colonnes de traçabilité ne sont reprises nulle part
    assert SECRET not in json.dumps([cases, answers], default=str, ensure_ascii=False)


def _donnees(tmp_path):
    pd.DataFrame({"": ["Cas clinique 1"], "Sexe": ["Homme"], "ATC": ["AOD"], "Délais": ["Programmé"],
                  "Indication": ["FA"], "Fonction rénale": ["Normale"], "ALR": ["Aucune"],
                  "Xie": ["Vasculaire"]}).to_excel(tmp_path / "Métadonnées.xlsx", index=False)
    (tmp_path / "preprompt.txt").write_text("Vous êtes anesthésiste-réanimateur.", encoding="utf-8")
    _fichier(tmp_path, LIGNES)
    return tmp_path


def test_fondus_dans_le_jeu_de_donnees(tmp_path):
    ds = build_dataset(_donnees(tmp_path))
    assert ds.cases.set_index("case_id")["Origine"].to_dict() == {
        1: "Étude", 101: "RCP", 102: "RCP", 103: "RCP", 104: "RCP", 105: "RCP"}
    ref = construire_reference(ds.cases, ds.answers, ["rcp"])
    assert sorted(k for k, v in ref.items() if v["evaluable"]) == ["101", "102", "105"]
    assert construire_reference(ds.cases, ds.answers, ["expert_clot"])["101"]["evaluable"] is False


@pytest.fixture()
def api_rcp(tmp_path, monkeypatch):
    monkeypatch.setenv("AOD_API_KEYS", "testeur:cle")
    (tmp_path / "Data").mkdir()
    monkeypatch.setenv("AOD_DATA_DIR", str(_donnees(tmp_path / "Data")))
    monkeypatch.setenv("AOD_STATE_DIR", str(tmp_path / "state"))
    import api
    importlib.reload(api)
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    c.headers["Authorization"] = "Bearer cle"
    return c


def test_api_rcp(api_rcp):
    v = api_rcp.get("/vignettes", params={"origine": "RCP"}).json()
    assert [x["cas"] for x in v["vignettes"]] == [101, 102, 103, 104, 105]
    p = api_rcp.get("/vignettes/101").json()
    assert "- Eliquis 5 mg matin et soir" in p["prompt"] and SECRET not in json.dumps(p, ensure_ascii=False)

    run = api_rcp.post("/runs", json={"modele": "x", "roles": ["rcp"]}).json()
    assert run["cas_evaluables"] == [101, 102, 105]
    g = api_rcp.post(f"/runs/{run['id']}/reponses/102", json={"reponse": {
        "risque_hemorragique": "faible", "risque_thrombotique": "faible", "interruption": "non",
        "dosage": "oui", "niveau_confiance": 8}}).json()
    assert g["strategie"]["statut"] == "juste"
    assert g["questions"]["risque_thrombotique"]["statut"] == "non_evaluable"  # la RCP ne l'a pas donné
    bilan = api_rcp.get(f"/runs/{run['id']}").json()["bilan"]
    assert bilan["par_origine"] == {"RCP": {"justes": 1, "repondus": 1, "taux": 1.0}}
