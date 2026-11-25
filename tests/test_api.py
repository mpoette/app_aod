"""Test de bout en bout de l'API, sur les données réelles si elles sont présentes dans Data/ (sinon ignoré)."""
import os
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent.parent / "Data"
pytestmark = pytest.mark.skipif(not any(DATA.glob("*.xlsx")), reason="pas de données dans Data/")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AOD_API_KEYS", "testeur:cle-test")
    monkeypatch.setenv("AOD_STATE_DIR", str(tmp_path))
    import importlib

    import api
    importlib.reload(api)  # prend en compte AOD_STATE_DIR
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    c.headers["Authorization"] = "Bearer cle-test"
    return c


def test_authentification(client):
    assert client.get("/health").status_code == 200
    assert client.get("/vignettes", headers={"Authorization": "Bearer mauvaise"}).status_code == 401
    assert client.get("/vignettes", headers={"X-API-Key": "cle-test", "Authorization": ""}).status_code == 200


def test_prompt_avec_et_sans_preprompt(client):
    avec = client.get("/vignettes/1").json()
    sans = client.get("/vignettes/1", params={"preprompt": "false"}).json()
    assert avec["prompt"].endswith(sans["prompt"])
    assert "anesthésiste-réanimateur" in avec["prompt"]
    assert "- Eliquis 5 mg matin et soir" in sans["prompt"]


def test_essai_complet_avec_reponses_majoritaires(client):
    """Un « LLM » qui donne toujours une réponse majoritaire doit obtenir 100 % de stratégies justes."""
    from aod.scoring import _label_vers_code, CHAMPS

    run = client.post("/runs", json={"modele": "oracle", "preprompt": True}).json()
    assert run["cas_evaluables"]
    assert run["auteur"] == "testeur"
    import api
    ref = api.STORE.run(run["id"])["reference"]
    for cas in run["cas_evaluables"]:
        q = ref[str(cas)]["questions"]
        rep = {cle: (_label_vers_code(cle, q[label]["majoritaires"][0]) if label in q else None)
               for cle, (label, _, _) in CHAMPS.items()}
        strat = q["Stratégie globale"]["majoritaires"][0]
        # la stratégie majoritaire peut différer de la combinaison des majorités question par question :
        # on reconstruit la réponse à partir de la stratégie elle-même
        if strat in ("Réversion", "Pas de réversion"):
            rep.update(interruption=None, derniere_prise=None, relais=None, debut_relais=None,
                       reversion="oui" if strat == "Réversion" else "non")
        elif strat == "Poursuite":
            rep.update(interruption="non", derniere_prise=None, relais=None, debut_relais=None, reversion=None)
        else:
            import re
            m = re.match(r"Arrêt J-(\d+)(?: \+ (HBPM|HNF) J-(\d+))?", strat)
            rep.update(interruption="oui", derniere_prise=int(m.group(1)), reversion=None,
                       relais=(m.group(2) or "non").lower(),
                       debut_relais=int(m.group(3)) if m.group(3) else None)
        rep["niveau_confiance"] = 7
        g = client.post(f"/runs/{run['id']}/reponses/{cas}", json={"reponse": rep}).json()
        assert g["strategie"]["statut"] == "juste", (cas, g)
        assert g["soumis_par"] == "testeur"

    bilan = client.get(f"/runs/{run['id']}").json()["bilan"]
    assert bilan["strategie"]["taux"] == 1.0
    assert bilan["cas_repondus"] == len(run["cas_evaluables"])


def test_refus_compte_comme_echec(client):
    run = client.post("/runs", json={"modele": "x", "roles": ["expert_clot"]}).json()
    cas = run["cas_evaluables"][0]
    g = client.post(f"/runs/{run['id']}/reponses/{cas}", json={"statut": "refus"}).json()
    assert g["strategie"]["statut"] == "faux" and not g["format_valide"]
    bilan = client.get(f"/runs/{run['id']}").json()["bilan"]
    assert bilan["refus"] == 1 and bilan["strategie"]["justes"] == 0 and bilan["cas_repondus"] == 1
    # une erreur technique est exclue du calcul
    client.post(f"/runs/{run['id']}/reponses/{run['cas_evaluables'][1]}", json={"statut": "erreur"})
    assert client.get(f"/runs/{run['id']}").json()["bilan"]["cas_repondus"] == 1


def test_roles_inconnus(client):
    assert client.post("/runs", json={"modele": "x", "roles": ["chirurgien"]}).status_code == 422
