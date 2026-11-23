"""Stockage des essais de benchmark (SQLite, un fichier).

Un « run » (essai) = un modèle évalué avec des paramètres donnés. La référence (modalités
majoritaires) est figée au moment de la création de l'essai : si de nouvelles réponses
humaines arrivent ensuite, les résultats de l'essai restent comparables et reproductibles.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    cree_le         TEXT NOT NULL,
    modele          TEXT NOT NULL,
    preprompt       INTEGER NOT NULL,
    roles           TEXT,              -- JSON : liste de codes de rôle, ou null = tous
    min_repondants  INTEGER NOT NULL,
    version_donnees TEXT NOT NULL,     -- empreinte du dossier Data/
    parametres      TEXT NOT NULL,     -- JSON libre (effort, max_tokens, notes…)
    reference       TEXT NOT NULL      -- JSON : référence figée par cas
);
CREATE TABLE IF NOT EXISTS reponses (
    run_id      TEXT NOT NULL REFERENCES runs(id),
    cas         INTEGER NOT NULL,
    soumis_le   TEXT NOT NULL,
    statut      TEXT NOT NULL,         -- ok | refus | erreur
    sortie      TEXT,                  -- texte brut renvoyé par le LLM
    meta        TEXT NOT NULL,         -- JSON libre (tokens, durée, modèle servi…)
    grille      TEXT NOT NULL,         -- JSON : grille de correction
    PRIMARY KEY (run_id, cas)
);
"""


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    # ------------------------------------------------------------------ essais
    def creer_run(self, modele: str, preprompt: bool, roles: list[str] | None, min_repondants: int,
                  version_donnees: str, parametres: dict, reference: dict) -> dict:
        run_id = uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, _maintenant(), modele, int(preprompt), json.dumps(roles), min_repondants,
                 version_donnees, json.dumps(parametres, ensure_ascii=False),
                 json.dumps(reference, ensure_ascii=False)),
            )
        return self.run(run_id)

    def run(self, run_id: str, avec_reference: bool = True) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if r is None:
            return None
        d = {
            "id": r["id"], "cree_le": r["cree_le"], "modele": r["modele"],
            "preprompt": bool(r["preprompt"]), "roles": json.loads(r["roles"]),
            "min_repondants": r["min_repondants"], "version_donnees": r["version_donnees"],
            "parametres": json.loads(r["parametres"]),
        }
        if avec_reference:
            d["reference"] = json.loads(r["reference"])
        return d

    def runs(self) -> list[dict]:
        with self._conn() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM runs ORDER BY cree_le DESC")]
        return [self.run(i, avec_reference=False) for i in ids]

    # ------------------------------------------------------------------ réponses
    def enregistrer_reponse(self, run_id: str, cas: int, statut: str, sortie: str | None,
                            meta: dict, grille: dict) -> None:
        """Une seule réponse par cas et par essai : une nouvelle soumission remplace la précédente."""
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO reponses VALUES (?,?,?,?,?,?,?)",
                (run_id, cas, _maintenant(), statut, sortie,
                 json.dumps(meta, ensure_ascii=False), json.dumps(grille, ensure_ascii=False)),
            )

    def reponses(self, run_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM reponses WHERE run_id = ? ORDER BY cas", (run_id,)).fetchall()
        return [{
            "cas": r["cas"], "soumis_le": r["soumis_le"], "statut": r["statut"], "sortie": r["sortie"],
            "meta": json.loads(r["meta"]), "grille": json.loads(r["grille"]),
        } for r in rows]
