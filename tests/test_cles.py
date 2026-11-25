"""Clés nommées et traçabilité de l'auteur : tests sans les données de l'étude."""
import importlib
import json
import sqlite3

import pytest

from aod.cles import SANS_NOM, lire_cles, titulaire
from aod.store import Store


def test_lire_cles_nommees_et_ancien_format():
    assert lire_cles("michael:aaa, alice:bbb") == {"aaa": "michael", "bbb": "alice"}
    # ancien format (clé seule) toujours accepté, attribué à « sans_nom »
    assert lire_cles("aaa,bob:bbb") == {"aaa": SANS_NOM, "bbb": "bob"}
    assert lire_cles(":aaa") == {"aaa": SANS_NOM}
    assert lire_cles("") == {} and lire_cles(" , alice: ,") == {}


def test_titulaire_essaie_chaque_cle_fournie():
    cles = {"aaa": "michael", "bbb": "alice"}
    assert titulaire(["bbb"], cles) == "alice"
    assert titulaire(["mauvaise", "aaa"], cles) == "michael"  # Bearer erroné, X-API-Key valide
    assert titulaire([None, ""], cles) is None
    assert titulaire(["aa"], cles) is None


def test_migration_base_existante(tmp_path):
    """Une base créée avant l'ajout des colonnes d'auteur est complétée sans perte."""
    chemin = tmp_path / "benchmark.sqlite"
    with sqlite3.connect(chemin) as c:
        c.executescript("""
            CREATE TABLE runs (id TEXT PRIMARY KEY, cree_le TEXT NOT NULL, modele TEXT NOT NULL,
                preprompt INTEGER NOT NULL, roles TEXT, min_repondants INTEGER NOT NULL,
                version_donnees TEXT NOT NULL, parametres TEXT NOT NULL, reference TEXT NOT NULL);
            CREATE TABLE reponses (run_id TEXT NOT NULL, cas INTEGER NOT NULL, soumis_le TEXT NOT NULL,
                statut TEXT NOT NULL, sortie TEXT, meta TEXT NOT NULL, grille TEXT NOT NULL,
                PRIMARY KEY (run_id, cas));
        """)
        c.execute("INSERT INTO runs VALUES ('ancien','2026-11-24','m',1,'null',1,'v','{}','{}')")
        c.execute("INSERT INTO reponses VALUES ('ancien',1,'2026-11-24','ok','x','{}',?)", (json.dumps({}),))

    store = Store(chemin)
    assert store.run("ancien")["auteur"] is None
    assert store.reponses("ancien")[0]["soumis_par"] is None

    nouveau = store.creer_run("m", True, None, 1, "v", {}, {}, auteur="alice")
    assert nouveau["auteur"] == "alice"
    store.enregistrer_reponse(nouveau["id"], 1, "ok", "x", {}, {}, soumis_par="bob")
    assert store.reponses(nouveau["id"])[0]["soumis_par"] == "bob"
    Store(chemin)  # réouverture : la migration ne s'applique pas deux fois


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AOD_API_KEYS", "michael:cle-m,cle-ancienne")
    monkeypatch.setenv("AOD_STATE_DIR", str(tmp_path))
    import api
    importlib.reload(api)
    from fastapi.testclient import TestClient
    return TestClient(api.app)


def test_api_cles_nommees(api_client):
    assert api_client.get("/runs", headers={"Authorization": "Bearer cle-m"}).status_code == 200
    assert api_client.get("/runs", headers={"X-API-Key": "cle-ancienne"}).status_code == 200
    assert api_client.get("/runs", headers={"X-API-Key": "michael:cle-m"}).status_code == 401
    # un Bearer erroné n'empêche plus une X-API-Key valide d'être acceptée
    r = api_client.get("/runs", headers={"Authorization": "Bearer cd97fd80ec4a", "X-API-Key": "cle-m"})
    assert r.status_code == 200
    assert api_client.get("/runs").status_code == 401
